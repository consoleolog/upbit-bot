#!/usr/bin/env bash
# 서버에서 지금 체크아웃된 브랜치(dev 또는 prod)의 최신 코드로 봇을 업데이트하고 재시작한다.
# dev는 모의 실행 확인용, prod는 운영(실거래) 브랜치다.
#
# 사용법: ./scripts/update.sh
#         ./scripts/update.sh --schema-applied   (바뀐 스키마를 기존 DB에 직접 적용한 뒤)
#
# 안전하지 않으면 재시작하지 않고 멈춘다.
# - 서버 저장소에 커밋하지 않은 수정이 있으면 멈춘다 (덮어쓰지 않음)
# - fast-forward로만 업데이트한다 (서버 저장소의 이력을 다시 쓰지 않음)
# - 새로 추가된 db/migrations/*.sql은 빌드가 끝난 뒤 봇을 멈추고 기존 DB에 이름 순서대로 적용한 다음 새 봇을 시작한다.
#   옛 봇이 바뀐 스키마로 도는 시간이 없게 한다
#   (파일 이름은 날짜-순번-동사-대상.sql. 예: 20261119-01-create-market-halt.sql)
#   (파일마다 한 트랜잭션. 마이그레이션은 여러 번 적용해도 결과가 같게 쓴다: CREATE TABLE IF NOT EXISTS 등)
# - db/schema.sql이 바뀌었는데 새 마이그레이션이 없으면 멈춘다. 기존 DB에는 schema.sql이 자동으로 적용되지 않으므로 직접 적용해야 한다
# - 마이그레이션이 있는데 DB에 접속할 수 없으면 코드와 봇을 건드리지 않고 멈춘다
# - 모의 실행 봇을 쓰면(COMPOSE_PROFILES=paper) 모의 DB에도 마이그레이션을 적용한다. 실패해도 실거래 배포는 계속한다
# - 새 이미지를 먼저 빌드하고, 빌드가 실패하면 기존 봇을 그대로 둔다
# - 재시작 후 봇이 시작됐는지 확인하고, 실패하면 로그와 되돌리는 방법을 보여 준다

set -euo pipefail

# 서버가 받을 수 있는 브랜치. 다른 브랜치(기능 브랜치 등)로 운영 서버를 돌리지 않게 막는다
ALLOWED_BRANCHES="dev prod"
SCHEMA_APPLIED=false
case "${1:-}" in
    "") ;;
    --schema-applied) SCHEMA_APPLIED=true ;;
    *) echo "알 수 없는 옵션: $1 (사용법: $0 [--schema-applied])" >&2; exit 2 ;;
esac
START_TIMEOUT_SECONDS="${START_TIMEOUT_SECONDS:-60}"
# 외부 호출의 시간 제한(초). 이미지 빌드는 의존성 설치로 오래 걸릴 수 있어 넉넉히 둔다
FETCH_TIMEOUT=60
BUILD_TIMEOUT=900
UP_TIMEOUT=180
MIGRATE_TIMEOUT=60

cd "$(dirname "$0")/.."

# docker 그룹에 속하지 않은 사용자는 sudo로 실행한다
if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
else
    DOCKER=(sudo docker)
fi

fail() {
    echo "중단: $*" >&2
    exit 1
}

[ -f .env ] || fail ".env가 없습니다. DB 접속 정보(DB_NAME, DB_USER, DB_PASSWORD)를 먼저 만들어 주세요."

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git status --short --untracked-files=no >&2
    fail "커밋하지 않은 수정이 있습니다. 수정 내용을 확인하고 정리한 뒤 다시 실행해 주세요."
fi

current_branch="$(git rev-parse --abbrev-ref HEAD)"
case " $ALLOWED_BRANCHES " in
    *" $current_branch "*) BRANCH="$current_branch" ;;
    *) fail "현재 브랜치가 dev 또는 prod가 아닙니다 ($current_branch)." ;;
esac

timeout "$FETCH_TIMEOUT" git fetch --quiet origin "$BRANCH" || fail "git fetch가 실패했거나 ${FETCH_TIMEOUT}초 안에 끝나지 않았습니다."
before="$(git rev-parse HEAD)"
after="$(git rev-parse "origin/$BRANCH")"

