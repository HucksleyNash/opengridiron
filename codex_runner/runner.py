from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

RUNNER_TOKEN = os.getenv("CODEX_RUNNER_TOKEN", "local-runner-token")
CODEX_BINARY = os.getenv("CODEX_BINARY", "codex")
CODEX_HOME = Path(os.getenv("CODEX_HOME", "/codex-home"))
RUN_TIMEOUT_SECONDS = float(os.getenv("CODEX_RUN_TIMEOUT_SECONDS", "270"))
DEVICE_LOGIN_TIMEOUT_SECONDS = 900
PROCESS_TERMINATE_GRACE_SECONDS = 5
login_lock = asyncio.Lock()
last_key_hash: str | None = None
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DEVICE_URL = re.compile(r"https://auth\.openai\.com/codex/device")
DEVICE_CODE = re.compile(r"\b[A-Z0-9]{4,8}-[A-Z0-9]{4,8}\b")


@dataclass
class DeviceLoginSession:
    id: str
    status: str = "starting"
    verification_url: str | None = None
    user_code: str | None = None
    message: str = "Starting Codex device login…"


device_login_session: DeviceLoginSession | None = None
device_login_task: asyncio.Task[None] | None = None


class RunRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10000)
    instructions: str
    dossier: dict[str, Any]
    schema: dict[str, Any]
    model: str
    api_key: str | None = None
    local_provider: str | None = Field(default=None, pattern="^(ollama|lmstudio)$")


class ModelsRequest(BaseModel):
    api_key: str | None = None


app = FastAPI(title="Isolated Codex Analysis Runner", version="0.1.0")


def _analysis_prompt(payload: RunRequest) -> str:
    dossier = json.dumps(payload.dossier, separators=(",", ":"))
    return (
        f"{payload.instructions}\n\n"
        "The only task data is the timestamped dossier below. Do not use tools, "
        "workspace files, or external sources. Answer the question and return only "
        "a JSON object matching the configured output schema.\n\n"
        f"Timestamped dossier:\n{dossier}\n\n"
        f"Question: {payload.question}"
    )


def authorize(token: str | None) -> None:
    if not token or not __import__("hmac").compare_digest(token, RUNNER_TOKEN):
        raise HTTPException(401, "Invalid runner token")


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    """Terminate and reap the entire CLI process group, including tool children."""

    def stop(sig: signal.Signals) -> None:
        try:
            if getattr(process, "pid", None):
                os.killpg(process.pid, sig)
            elif process.returncode is None:
                process.kill() if sig == signal.SIGKILL else process.terminate()
        except ProcessLookupError:
            pass

    stop(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=PROCESS_TERMINATE_GRACE_SECONDS)
    except TimeoutError:
        stop(signal.SIGKILL)
        await process.wait()
    finally:
        # A child can ignore TERM even after its parent has exited.
        stop(signal.SIGKILL)


async def _communicate(
    process: asyncio.subprocess.Process, data: bytes | None = None, *, timeout: float
) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(process.communicate(data), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        await _stop_process(process)
        raise


async def ensure_login(api_key: str | None) -> None:
    global last_key_hash
    if not api_key:
        return
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    if key_hash == last_key_hash:
        return
    async with login_lock:
        if key_hash == last_key_hash:
            return
        process = await asyncio.create_subprocess_exec(
            CODEX_BINARY,
            "login",
            "--with-api-key",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CODEX_HOME": str(CODEX_HOME)},
            start_new_session=True,
        )
        _stdout, stderr = await _communicate(process, api_key.encode(), timeout=30)
        if process.returncode != 0:
            raise RuntimeError(f"Codex login failed: {stderr.decode(errors='replace')[-800:]}")
        last_key_hash = key_hash


async def codex_authenticated() -> bool:
    if not shutil.which(CODEX_BINARY):
        return False
    process = await asyncio.create_subprocess_exec(
        CODEX_BINARY,
        "login",
        "status",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "CODEX_HOME": str(CODEX_HOME)},
        start_new_session=True,
    )
    try:
        await _communicate(process, timeout=10)
    except TimeoutError:
        return False
    return process.returncode == 0


async def _app_server_response(
    process: asyncio.subprocess.Process, request_id: int
) -> dict[str, Any]:
    if process.stdout is None:
        raise RuntimeError("Codex app-server did not expose its output stream")
    while line := await process.stdout.readline():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") == request_id:
            if error := message.get("error"):
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise RuntimeError(f"Codex app-server request failed: {detail}")
            return message.get("result") or {}
    raise RuntimeError("Codex app-server stopped before returning a response")


