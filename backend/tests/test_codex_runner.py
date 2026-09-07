from __future__ import annotations

import json

import pytest

from codex_runner import runner


def test_analysis_prompt_embeds_dossier_without_requiring_file_access() -> None:
    payload = runner.RunRequest(
        question="What is the evidence marker?",
        instructions="Use only the supplied evidence.",
        dossier={"marker": "synthetic-evidence-42"},
        schema={"type": "object"},
        model="test-model",
    )

    prompt = runner._analysis_prompt(payload)

    assert '"marker":"synthetic-evidence-42"' in prompt
    assert "dossier.json" not in prompt


def test_codex_failure_detail_reads_json_error_from_stdout() -> None:
    api_error = json.dumps(
        {
            "error": {
                "message": "Invalid schema for response_format: additionalProperties is required"
            }
        }
    )
    stdout = (
        json.dumps({"type": "error", "message": api_error}).encode()
        + b"\n"
        + json.dumps({"type": "turn.failed", "error": {"message": api_error}}).encode()
    )

    assert runner._codex_failure_detail(stdout, b"") == (
        "Invalid schema for response_format: additionalProperties is required"
    )


def test_device_login_output_extracts_url_and_one_time_code() -> None:
    output = runner._clean_login_output(
        "\x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\r\n"
        "Enter this one-time code\r\n\x1b[94mLRKS-QSOA5\x1b[0m\r\n"
    )

    url_match = runner.DEVICE_URL.search(output)
    code_match = runner.DEVICE_CODE.search(output)
    assert url_match and url_match.group(0) == "https://auth.openai.com/codex/device"
    assert code_match and code_match.group(0) == "LRKS-QSOA5"


@pytest.mark.asyncio
async def test_device_login_session_reaches_authenticated_state(monkeypatch) -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.lines = [
                b"https://auth.openai.com/codex/device\n",
                b"LRKS-QSOA5\n",
            ]

        async def readline(self) -> bytes:
            return self.lines.pop(0) if self.lines else b""

    class FakeProcess:
        stdout = FakeStream()

        async def wait(self) -> int:
            return 0

    async def fake_subprocess(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeProcess()

    async def authenticated() -> bool:
        return True

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(runner, "codex_authenticated", authenticated)
    session = runner.DeviceLoginSession(id="test")

    await runner._run_device_login(session)

    assert session.status == "authenticated"
    assert session.verification_url == "https://auth.openai.com/codex/device"
    assert session.user_code == "LRKS-QSOA5"
    assert session.message == "Codex CLI is connected to ChatGPT."


@pytest.mark.asyncio
async def test_codex_models_ignores_notifications_and_returns_visible_models(monkeypatch) -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.lines = [
                b'{"id":0,"result":{"userAgent":"test"}}\n',
                b'{"method":"configWarning","params":{"summary":"warning"}}\n',
                b'{"method":"remoteControl/status/changed","params":{"status":"disabled"}}\n',
                b'{"id":1,"result":{"data":['
                b'{"id":"gpt-visible","model":"gpt-visible","hidden":false},'
                b'{"id":"gpt-hidden","model":"gpt-hidden","hidden":true}]}}\n',
            ]

        async def readline(self) -> bytes:
            return self.lines.pop(0) if self.lines else b""

    class FakeWriter:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        def write(self, value: bytes) -> None:
            self.messages.append(__import__("json").loads(value))

        async def drain(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeWriter()
            self.stdout = FakeStream()
            self.stderr = FakeStream()
            self.returncode: int | None = None

        def terminate(self) -> None:
            self.returncode = -15

        async def wait(self) -> int:
            return self.returncode or 0

    process = FakeProcess()

    async def fake_subprocess(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return process

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_subprocess)

    assert await runner.codex_models() == ["gpt-visible"]
    assert process.stdin.messages[0]["method"] == "initialize"
    assert process.stdin.messages[-1]["method"] == "model/list"


class HangingProcess:
    def __init__(self, ignore_terminate=False):
        import asyncio

        self.returncode = None
        self.ignore_terminate = ignore_terminate
        self.terminated = False
        self.killed = False
        self.reaped = False
        self.finished = asyncio.Event()
        self.stdout = self

    async def communicate(self, _data=None):
        await self.finished.wait()
        return b"", b""

    async def readline(self):
        await self.finished.wait()
        return b""

    def terminate(self):
        self.terminated = True
        if not self.ignore_terminate:
            self.returncode = -15
            self.finished.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.finished.set()

    async def wait(self):
        await self.finished.wait()
        self.reaped = True
        return self.returncode


@pytest.mark.asyncio
async def test_codex_execution_timeout_reaps_process_before_removing_workspace(monkeypatch):
    from pathlib import Path

    from fastapi import HTTPException

    process = HangingProcess(ignore_terminate=True)
    arguments = []

    async def spawn(*args, **kwargs):
        arguments.extend(args)
        assert kwargs["start_new_session"] is True
        return process

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(runner.shutil, "which", lambda _binary: "fixture")
    monkeypatch.setattr(runner, "RUN_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(runner, "PROCESS_TERMINATE_GRACE_SECONDS", 0.01)
    payload = runner.RunRequest(
        question="Fixture",
        instructions="Fixture",
        dossier={},
        schema={},
        model="fixture",
        local_provider="ollama",
    )
    with pytest.raises(HTTPException) as failure:
        await runner.run_codex(payload, runner.RUNNER_TOKEN)
    assert failure.value.status_code == 504
    assert process.terminated and process.killed and process.reaped
    assert not Path(arguments[arguments.index("--cd") + 1]).exists()


@pytest.mark.asyncio
async def test_cancelled_communication_stops_and_reaps_process():
    import asyncio

    process = HangingProcess()
    task = asyncio.create_task(runner._communicate(process, timeout=300))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated and process.reaped


@pytest.mark.asyncio
async def test_device_login_timeout_can_be_retried_and_reaps_process(monkeypatch):
    process = HangingProcess()

    async def spawn(*_args, **_kwargs):
        return process

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(runner, "DEVICE_LOGIN_TIMEOUT_SECONDS", 0.01)
    session = runner.DeviceLoginSession(id="timeout")
    await runner._run_device_login(session)
    assert session.status == "error" and "expired" in session.message
    assert process.terminated and process.reaped
