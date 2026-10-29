#!/usr/bin/env bash
# 서버에서 dev 브랜치의 최신 코드로 봇을 업데이트하고 재시작한다.
#
# 사용법: ./scripts/update.sh
#         ./scripts/update.sh --schema-applied   (바뀐 스키마를 기존 DB에 직접 적용한 뒤)
#
# 안전하지 않으면 재시작하지 않고 멈춘다.
# - 서버 저장소에 커밋하지 않은 수정이 있으면 멈춘다 (덮어쓰지 않음)
# - fast-forward로만 업데이트한다 (서버 저장소의 이력을 다시 쓰지 않음)
# - db/schema.sql이 바뀌었으면 멈춘다. 기존 DB에는 스키마가 자동으로 적용되지 않으므로 직접 적용해야 한다
# - 새 이미지를 먼저 빌드하고, 빌드가 실패하면 기존 봇을 그대로 둔다
# - 재시작 후 봇이 시작됐는지 확인하고, 실패하면 로그와 되돌리는 방법을 보여 준다

set -euo pipefail

BRANCH="dev"
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
[ "$current_branch" = "$BRANCH" ] || fail "현재 브랜치가 $BRANCH가 아닙니다 ($current_branch)."

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

if [ "$SCHEMA_APPLIED" = false ] && ! git diff --quiet "$before" "$after" -- db/schema.sql; then
    git diff "$before" "$after" -- db/schema.sql
    fail "db/schema.sql이 바뀌었습니다. 코드는 아직 업데이트하지 않았습니다.
  위 변경을 기존 DB에 직접 적용한 뒤 '$0 --schema-applied'로 다시 실행해 주세요."
fi

git merge --quiet --ff-only "origin/$BRANCH"

echo "== 이미지 빌드"
if ! timeout "$BUILD_TIMEOUT" "${DOCKER[@]}" compose build bot; then
    git reset --quiet --keep "$before"
    fail "이미지 빌드에 실패했습니다. 코드를 이전 커밋($(git rev-parse --short "$before"))으로 되돌렸고, 기존 봇은 그대로 실행 중입니다."
fi

echo "== 재시작"
restarted_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
rollback="git reset --keep $(git rev-parse --short "$before") && ${DOCKER[*]} compose up -d --build"
timeout "$UP_TIMEOUT" "${DOCKER[@]}" compose up -d ||
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