async def codex_models() -> list[str]:
    process = await asyncio.create_subprocess_exec(
        CODEX_BINARY,
        "app-server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "CODEX_HOME": str(CODEX_HOME), "NO_COLOR": "1"},
        start_new_session=True,
    )

    async def send(message: dict[str, Any]) -> None:
        process.stdin.write(f"{json.dumps(message)}\n".encode())
        await process.stdin.drain()

    try:
        if process.stdin is None:
            raise RuntimeError("Codex app-server did not expose its input stream")
        await send(
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "opengridiron",
                        "title": "Open Gridiron",
                        "version": "0.1.0",
                    }
                },
            }
        )
        await asyncio.wait_for(_app_server_response(process, 0), timeout=15)
        await send({"method": "initialized", "params": {}})
        await send(
            {
                "method": "model/list",
                "id": 1,
                "params": {"limit": 100, "includeHidden": False},
            }
        )
        result = await asyncio.wait_for(_app_server_response(process, 1), timeout=20)
        return list(
            dict.fromkeys(
                model
                for item in result.get("data", [])
                if isinstance(item, dict)
                and not item.get("hidden", False)
                and isinstance((model := item.get("model") or item.get("id")), str)
                and model
            )
        )
    finally:
        await _stop_process(process)


def _clean_login_output(value: str) -> str:
    return ANSI_ESCAPE.sub("", value).replace("\r", "").strip()


def _codex_failure_detail(stdout: bytes, stderr: bytes) -> str:
    stdout_text = stdout.decode(errors="replace")
    for line in reversed(stdout_text.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        message = event.get("message")
        if not isinstance(message, str):
            error = event.get("error")
            message = error.get("message") if isinstance(error, dict) else None
        if not isinstance(message, str) or not message.strip():
            continue
        try:
            nested = json.loads(message)
        except json.JSONDecodeError:
            nested = None
        if isinstance(nested, dict):
            nested_error = nested.get("error")
            if isinstance(nested_error, dict) and isinstance(nested_error.get("message"), str):
                message = nested_error["message"]
        return _clean_login_output(message)[-1200:]

    stderr_text = _clean_login_output(stderr.decode(errors="replace"))
    if stderr_text:
        return stderr_text[-1200:]
    stdout_text = _clean_login_output(stdout_text)
    return stdout_text[-1200:] if stdout_text else "Codex exited without error output"


def _device_login_response(
    session: DeviceLoginSession, authenticated: bool = False
) -> dict[str, Any]:
    return {
        "status": "authenticated" if authenticated else session.status,
        "authenticated": authenticated,
        "verification_url": session.verification_url,
        "user_code": session.user_code,
        "message": session.message,
        "expires_in": 900 if session.status in {"starting", "pending"} else None,
    }


async def _run_device_login(session: DeviceLoginSession) -> None:
    output = ""
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            CODEX_BINARY,
            "login",
            "--device-auth",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "CODEX_HOME": str(CODEX_HOME), "NO_COLOR": "1"},
            start_new_session=True,
        )
        if process.stdout is None:
            raise RuntimeError("Codex device login did not expose its output stream")
        async with asyncio.timeout(DEVICE_LOGIN_TIMEOUT_SECONDS):
            while line := await process.stdout.readline():
                output = f"{output}\n{line.decode(errors='replace')}"[-6000:]
                cleaned = _clean_login_output(output)
                url_match = DEVICE_URL.search(cleaned)
                code_match = DEVICE_CODE.search(cleaned)
                if url_match:
                    session.verification_url = url_match.group(0)
                if code_match:
                    session.user_code = code_match.group(0)
                if session.verification_url and session.user_code:
                    session.status = "pending"
                    session.message = "Open the ChatGPT sign-in page and enter the one-time code."
                else:
                    session.message = cleaned
            return_code = await process.wait()
        authenticated = return_code == 0 and await codex_authenticated()
        session.status = "authenticated" if authenticated else "error"
        session.message = (
            "Codex CLI is connected to ChatGPT."
            if authenticated
            else _clean_login_output(output) or "Codex device login did not complete."
        )
    except TimeoutError:
        session.status = "error"
        session.message = "Codex device login expired. Start a new sign-in attempt."
    except asyncio.CancelledError:
        session.status = "error"
        session.message = "Codex device login was interrupted. Start a new sign-in attempt."
        raise
    except Exception as exc:
        session.status = "error"
        session.message = f"Codex device login failed: {str(exc)[:800]}"
    finally:
        if process is not None and getattr(process, "returncode", 0) is None:
            await _stop_process(process)


async def _current_device_login() -> dict[str, Any]:
    if device_login_session and device_login_session.status in {"starting", "pending"}:
        return _device_login_response(device_login_session)
    authenticated = await codex_authenticated()
    if device_login_session:
        if not authenticated and device_login_session.status == "authenticated":
            device_login_session.status = "idle"
            device_login_session.message = "Codex CLI is not connected to ChatGPT."
        return _device_login_response(device_login_session, authenticated)
    session = DeviceLoginSession(
        id="status",
        status="authenticated" if authenticated else "idle",
        message=(
            "Codex CLI is connected to ChatGPT."
            if authenticated
            else "Codex CLI is not connected to ChatGPT."
        ),
    )
    return _device_login_response(session, authenticated)