if [ "$before" = "$after" ]; then
    echo "이미 최신입니다 ($(git log -1 --format='%h %s' HEAD)). 재시작하지 않습니다."
    exit 0
fi

git merge-base --is-ancestor "$before" "$after" ||
    fail "서버 저장소가 origin/$BRANCH에서 갈라졌습니다. fast-forward로 업데이트할 수 없습니다."

echo "== 받을 커밋"
git log --format='  %h %s' "$before..$after"

# 이번에 새로 추가된 마이그레이션 (이름 순서)
mapfile -t migrations < <(git diff --name-only --diff-filter=A "$before" "$after" -- 'db/migrations/*.sql' | sort)

if [ "$SCHEMA_APPLIED" = false ] && [ "${#migrations[@]}" -eq 0 ] && ! git diff --quiet "$before" "$after" -- db/schema.sql; then
    git diff "$before" "$after" -- db/schema.sql
    fail "db/schema.sql이 바뀌었습니다. 코드는 아직 업데이트하지 않았습니다.
  위 변경을 기존 DB에 직접 적용한 뒤 '$0 --schema-applied'로 다시 실행해 주세요."
fi

# 마이그레이션이 있으면 DB에 접속할 수 있는지 먼저 확인한다. 안 되면 코드도 봇도 건드리지 않고 멈춘다
if [ "${#migrations[@]}" -gt 0 ]; then
    timeout "$MIGRATE_TIMEOUT" "${DOCKER[@]}" compose exec -T db \
        sh -c 'pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' </dev/null ||
        fail "DB에 접속할 수 없어 업데이트하지 않았습니다 (마이그레이션 필요). 코드와 봇은 그대로입니다.
  DB 상태 확인: ${DOCKER[*]} compose ps -a db / ${DOCKER[*]} compose logs --tail 50 db
  DB 시작(데이터 볼륨이 남아 있으면 그대로 씀. 확인: ${DOCKER[*]} volume ls | grep db-data): ${DOCKER[*]} compose up -d db"
fi

git merge --quiet --ff-only "origin/$BRANCH"

echo "== 이미지 빌드"
if ! timeout "$BUILD_TIMEOUT" "${DOCKER[@]}" compose build bot; then
    git reset --quiet --keep "$before"
    fail "이미지 빌드에 실패했습니다. 코드를 이전 커밋($(git rev-parse --short "$before"))으로 되돌렸고, 기존 봇은 그대로 실행 중입니다."
fi

if [ "${#migrations[@]}" -gt 0 ]; then
    # 옛 봇이 바뀐 스키마로 돌지 않게 먼저 멈춘다. 컨테이너는 지우지 않으므로 'compose start'로 옛 봇을 다시 띄울 수 있다
    echo "== 봇 멈춤 (마이그레이션 동안)"
    timeout "$UP_TIMEOUT" "${DOCKER[@]}" compose stop bot || {
        git reset --quiet --keep "$before"
        fail "봇을 멈추지 못해 마이그레이션을 적용하지 않았습니다. 코드를 이전 커밋($(git rev-parse --short "$before"))으로 되돌렸습니다. 봇 상태를 확인해 주세요."
    }
    echo "== DB 마이그레이션"
    applied=0
    for migration in "${migrations[@]}"; do
        echo "  $migration"
        # DB 컨테이너 안의 접속 정보로 적용한다. 오류가 나면 그 파일은 롤백되고 멈춘다
        status=0
        timeout "$MIGRATE_TIMEOUT" "${DOCKER[@]}" compose exec -T db \
            sh -c 'psql -q -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <"$migration" ||
            status=$?
        if [ "$status" -eq 0 ]; then
            applied=$((applied + 1))
            continue
        fi
        git reset --quiet --keep "$before"
        # 첫 파일이 SQL 오류로 실패했으면 그 트랜잭션은 롤백됐으므로 DB는 그대로다. 옛 봇을 다시 띄운다.
        # 시간 초과(124)는 커밋됐는지 알 수 없고, 앞 파일이 적용됐으면 옛 코드와 맞는지 알 수 없으므로 멈춘 채로 둔다
        if [ "$applied" -eq 0 ] && [ "$status" -ne 124 ] && timeout "$UP_TIMEOUT" "${DOCKER[@]}" compose start bot; then
            fail "마이그레이션($migration) 적용에 실패했습니다. DB는 바뀌지 않았고, 코드를 이전 커밋($(git rev-parse --short "$before"))으로 되돌려 이전 봇을 다시 시작했습니다."
        fi
        fail "마이그레이션($migration) 적용에 실패했습니다. DB 상태를 확신할 수 없어 봇을 멈춘 채로 두었습니다 (매매·손절 없음).
  코드는 이전 커밋($(git rev-parse --short "$before"))으로 되돌렸습니다. 마이그레이션은 여러 번 적용해도 되므로 원인을 고친 뒤 다시 실행하거나,
  DB를 확인한 뒤 이전 봇을 다시 시작하세요: ${DOCKER[*]} compose start bot"
    done
