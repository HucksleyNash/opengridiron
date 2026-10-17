"""Exercise the tracked hook against real local Git repositories and pushes."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def push_repo(tmp_path):
    repo = tmp_path / "source"
    remote = tmp_path / "remote.git"
    repo.mkdir()

    def git_env():
        # A parent Git hook can export GIT_DIR and other repository-local values.
        # Never let those redirect these disposable repositories into the real one.
        return {
            **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            "GIT_TERMINAL_PROMPT": "0",
        }

    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True, env=git_env()
    )
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)], check=True, capture_output=True, env=git_env()
    )

    def git(*args, check=True):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            capture_output=True,
            check=check,
            env=git_env(),
        )

    git("config", "user.name", "CI gate test")
    git("config", "user.email", "ci@example.test")
    git("config", "core.hooksPath", ".githooks")
    git("remote", "add", "origin", str(remote))
    (repo / ".githooks").mkdir()
    shutil.copy2(ROOT / ".githooks" / "pre-push", repo / ".githooks" / "pre-push")
    (repo / "scripts").mkdir()
    # A controllable CI command: the Git transport and hook are real.
    (repo / "scripts" / "check.sh").write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" > .git/ci-invoked\n'
        'exit "${CI_GATE_TEST_EXIT:-0}"\n'
    )
    (repo / "source.txt").write_text("committed source\n")
    git("add", ".githooks/pre-push", "scripts/check.sh", "source.txt")
    git("commit", "-m", "Fixture")
    return repo, git


def test_passing_ci_allows_push(push_repo):
    repo, git = push_repo
    git("push", "origin", "main")
    assert (repo / ".git" / "ci-invoked").read_text() == "all\n"
    assert git("ls-remote", "origin", "refs/heads/main").stdout.startswith(
        git("rev-parse", "HEAD").stdout.strip()
    )


def test_failing_ci_prevents_remote_update(push_repo, monkeypatch):
    repo, git = push_repo
    monkeypatch.setenv("CI_GATE_TEST_EXIT", "1")
    result = git("push", "origin", "main", check=False)
    assert result.returncode != 0
    assert (repo / ".git" / "ci-invoked").exists()
    assert git("ls-remote", "origin", "refs/heads/main").stdout == ""


@pytest.mark.parametrize("change", ["unstaged", "staged", "untracked"])
def test_uncommitted_changes_cannot_mask_a_broken_commit(push_repo, change):
    repo, git = push_repo
    if change == "untracked":
        (repo / "missing_from_commit.txt").write_text("required but uncommitted\n")
    else:
        (repo / "source.txt").write_text("a local fix missing from the pushed commit\n")
        if change == "staged":
            git("add", "source.txt")
    result = git("push", "origin", "main", check=False)
    assert result.returncode != 0
    assert "commit or stash" in result.stderr
    assert not (repo / ".git" / "ci-invoked").exists()
    assert git("ls-remote", "origin", "refs/heads/main").stdout == ""


def test_pushing_a_different_commit_is_rejected(push_repo):
    repo, git = push_repo
    git("branch", "other")
    (repo / "source.txt").write_text("new source\n")
    git("add", "source.txt")
    git("commit", "-m", "New head")
    result = git("push", "origin", "other", check=False)
    assert result.returncode != 0
    assert "check out refs/heads/other" in result.stderr
    assert not (repo / ".git" / "ci-invoked").exists()
    assert git("ls-remote", "origin", "refs/heads/other").stdout == ""


def test_deleting_a_branch_does_not_run_ci(push_repo, monkeypatch):
    repo, git = push_repo
    git("push", "origin", "HEAD:temporary")
    (repo / ".git" / "ci-invoked").unlink()
    (repo / "source.txt").write_text("uncommitted\n")
    monkeypatch.setenv("CI_GATE_TEST_EXIT", "1")
    git("push", "origin", "--delete", "temporary")
    assert not (repo / ".git" / "ci-invoked").exists()
    assert git("ls-remote", "origin", "refs/heads/temporary").stdout == ""


def test_changes_during_checks_prevent_push(push_repo):
    repo, git = push_repo
    (repo / "scripts" / "check.sh").write_text('echo "changed during checks" > source.txt\n')
    git("add", "scripts/check.sh")
    git("commit", "-m", "Mutating check fixture")
    result = git("push", "origin", "main", check=False)
    assert result.returncode != 0
    assert "commit or stash" in result.stderr
    assert git("ls-remote", "origin", "refs/heads/main").stdout == ""


def test_head_changes_during_checks_prevent_push_even_with_a_clean_tree(push_repo):
    repo, git = push_repo
    (repo / "scripts" / "check.sh").write_text(
        'git commit --allow-empty -m "HEAD changed during CI"\n'
    )
    git("add", "scripts/check.sh")
    git("commit", "-m", "Head-changing check fixture")
    outgoing = git("rev-parse", "HEAD").stdout

    result = git("push", "origin", "main", check=False)

    assert git("rev-parse", "HEAD").stdout != outgoing
    assert git("status", "--porcelain").stdout == ""
    assert result.returncode != 0
    assert "HEAD changed while CI checks were running" in result.stderr
    assert git("ls-remote", "origin", "refs/heads/main").stdout == ""


BACKEND_CHECKS = ("python -m ruff format", "python -m ruff check", "python -m pytest")
FRONTEND_CHECKS = ("pnpm install", "pnpm test", "pnpm run build", "pnpm exec playwright test")


@pytest.fixture
def real_check_repo(push_repo, tmp_path, monkeypatch):
    repo, git = push_repo
    shutil.copy2(ROOT / "scripts" / "check.sh", repo / "scripts" / "check.sh")
    (repo / "frontend").mkdir()
    (repo / "frontend" / ".gitkeep").touch()
    git("add", "scripts/check.sh", "frontend/.gitkeep")
    git("commit", "-m", "Use the real shared CI gate")

    # Only expensive tool execution is stubbed; the gate, hook, and Git push are real.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("python", "pnpm"):
        executable = bin_dir / name
        executable.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'command="${0##*/} $*"\n'
            'printf "%s\\n" "$command" >> "$CI_GATE_TEST_LOG"\n'
            'if [[ -n "${CI_GATE_TEST_FAIL:-}" && '
            '"$command" == "$CI_GATE_TEST_FAIL"* ]]; then exit 7; fi\n'
        )
        executable.chmod(0o755)
    log = tmp_path / "checks.log"
    monkeypatch.setenv("PYTHON", str(bin_dir / "python"))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CI_GATE_TEST_LOG", str(log))
    monkeypatch.delenv("CI_GATE_TEST_FAIL", raising=False)
    return repo, git, log


def test_full_gate_completes_every_check_before_publishing(real_check_repo):
    _, git, log = real_check_repo
    outgoing = git("rev-parse", "HEAD").stdout.strip()

    git("push", "origin", "main")

    commands = log.read_text().splitlines()
    expected = BACKEND_CHECKS + FRONTEND_CHECKS
    assert len(commands) == len(expected)
    assert all(command.startswith(stage) for command, stage in zip(commands, expected, strict=True))
    assert git("ls-remote", "origin", "refs/heads/main").stdout.startswith(outgoing)


@pytest.mark.parametrize("failed_check", BACKEND_CHECKS + FRONTEND_CHECKS)
def test_each_failed_check_stops_the_gate_and_prevents_publication(
    real_check_repo, monkeypatch, failed_check
):
    _, git, log = real_check_repo
    monkeypatch.setenv("CI_GATE_TEST_FAIL", failed_check)

    result = git("push", "origin", "main", check=False)

    assert result.returncode != 0
    assert log.read_text().splitlines()[-1].startswith(failed_check)
    assert git("ls-remote", "origin", "refs/heads/main").stdout == ""


@pytest.mark.parametrize(
    ("target", "expected"), [("backend", BACKEND_CHECKS), ("frontend", FRONTEND_CHECKS)]
)
def test_ci_job_targets_run_only_their_required_checks(real_check_repo, target, expected):
    repo, _, log = real_check_repo

    result = subprocess.run(
        ["bash", "scripts/check.sh", target], cwd=repo, text=True, capture_output=True
    )

    assert result.returncode == 0, result.stderr
    commands = log.read_text().splitlines()
    assert len(commands) == len(expected)
    assert all(command.startswith(stage) for command, stage in zip(commands, expected, strict=True))