@app.get("/healthz")
async def healthz(x_runner_token: str | None = Header(default=None)) -> dict[str, Any]:
    authorize(x_runner_token)
    available = shutil.which(CODEX_BINARY) is not None
    authenticated = await codex_authenticated() if available else False
    return {
        "status": "ok" if available and authenticated else "degraded",
        "codex_available": available,
        "authenticated": authenticated,
    }


@app.get("/v1/auth/device")
async def device_auth_status(x_runner_token: str | None = Header(default=None)) -> dict[str, Any]:
    authorize(x_runner_token)
    if not shutil.which(CODEX_BINARY):
        raise HTTPException(503, "Codex CLI is not installed in the runner")
    return await _current_device_login()


@app.post("/v1/auth/device")
async def start_device_auth(x_runner_token: str | None = Header(default=None)) -> dict[str, Any]:
    global device_login_session, device_login_task
    authorize(x_runner_token)
    if not shutil.which(CODEX_BINARY):
        raise HTTPException(503, "Codex CLI is not installed in the runner")
    if await codex_authenticated():
        return await _current_device_login()
    if device_login_session and device_login_session.status in {"starting", "pending"}:
        return _device_login_response(device_login_session)
    device_login_session = DeviceLoginSession(id=uuid.uuid4().hex)
    device_login_task = asyncio.create_task(_run_device_login(device_login_session))
    for _ in range(50):
        if device_login_session.status != "starting":
            break
        await asyncio.sleep(0.1)
    return _device_login_response(
        device_login_session,
        device_login_session.status == "authenticated",
    )


@app.post("/v1/models")
async def list_codex_models(
    payload: ModelsRequest, x_runner_token: str | None = Header(default=None)
) -> dict[str, Any]:
    authorize(x_runner_token)
    if not shutil.which(CODEX_BINARY):
        raise HTTPException(503, "Codex CLI is not installed in the runner")
    await ensure_login(payload.api_key)
    if not await codex_authenticated():
        raise HTTPException(
            409,
            "Connect the Codex CLI to ChatGPT or enter an OpenAI API key before loading models.",
        )
    try:
        models = await codex_models()
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(502, f"Could not load Codex models: {str(exc)[:800]}") from exc
    if not models:
        raise HTTPException(502, "Codex returned no available models")
    return {"models": models}


@app.post("/v1/run")
async def run_codex(
    payload: RunRequest, x_runner_token: str | None = Header(default=None)
) -> dict[str, Any]:
    authorize(x_runner_token)
    if not shutil.which(CODEX_BINARY):
        raise HTTPException(503, "Codex CLI is not installed in the runner")
    await ensure_login(payload.api_key)
    if not payload.local_provider and not await codex_authenticated():
        raise HTTPException(
            409,
            "Codex is not authenticated. Configure an OpenAI API key or complete "
            "device login in the runner.",
        )
    with tempfile.TemporaryDirectory(prefix="football-codex-") as temp_dir:
        workspace = Path(temp_dir)
        schema_path = workspace / "output-schema.json"
        output_path = workspace / "result.json"
        schema_path.write_text(json.dumps(payload.schema), encoding="utf-8")
        prompt = _analysis_prompt(payload)
        command = [
            CODEX_BINARY,
            "--ask-for-approval",
            "never",
            "exec",
            "--ephemeral",
            "--json",
            "--ignore-user-config",
            "--output-last-message",
            str(output_path),
            "--output-schema",
            str(schema_path),
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--cd",
            str(workspace),
            "--model",
            payload.model,
        ]
        if payload.local_provider:
            command.extend(["--oss", "--local-provider", payload.local_provider])
        command.append("-")
        env = {**os.environ, "CODEX_HOME": str(CODEX_HOME), "NO_COLOR": "1"}
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, stderr = await _communicate(
                process, prompt.encode(), timeout=RUN_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            raise HTTPException(504, "Codex execution timed out; its process was stopped.") from exc
        if process.returncode != 0:
            raise HTTPException(
                502, f"Codex execution failed: {_codex_failure_detail(stdout, stderr)}"
            )
        if not output_path.exists():
            raise HTTPException(502, "Codex completed without writing a final result")
        try:
            output = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(
                502, "Codex returned invalid JSON despite the output schema"
            ) from exc
        event_count = sum(1 for line in stdout.splitlines() if line.strip())
        return {
            "output": output,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "event_count": event_count,
        }
