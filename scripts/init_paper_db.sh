#!/usr/bin/env bash
# 모의 실행 봇(paper-bot)이 쓸 DB(<DB_NAME>_paper)를 만들고 db/schema.sql을 적용한다. 서버에서 처음 한 번 실행한다.
# 같은 PostgreSQL 컨테이너 안의 다른 DB라 실거래 DB(<DB_NAME>)와 데이터가 섞이지 않는다.
#
# 사용법: ./scripts/init_paper_db.sh
# 여러 번 실행해도 된다. DB가 이미 있으면 만들지 않고, 스키마가 이미 있으면 적용하지 않는다.
# 실거래 DB는 건드리지 않는다.

set -euo pipefail

cd "$(dirname "$0")/.."

DB_TIMEOUT=60

if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
else
    DOCKER=(sudo docker)
fi

fail() {
    echo "중단: $*" >&2
    exit 1
}

# DB 컨테이너 안에서 psql을 실행한다. $1은 접속할 DB 이름의 접미사("" 또는 "_paper"), SQL은 표준 입력으로 받는다
db_psql() {
    timeout "$DB_TIMEOUT" "${DOCKER[@]}" compose exec -T db \
        sh -c 'psql -X -q -tA -v ON_ERROR_STOP=1 -v name="${POSTGRES_DB}_paper" -U "$POSTGRES_USER" -d "${POSTGRES_DB}$1"' sh "$1"
}

timeout "$DB_TIMEOUT" "${DOCKER[@]}" compose exec -T db sh -c 'pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' </dev/null ||
    fail "DB에 접속할 수 없습니다. 확인: ${DOCKER[*]} compose ps -a db"

exists="$(db_psql "" <<<"SELECT 1 FROM pg_database WHERE datname = :'name';")" ||
    fail "모의 DB가 있는지 확인하지 못했습니다."
if [ "$exists" = 1 ]; then
    echo "모의 DB가 이미 있습니다."
else
    echo "== 모의 DB 만들기"
    db_psql "" <<<"CREATE DATABASE :\"name\";" >/dev/null || fail "모의 DB를 만들지 못했습니다."
fi

# 만들기만 하고 스키마 적용 전에 멈췄을 수 있으므로, 테이블이 없으면 스키마를 적용한다
has_schema="$(db_psql "_paper" <<<"SELECT to_regclass('market') IS NOT NULL;")" ||
    fail "모의 DB의 스키마를 확인하지 못했습니다."
if [ "$has_schema" = t ]; then
    echo "모의 DB에 스키마가 이미 있습니다."
else
    echo "== 모의 DB에 스키마 적용 (db/schema.sql)"
    timeout "$DB_TIMEOUT" "${DOCKER[@]}" compose exec -T db \
        sh -c 'psql -X -q -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "${POSTGRES_DB}_paper"' <db/schema.sql ||
        fail "스키마를 적용하지 못했습니다. 모의 DB는 비어 있으므로 원인을 고친 뒤 다시 실행하면 됩니다."
fi

echo "== 완료. 모의 봇 시작: .env에 COMPOSE_PROFILES=paper를 넣고 ${DOCKER[*]} compose up -d"
