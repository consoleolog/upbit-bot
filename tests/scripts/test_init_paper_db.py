"""scripts/init_paper_db.sh 시험 (실제 PostgreSQL).

가짜 docker가 'compose exec -T db <명령>'을 이 컴퓨터에서 그대로 실행해, 서버의 DB 컨테이너 안처럼
POSTGRES_USER, POSTGRES_DB 환경 변수로 시험 DB 서버에 접속한다.
"""

import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest

from src.connections.database import DatabaseConfig

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "init_paper_db.sh"
PG_BIN = Path("/usr/lib/postgresql/16/bin")

pytestmark = [pytest.mark.integration, pytest.mark.skipif(shutil.which("psql") is None, reason="psql 필요")]

FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$CALLS"
[ "$1" = info ] && exit 0
if [ "$1 $2 $3 $4" = "compose exec -T db" ]; then
    shift 4
    exec "$@"
fi
exit 0
"""


def _admin(config: DatabaseConfig, sql: str, name: str) -> None:
    """DB를 만들거나 지운다. 이름은 psql 변수(:"name")로만 넘긴다 (SQL을 문자열로 만들지 않음)."""
    subprocess.run(
        ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-v", f"name={name}", "-d", config.name],
        input=sql,
        env={
            **os.environ,
            "PGHOST": config.host,
            "PGPORT": str(config.port),
            "PGUSER": config.user,
            "PGPASSWORD": config.password,
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


async def _connect(config: DatabaseConfig, database: str) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=config.host, port=config.port, user=config.user, password=config.password, database=database
    )


@pytest.fixture
def main_db(test_db_config: DatabaseConfig) -> Iterator[str]:
    """빈 '운영' DB 하나. 시험이 끝나면 그 DB와 모의 DB를 지운다."""
    name = "ip_" + uuid.uuid4().hex[:12]
    _admin(test_db_config, 'CREATE DATABASE :"name";', name)
    try:
        yield name
    finally:
        for database in (f"{name}_paper", name):
            _admin(test_db_config, 'DROP DATABASE IF EXISTS :"name";', database)


def _run(tmp_path: Path, config: DatabaseConfig, main_db: str) -> subprocess.CompletedProcess[str]:
    repo = tmp_path / "repo"
    if not repo.exists():
        (repo / "scripts").mkdir(parents=True)
        (repo / "db").mkdir()
        shutil.copy(SCRIPT, repo / "scripts" / "init_paper_db.sh")
        shutil.copy(ROOT / "db" / "schema.sql", repo / "db" / "schema.sql")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(FAKE_DOCKER, encoding="utf-8")
        (bin_dir / "docker").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}:{PG_BIN}:{os.environ['PATH']}",
        "CALLS": str(tmp_path / "calls"),
        "POSTGRES_USER": config.user,
        "POSTGRES_DB": main_db,
        "PGHOST": config.host,
        "PGPORT": str(config.port),
        "PGPASSWORD": config.password,
    }
    return subprocess.run(
        ["bash", "scripts/init_paper_db.sh"], cwd=repo, env=env, capture_output=True, text=True, timeout=60
    )


async def test_creates_paper_db_with_schema_and_is_idempotent(
    tmp_path: Path, test_db_config: DatabaseConfig, main_db: str
) -> None:
    first = _run(tmp_path, test_db_config, main_db)

    assert first.returncode == 0, first.stderr
    assert "모의 DB 만들기" in first.stdout
    assert "스키마 적용" in first.stdout
    paper = await _connect(test_db_config, f"{main_db}_paper")
    try:
        tables = {
            row["tablename"] for row in await paper.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
    finally:
        await paper.close()
    assert {"market", "indicator_info", "paper_position", "orders", "position", "market_halt"} <= tables

    # 다시 실행해도 만들거나 적용하지 않는다
    second = _run(tmp_path, test_db_config, main_db)
    assert second.returncode == 0, second.stderr
    assert "모의 DB가 이미 있습니다" in second.stdout
    assert "스키마가 이미 있습니다" in second.stdout


def test_applies_schema_when_db_was_created_without_it(
    tmp_path: Path, test_db_config: DatabaseConfig, main_db: str
) -> None:
    # 만들기만 하고 스키마 적용 전에 멈춘 경우
    _admin(test_db_config, 'CREATE DATABASE :"name";', f"{main_db}_paper")

    result = _run(tmp_path, test_db_config, main_db)

    assert result.returncode == 0, result.stderr
    assert "모의 DB가 이미 있습니다" in result.stdout
    assert "스키마 적용" in result.stdout


async def test_main_db_is_not_touched(tmp_path: Path, test_db_config: DatabaseConfig, main_db: str) -> None:
    assert _run(tmp_path, test_db_config, main_db).returncode == 0

    # 운영 DB에는 아무 테이블도 만들지 않는다
    conn = await _connect(test_db_config, main_db)
    try:
        assert await conn.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") == 0
    finally:
        await conn.close()


def test_database_unreachable_stops(tmp_path: Path, test_db_config: DatabaseConfig) -> None:
    config = DatabaseConfig(
        host="127.0.0.1", port=1, name="x", user=test_db_config.user, password=test_db_config.password
    )

    result = _run(tmp_path, config, "x")

    assert result.returncode == 1
    assert "DB에 접속할 수 없습니다" in result.stderr
