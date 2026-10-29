"""scripts/update.sh 시험. 임시 git 저장소와 가짜 docker 명령으로 서버의 업데이트 상황을 재현한다."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "update.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="git, bash 필요")

FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$DOCKER_CALLS"
case "$1 $2" in
    "compose build") [ "${FAKE_BUILD_FAILS:-0}" = 1 ] && exit 1 ;;
    "compose logs")
        [ "${FAKE_STARTED:-1}" = 1 ] && echo "INFO src.app.application: application started"
        # 시작 로그 뒤에 긴 로그가 이어지는 경우. 실제 docker처럼 읽는 쪽이 먼저 끝나면(SIGPIPE) 실패 코드로 끝난다
        if [ -n "${FAKE_EXTRA_LOG_LINES:-}" ]; then
            seq "$FAKE_EXTRA_LOG_LINES" || exit $?
        fi
        ;;
    "compose up") [ "${FAKE_UP_FAILS:-0}" = 1 ] && exit 1 ;;
esac
exit 0
"""


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _commit(repo: Path, path: str, content: str, message: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)


class Server:
    """origin(개발 저장소)과 서버 저장소(clone). 서버 저장소에 업데이트 스크립트를 둔다."""

    def __init__(self, tmp_path: Path) -> None:
        self.origin = tmp_path / "origin"
        self.origin.mkdir()
        _git(self.origin, "init", "-q", "-b", "dev")
        _commit(self.origin, "db/schema.sql", "CREATE TABLE a ();\n", "init")
        _commit(self.origin, "scripts/update.sh", SCRIPT.read_text(encoding="utf-8"), "script")
        self.repo = tmp_path / "server"
        _git(tmp_path, "clone", "-q", "-b", "dev", str(self.origin), str(self.repo))
        (self.repo / ".env").write_text("DB_NAME=x\n", encoding="utf-8")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(FAKE_DOCKER, encoding="utf-8")
        (bin_dir / "docker").chmod(0o755)
        self.calls = tmp_path / "docker_calls"
        self.calls.touch()
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DOCKER_CALLS": str(self.calls),
            "START_TIMEOUT_SECONDS": "2",
        }

    def push(self, path: str, content: str, message: str) -> None:
        _commit(self.origin, path, content, message)

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "scripts/update.sh", *args],
            cwd=self.repo,
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=60,
        )

    def head(self) -> str:
        return _git(self.repo, "log", "-1", "--format=%s")

    def docker_calls(self) -> list[str]:
        return self.calls.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def server(tmp_path: Path) -> Server:
    return Server(tmp_path)


def test_updates_builds_and_restarts(server: Server) -> None:
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")

    result = server.run()

    assert result.returncode == 0, result.stderr
    assert "feat: 새 기능" in result.stdout
    assert server.head() == "feat: 새 기능"
    calls = server.docker_calls()
    # 빌드를 먼저 하고 재시작한다
    assert calls.index("compose build bot") < calls.index("compose up -d")
    assert "완료" in result.stdout


def test_already_up_to_date_does_not_restart(server: Server) -> None:
    result = server.run()

    assert result.returncode == 0
    assert "이미 최신" in result.stdout
    assert not any(call.startswith("compose") for call in server.docker_calls())


def test_schema_change_stops_before_updating(server: Server) -> None:
    server.push("db/schema.sql", "CREATE TABLE a ();\nCREATE TABLE b ();\n", "feat: 테이블 추가")

    result = server.run()

    assert result.returncode == 1
    assert "db/schema.sql이 바뀌었습니다" in result.stderr
    assert "CREATE TABLE b" in result.stdout
    # 코드도 바꾸지 않고 재시작도 하지 않는다
    assert server.head() == "script"
    assert not any(call.startswith("compose") for call in server.docker_calls())


def test_schema_applied_option_continues(server: Server) -> None:
    server.push("db/schema.sql", "CREATE TABLE a ();\nCREATE TABLE b ();\n", "feat: 테이블 추가")

    result = server.run("--schema-applied")

    assert result.returncode == 0, result.stderr
    assert server.head() == "feat: 테이블 추가"
    assert "compose up -d" in server.docker_calls()


def test_uncommitted_changes_stop(server: Server) -> None:
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")
    (server.repo / "db" / "schema.sql").write_text("edited on server\n", encoding="utf-8")

    result = server.run()

    assert result.returncode == 1
    assert "커밋하지 않은 수정" in result.stderr
    assert server.head() == "script"
    # 서버의 수정을 덮어쓰지 않는다
    assert (server.repo / "db" / "schema.sql").read_text(encoding="utf-8") == "edited on server\n"


def test_build_failure_keeps_running_bot_and_reverts_code(server: Server) -> None:
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")

    result = server.run(FAKE_BUILD_FAILS="1")

    assert result.returncode == 1
    assert "이미지 빌드에 실패" in result.stderr
    assert server.head() == "script"
    assert "compose up -d" not in server.docker_calls()


def test_start_timeout_reports_logs_and_rollback(server: Server) -> None:
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")

    result = server.run(FAKE_STARTED="0")

    assert result.returncode == 1
    assert "시작되지 않았습니다" in result.stderr
    assert "git reset --keep" in result.stderr
    assert "compose logs --tail 50 bot" in server.docker_calls()


def test_long_logs_after_start_are_detected_as_started(server: Server) -> None:
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")

    # 시작 로그 뒤로 로그가 길게 이어져도 시작한 것으로 판정한다 (파이프가 일찍 끊겨도 오판하지 않음)
    result = server.run(FAKE_EXTRA_LOG_LINES="200000")

    assert result.returncode == 0, result.stderr
    assert "완료" in result.stdout


def test_restart_failure_reports_rollback(server: Server) -> None:
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")

    result = server.run(FAKE_UP_FAILS="1")

    assert result.returncode == 1
    assert "재시작(docker compose up)에 실패" in result.stderr
    assert "git reset --keep" in result.stderr


def test_diverged_server_repository_stops(server: Server) -> None:
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")
    _commit(server.repo, "local.txt", "x\n", "서버에서 만든 커밋")

    result = server.run()

    assert result.returncode == 1
    assert "갈라졌습니다" in result.stderr
    assert server.head() == "서버에서 만든 커밋"


def test_wrong_branch_stops(server: Server) -> None:
    _git(server.repo, "checkout", "-q", "-b", "other")

    result = server.run()

    assert result.returncode == 1
    assert "브랜치가 dev가 아닙니다" in result.stderr


def test_missing_env_file_stops(server: Server) -> None:
    (server.repo / ".env").unlink()

    result = server.run()

    assert result.returncode == 1
    assert ".env가 없습니다" in result.stderr


def test_unknown_option_rejected(server: Server) -> None:
    result = server.run("--force")

    assert result.returncode == 2
    assert "알 수 없는 옵션" in result.stderr
