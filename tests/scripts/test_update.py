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
    "compose stop") [ "${FAKE_STOP_FAILS:-0}" = 1 ] && exit 1 ;;
    "compose config") printf '%b\n' "${FAKE_SERVICES-db\nbot}" ;;
    "compose exec")
        # DB 접속 확인. FAKE_DB_DOWN이면 접속할 수 없다
        if [[ "$*" == *pg_isready* ]]; then
            [ "${FAKE_DB_DOWN:-0}" = 1 ] && exit 1
            exit 0
        fi
        # 모의 DB 마이그레이션. FAKE_PAPER_MIGRATE_FAILS면 실패한다
        if [[ "$*" == *_paper* ]]; then
            cat >> "$DOCKER_CALLS.paper_sql"
            [ "${FAKE_PAPER_MIGRATE_FAILS:-0}" = 1 ] && exit 3
            exit 0
        fi
        # 마이그레이션은 표준 입력으로 받는다. 받은 SQL을 기록하고, FAKE_MIGRATE_FAIL_AT번째 적용을 실패시킨다
        cat >> "$DOCKER_CALLS.sql"
        count=$(grep "psql -q" "$DOCKER_CALLS" | grep -vc _paper)
        [ "$count" = "${FAKE_MIGRATE_FAIL_AT:-0}" ] && exit "${FAKE_MIGRATE_EXIT:-3}"
        ;;
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

    def _read(self, suffix: str) -> str:
        path = self.calls.with_name(self.calls.name + suffix)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def applied_sql(self) -> str:
        path = self.calls.with_name(self.calls.name + ".sql")
        return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.fixture
def server(tmp_path: Path) -> Server:
    return Server(tmp_path)


UP_DB = "compose up -d db"
# 봇 컨테이너는 배포마다 다시 만든다 (설정 파일만 바뀌어도 반영되게)
RECREATE_BOT = "compose up -d --force-recreate --no-deps bot"
RECREATE_BOTH = "compose up -d --force-recreate --no-deps bot paper-bot"


def _restarted(server: "Server") -> bool:
    return any(call.startswith("compose up") for call in server.docker_calls())


def test_updates_builds_and_restarts(server: Server) -> None:
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")

    result = server.run()

    assert result.returncode == 0, result.stderr
    assert "feat: 새 기능" in result.stdout
    assert server.head() == "feat: 새 기능"
    calls = server.docker_calls()
    # 빌드를 먼저 하고 재시작한다
    assert calls.index("compose build bot") < calls.index(UP_DB) < calls.index(RECREATE_BOT)
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
    assert RECREATE_BOT in server.docker_calls()


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
    assert not _restarted(server)


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
    assert "브랜치가 dev 또는 prod가 아닙니다" in result.stderr


def test_prod_branch_updates_from_origin_prod(server: Server) -> None:
    # 운영 서버는 prod 브랜치를 받는다. dev의 변경은 prod에 머지되기 전에는 받지 않는다
    _git(server.origin, "checkout", "-q", "-b", "prod")
    _git(server.repo, "fetch", "-q", "origin", "prod")
    _git(server.repo, "checkout", "-q", "-b", "prod", "origin/prod")
    server.push("config/trading.json", '{"live": true}\n', "chore: 실거래 켜기")
    _git(server.origin, "checkout", "-q", "dev")
    server.push("src/app.py", "print(2)\n", "feat: 아직 dev에만 있는 기능")

    result = server.run()

    assert result.returncode == 0, result.stderr
    assert server.head() == "chore: 실거래 켜기"
    assert RECREATE_BOT in server.docker_calls()


def test_missing_env_file_stops(server: Server) -> None:
    (server.repo / ".env").unlink()

    result = server.run()

    assert result.returncode == 1
    assert ".env가 없습니다" in result.stderr


def test_unknown_option_rejected(server: Server) -> None:
    result = server.run("--force")

    assert result.returncode == 2
    assert "알 수 없는 옵션" in result.stderr


MIGRATE_CALL = (
    'compose exec -T db sh -c psql -q -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
)


