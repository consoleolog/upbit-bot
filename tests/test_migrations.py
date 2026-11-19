"""db/migrations 시험 (실제 PostgreSQL). 기존 DB에 적용하는 마이그레이션이 db/schema.sql과 같은 결과를 내는지 본다."""

import re
from pathlib import Path

import asyncpg
import pytest

MIGRATION_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"
MIGRATIONS = sorted(MIGRATION_DIR.glob("*.sql"))
# 날짜-순번-동사-대상 (예: 20261119-01-create-market-halt.sql). 이름 순서가 적용 순서다
NAME = re.compile(r"\d{8}-\d{2}-[a-z]+(-[a-z0-9]+)+\.sql")

# 테이블의 컬럼(이름, 타입, NULL 허용)과 제약(이름, 정의). 스키마 이름은 $1로 받는다
COLUMNS = """
    SELECT column_name, data_type, is_nullable FROM information_schema.columns
    WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position
"""
CONSTRAINTS = """
    SELECT conname, pg_get_constraintdef(c.oid) FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = $1 AND t.relname = $2 ORDER BY conname
"""


@pytest.mark.parametrize("migration", MIGRATIONS, ids=lambda path: path.name)
def test_migration_file_name(migration: Path) -> None:
    assert NAME.fullmatch(migration.name), f"{migration.name}: 날짜-순번-동사-대상.sql 형식이어야 합니다"


@pytest.mark.parametrize("migration", MIGRATIONS, ids=lambda path: path.name)
@pytest.mark.integration
async def test_migration_can_be_applied_again(conn: asyncpg.Connection, migration: Path) -> None:
    # 테스트 DB에는 schema.sql이 이미 적용돼 있다. 이미 반영된 DB에 다시 적용해도 오류가 없어야 한다
    await conn.execute(migration.read_text(encoding="utf-8"))
    await conn.execute(migration.read_text(encoding="utf-8"))


@pytest.mark.integration
async def test_market_halt_migration_matches_schema(conn: asyncpg.Connection) -> None:
    # 빈 스키마에 마이그레이션을 적용해 schema.sql로 만든 테이블과 비교한다. 트랜잭션이 끝나면 롤백된다
    await conn.execute("CREATE SCHEMA migration_check")
    await conn.execute("SET LOCAL search_path TO migration_check, public")
    await conn.execute((MIGRATION_DIR / "20261119-01-create-market-halt.sql").read_text(encoding="utf-8"))

    for query in (COLUMNS, CONSTRAINTS):
        migrated = await conn.fetch(query, "migration_check", "market_halt")
        expected = await conn.fetch(query, "public", "market_halt")
        assert migrated
        assert [tuple(row) for row in migrated] == [tuple(row) for row in expected]