fi

# 모의 실행 봇(.env의 COMPOSE_PROFILES=paper)을 쓰면 모의 DB(<DB_NAME>_paper)에도 같은 마이그레이션을 적용한다.
# 모의 쪽 실패는 실거래 배포를 막지 않는다. 경고만 남기고 모의 봇은 멈춘 채로 둔다 (바뀐 코드가 옛 스키마로 돌지 않게)
up_services=()
if [ "${#migrations[@]}" -gt 0 ] && "${DOCKER[@]}" compose config --services 2>/dev/null | grep -qx paper-bot; then
    echo "== 모의 DB 마이그레이션"
    timeout "$UP_TIMEOUT" "${DOCKER[@]}" compose stop paper-bot || true
    for migration in "${migrations[@]}"; do
        echo "  $migration"
        if ! timeout "$MIGRATE_TIMEOUT" "${DOCKER[@]}" compose exec -T db \
            sh -c 'psql -q -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "${POSTGRES_DB}_paper"' <"$migration"; then
            echo "경고: 모의 DB 마이그레이션($migration)에 실패해 모의 봇은 시작하지 않습니다. 실거래 봇은 계속 업데이트합니다.
  모의 DB가 없으면: ./scripts/init_paper_db.sh / 원인을 고친 뒤 다시 적용하고 시작: ${DOCKER[*]} compose up -d paper-bot" >&2
            up_services=(db bot)
            break
        fi
    done
fi

echo "== 재시작"
restarted_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
rollback="git reset --keep $(git rev-parse --short "$before") && ${DOCKER[*]} compose up -d --build"
if [ "${#migrations[@]}" -gt 0 ]; then
    # 코드는 되돌려도 적용한 마이그레이션은 DB에 남는다
    rollback="$rollback
  (적용한 마이그레이션은 DB에 남습니다. 이전 코드가 바뀐 스키마로 동작하는지 먼저 확인하세요)"
fi
timeout "$UP_TIMEOUT" "${DOCKER[@]}" compose up -d "${up_services[@]}" ||
    fail "재시작(docker compose up)에 실패했습니다. 상태를 확인해 주세요.
  이전 버전으로 되돌리기: $rollback"

for _ in $(seq "$START_TIMEOUT_SECONDS"); do
    # 파이프로 바로 grep -q에 넘기면, 로그가 길 때 grep이 먼저 끝나 docker가 SIGPIPE로 실패하고
    # pipefail 때문에 시작했는데도 실패로 판정될 수 있다. 로그를 먼저 받은 뒤 찾는다
    logs="$("${DOCKER[@]}" compose logs --since "$restarted_at" bot 2>/dev/null || true)"
    if grep -q "application started" <<<"$logs"; then
        echo "== 완료: $(git log -1 --format='%h %s' HEAD)"
        "${DOCKER[@]}" compose ps
        exit 0
    fi
    sleep 1
done

echo "== 봇 로그 (최근 50줄)" >&2
"${DOCKER[@]}" compose logs --tail 50 bot >&2 || true
fail "봇이 ${START_TIMEOUT_SECONDS}초 안에 시작되지 않았습니다. 위 로그를 확인해 주세요.
  이전 버전으로 되돌리기: $rollback"