def test_new_migrations_are_applied_in_order_before_restart(server: Server) -> None:
    server.push("db/migrations/20261119-02-create-b.sql", "CREATE TABLE IF NOT EXISTS b ();\n", "feat: b")
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a2 ();\n", "feat: a2")
    server.push("db/schema.sql", "CREATE TABLE a ();\nCREATE TABLE a2 ();\nCREATE TABLE b ();\n", "feat: 스키마")

    result = server.run()

    # 스키마가 바뀌었어도 마이그레이션이 함께 오면 멈추지 않고 적용한다
    assert result.returncode == 0, result.stderr
    assert server.applied_sql() == "CREATE TABLE IF NOT EXISTS a2 ();\nCREATE TABLE IF NOT EXISTS b ();\n"
    calls = server.docker_calls()
    assert calls.count(MIGRATE_CALL) == 2
    # 빌드가 끝난 뒤 봇을 멈추고 적용한 다음 새 봇을 시작한다 (옛 봇이 바뀐 스키마로 돌지 않음)
    assert (
        calls.index("compose build bot")
        < calls.index("compose stop bot")
        < calls.index(MIGRATE_CALL)
        < calls.index(RECREATE_BOT)
    )


def test_migrations_already_on_server_are_not_applied_again(server: Server) -> None:
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a2 ();\n", "feat: a2")
    assert server.run().returncode == 0
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")

    result = server.run()

    assert result.returncode == 0, result.stderr
    assert server.docker_calls().count(MIGRATE_CALL) == 1


def test_first_migration_failure_restarts_previous_bot(server: Server) -> None:
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE broken (;\n", "feat: 잘못된 마이그레이션")

    result = server.run(FAKE_MIGRATE_FAIL_AT="1")

    assert result.returncode == 1
    assert "마이그레이션(db/migrations/20261119-01-create-a2.sql) 적용에 실패했습니다" in result.stderr
    # 실패한 파일은 롤백돼 DB가 그대로이므로, 멈춰 둔 이전 봇(컨테이너)을 다시 시작한다
    assert "이전 봇을 다시 시작했습니다" in result.stderr
    assert server.head() == "script"
    calls = server.docker_calls()
    assert calls.index("compose stop bot") < calls.index("compose start bot")
    assert not _restarted(server)


@pytest.mark.parametrize(
    ("fail_at", "exit_code"), [("2", "3"), ("1", "124")], ids=["after-earlier-migration-applied", "timeout"]
)
def test_uncertain_migration_failure_leaves_bot_stopped(server: Server, fail_at: str, exit_code: str) -> None:
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a2 ();\n", "feat: a2")
    server.push("db/migrations/20261119-02-create-b.sql", "CREATE TABLE IF NOT EXISTS b ();\n", "feat: b")

    result = server.run(FAKE_MIGRATE_FAIL_AT=fail_at, FAKE_MIGRATE_EXIT=exit_code)

    # 앞 파일이 적용됐거나 커밋 여부를 알 수 없으면, 이전 봇이 바뀐 스키마로 돌지 않게 멈춘 채로 둔다
    assert result.returncode == 1
    assert "봇을 멈춘 채로 두었습니다" in result.stderr
    assert "compose start bot" in result.stderr
    assert server.head() == "script"
    calls = server.docker_calls()
    assert "compose start bot" not in calls
    assert not _restarted(server)


def test_stop_failure_does_not_migrate(server: Server) -> None:
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a2 ();\n", "feat: a2")

    result = server.run(FAKE_STOP_FAILS="1")

    assert result.returncode == 1
    assert "봇을 멈추지 못해" in result.stderr
    assert server.head() == "script"
    assert MIGRATE_CALL not in server.docker_calls()


def test_start_timeout_after_migration_warns_schema_remains(server: Server) -> None:
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a2 ();\n", "feat: a2")

    result = server.run(FAKE_STARTED="0")

    assert result.returncode == 1
    assert "적용한 마이그레이션은 DB에 남습니다" in result.stderr


def test_update_without_migration_does_not_stop_bot(server: Server) -> None:
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")

    assert server.run().returncode == 0
    assert "compose stop bot" not in server.docker_calls()


def test_schema_change_with_only_modified_migration_stops(server: Server) -> None:
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a2 ();\n", "feat: a2")
    assert server.run().returncode == 0
    # 이미 적용된 마이그레이션을 고친 것은 새 마이그레이션이 아니다
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a3 ();\n", "fix: a3")
    server.push("db/schema.sql", "CREATE TABLE a ();\nCREATE TABLE a3 ();\n", "feat: 스키마")

    result = server.run()

    assert result.returncode == 1
    assert "db/schema.sql이 바뀌었습니다" in result.stderr


DB_CHECK_CALL = 'compose exec -T db sh -c pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'


def test_migration_checks_database_before_updating(server: Server) -> None:
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a2 ();\n", "feat: a2")

    result = server.run()

    assert result.returncode == 0, result.stderr
    calls = server.docker_calls()
    # 코드를 받고 빌드하기 전에 DB 접속을 확인한다
    assert calls.index(DB_CHECK_CALL) < calls.index("compose build bot")


def test_database_down_stops_without_touching_code_or_bot(server: Server) -> None:
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a2 ();\n", "feat: a2")

    result = server.run(FAKE_DB_DOWN="1")

    assert result.returncode == 1
    assert "DB에 접속할 수 없어 업데이트하지 않았습니다" in result.stderr
    assert "compose up -d db" in result.stderr
    # 볼륨이 지워졌으면 빈 DB가 새로 만들어지므로, 시작하기 전에 볼륨을 확인하게 안내한다
    assert "volume ls | grep db-data" in result.stderr
    # 코드도 받지 않고, 빌드·봇 멈춤·마이그레이션도 하지 않는다 (실행 중인 봇은 그대로)
    assert server.head() == "script"
    assert [call for call in server.docker_calls() if call.startswith("compose")] == [DB_CHECK_CALL]


def test_update_without_migration_does_not_check_database(server: Server) -> None:
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")

    assert server.run(FAKE_DB_DOWN="1").returncode == 0
    assert DB_CHECK_CALL not in server.docker_calls()


PAPER_MIGRATE_CALL = (
    "compose exec -T db sh -c psql -q -v ON_ERROR_STOP=1 --single-transaction "
    '-U "$POSTGRES_USER" -d "${POSTGRES_DB}_paper"'
)
PAPER = {"FAKE_SERVICES": "db\\nbot\\npaper-bot"}


def test_paper_db_gets_same_migrations_before_restart(server: Server) -> None:
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a2 ();\n", "feat: a2")

    result = server.run(**PAPER)

    assert result.returncode == 0, result.stderr
    assert server._read(".paper_sql") == "CREATE TABLE IF NOT EXISTS a2 ();\n"
    calls = server.docker_calls()
    # 실거래 DB → 모의 봇 멈춤 → 모의 DB → 모두 시작
    assert (
        calls.index(MIGRATE_CALL)
        < calls.index("compose stop paper-bot")
        < calls.index(PAPER_MIGRATE_CALL)
        < calls.index(RECREATE_BOTH)
    )


def test_paper_migration_failure_does_not_block_live_update(server: Server) -> None:
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a2 ();\n", "feat: a2")

    result = server.run(FAKE_PAPER_MIGRATE_FAILS="1", **PAPER)

    # 실거래 봇은 업데이트하고, 모의 봇은 바뀐 코드가 옛 스키마로 돌지 않게 시작하지 않는다
    assert result.returncode == 0, result.stderr
    assert "모의 DB 마이그레이션" in result.stderr and "init_paper_db.sh" in result.stderr
    assert server.head() == "feat: a2"
    assert RECREATE_BOT in server.docker_calls() and RECREATE_BOTH not in server.docker_calls()


def test_paper_not_used_skips_paper_migration(server: Server) -> None:
    server.push("db/migrations/20261119-01-create-a2.sql", "CREATE TABLE IF NOT EXISTS a2 ();\n", "feat: a2")

    assert server.run().returncode == 0
    calls = server.docker_calls()
    assert PAPER_MIGRATE_CALL not in calls
    assert "compose stop paper-bot" not in calls


def test_update_without_migration_does_not_touch_paper_db(server: Server) -> None:
    server.push("src/app.py", "print(1)\n", "feat: 새 기능")

    assert server.run(**PAPER).returncode == 0
    calls = server.docker_calls()
    assert PAPER_MIGRATE_CALL not in calls
    # 모의 봇도 함께 새 이미지로 다시 시작한다
    assert RECREATE_BOTH in calls


def test_config_only_change_recreates_bot(server: Server) -> None:
    # 설정 파일만 바뀌어 이미지가 같아도 봇을 다시 만들어 새 설정을 읽게 한다
    server.push("config/trading.json", '{"timeframe": "15m"}\n', "chore: 설정 변경")

    result = server.run()

    assert result.returncode == 0, result.stderr
    calls = server.docker_calls()
    assert calls.index(UP_DB) < calls.index(RECREATE_BOT)
    # DB는 다시 만들지 않는다
    assert [call for call in calls if "--force-recreate" in call] == [RECREATE_BOT]


def test_paper_bot_is_recreated_with_bot(server: Server) -> None:
    server.push("config/trading.paper.json", '{"timeframe": "15m"}\n', "chore: 모의 설정 변경")

    assert server.run(**PAPER).returncode == 0
    assert RECREATE_BOTH in server.docker_calls()
