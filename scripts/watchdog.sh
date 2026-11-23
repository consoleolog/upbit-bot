#!/usr/bin/env bash
# 봇 감시. 봇과 따로 VM의 cron(root)으로 5분마다 실행하고, 이상이 있으면 디스코드로 알린다.
# 봇 안에서는 봇이 죽었다는 것을 알릴 수 없으므로 밖에서 본다. VM 자체가 꺼지면 이 감시도 멈춘다.
#
# 설치 (서버): sudo crontab -e 에 한 줄 추가
#   */5 * * * * /home/<사용자>/upbit-bot/scripts/watchdog.sh
# 감시 기록 보기: sudo journalctl -t upbit-bot-watchdog --since today
#
# 확인하는 것
# ① db, bot 컨테이너가 실행 중인가. 배포 중 잠깐 멈추는 것은 넘기도록 두 번 연속(10분) 꺼져 있을 때만 이상으로 본다
# ② 설정한 마켓마다 마지막으로 처리(커밋)한 캔들이 너무 오래되지 않았는가.
#    indicator_info.candle_time은 캔들 처리가 성공해 커밋될 때만 바뀌므로, 봇이 살아 있어도 DB 장애 등으로
#    작업이 계속 실패하면 잡힌다. 마감 뒤 처리까지 (캔들 주기 + 15분)을 넘기면 이상으로 본다 (30분봉이면 45분).
#    시각은 DB의 now()로 비교한다. DB에 조회할 수 없어도 이상으로 본다
#
# 알림: 이상이 시작되면 1번, 이어지면 6시간마다, 회복되면 1번. 보내지 못하면 상태를 바꾸지 않아 다음 실행에서 다시 보낸다.
# 웹훅 URL은 .env의 DISCORD_WEBHOOK_URL에서 읽고, 출력·로그·명령줄 인자에 남기지 않는다.

set -euo pipefail

cd "$(dirname "$0")/.."

STATE_DIR="${WATCHDOG_STATE_DIR:-/var/lib/upbit-bot-watchdog}"
# 시험에서 시각을 정할 때만 쓴다
NOW="${WATCHDOG_NOW:-$(date +%s)}"
REMIND_SECONDS=$((6 * 3600))
# 컨테이너가 이 횟수만큼 연속으로 꺼져 있어야 이상으로 본다
DOWN_CHECKS=2
GRACE_MINUTES=15
DOCKER_TIMEOUT=30
SEND_TIMEOUT=10
SERVICES="db bot"

log() {
    echo "$*"
    if command -v logger >/dev/null 2>&1; then
        logger -t upbit-bot-watchdog -- "$*" || true
    fi
}

mkdir -p "$STATE_DIR"
# 앞 실행이 docker 지연으로 길어져도 겹쳐 돌지 않게 한다
exec 9>"$STATE_DIR/lock"
flock -n 9 || exit 0

# 설정에서 캔들 주기(분)와 마켓을 읽는다. 형식이 예상과 다르면 감시가 잘못 판단하지 않게 이상으로 알린다
config_timeframe="$(sed -n 's/^ *"timeframe": *"\([0-9]*\)m",* *$/\1/p' config/trading.json)"
config_markets="$(sed -n 's/^ *"markets": *\[\(.*\)\],* *$/\1/p' config/trading.json | tr -d ' "')"

problems=()
# 컨테이너가 한 번 꺼진 것을 본 상태 (아직 알리지 않음). 이때는 정상이라고 보지 않으므로 회복 알림도 보내지 않는다
pending=false

if ! [[ "$config_timeframe" =~ ^[0-9]+$ ]] || ! [[ "$config_markets" =~ ^[A-Z]+-[A-Z0-9]+(,[A-Z]+-[A-Z0-9]+)*$ ]]; then
    problems+=("설정(config/trading.json)에서 캔들 주기나 마켓을 읽지 못해 처리 지연을 확인하지 못함")
fi

# ① 컨테이너
db_running=false
if running="$(timeout "$DOCKER_TIMEOUT" docker compose ps --status running --services 2>/dev/null)"; then
    down=()
    for service in $SERVICES; do
        if grep -qx "$service" <<<"$running"; then
            [ "$service" = db ] && db_running=true
        else
            down+=("$service")
        fi
    done
    count=0
    if [ "${#down[@]}" -gt 0 ]; then
        count=$(($(cat "$STATE_DIR/down_count" 2>/dev/null || echo 0) + 1))
        if [ "$count" -ge "$DOWN_CHECKS" ]; then
            problems+=("컨테이너가 실행 중이 아님: ${down[*]}")
        else
            pending=true
            log "컨테이너가 실행 중이 아님: ${down[*]} (다음 확인에서도 그러면 알림)"
        fi
    fi
    echo "$count" >"$STATE_DIR/down_count"
else
    problems+=("docker compose 상태를 확인하지 못함")
fi

# ② 마켓별 마지막 처리. DB가 꺼져 있으면 ①이 다룬다
if [ "$db_running" = true ] && [[ "$config_timeframe" =~ ^[0-9]+$ ]] && [ -n "$config_markets" ]; then
    # 마켓마다 "마켓|마지막 처리한 캔들 시작 뒤 지난 분" (기록이 없으면 -1). 마켓 코드는 위에서 형식을 확인했다
    query="SELECT s.symbol || '|' || coalesce(floor(extract(epoch FROM now() - max(i.candle_time)) / 60)::int, -1)
FROM unnest(string_to_array(:'markets', ',')) AS s(symbol)
LEFT JOIN market m ON m.symbol = s.symbol
LEFT JOIN indicator_info i ON i.market_id = m.market_id
GROUP BY s.symbol ORDER BY s.symbol;"
    if rows="$(timeout "$DOCKER_TIMEOUT" docker compose exec -T db \
        sh -c 'psql -X -A -t -q -v ON_ERROR_STOP=1 -v markets="$1" -U "$POSTGRES_USER" -d "$POSTGRES_DB"' sh "$config_markets" \
        <<<"$query" 2>/dev/null)"; then
        # 처리는 캔들이 마감된 뒤(시작 + 주기)에 한다
        limit=$((config_timeframe * 2 + GRACE_MINUTES))
        checked=0
        while IFS='|' read -r symbol minutes; do
            [ -n "$symbol" ] || continue
            # 예상한 형식이 아니면 정상으로 넘기지 않는다
            if ! [[ "$minutes" =~ ^-?[0-9]+$ ]]; then
                problems+=("DB 조회 결과를 해석하지 못함")
                continue
            fi
            checked=$((checked + 1))
            if [ "$minutes" -lt 0 ]; then
                problems+=("$symbol 처리 기록 없음")
            elif [ "$minutes" -gt "$limit" ]; then
                problems+=("$symbol 마지막 캔들 처리 $((minutes - config_timeframe))분 전")
            fi
        done <<<"$rows"
        # 설정한 마켓 수만큼 결과가 있어야 한다 (빈 결과를 정상으로 보지 않음)
        expected=$(($(tr -cd ',' <<<"$config_markets" | wc -c) + 1))
        if [ "$checked" -ne "$expected" ]; then
            problems+=("DB 조회 결과의 마켓 수가 설정과 다름 (${checked}/${expected})")
        fi
    else
        problems+=("DB에서 캔들 처리 기록을 조회하지 못함")
    fi
fi

# 디스코드로 보낸다. 성공하면 0
send() {
    local url text payload code
    url="$(grep -E '^DISCORD_WEBHOOK_URL=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r')" || true
    url="${url%\"}"
    url="${url#\"}"
    if ! [[ "$url" =~ ^https://(discord\.com|discordapp\.com)/api/webhooks/ ]]; then
        log "알림을 보내지 못함: .env의 DISCORD_WEBHOOK_URL이 없거나 디스코드 웹훅 형식이 아님"
        return 1
    fi
    text="${1//\\/\\\\}"
    text="${text//\"/\\\"}"
    text="${text//$'\n'/\\n}"
    payload="{\"content\":\"$text\",\"allowed_mentions\":{\"parse\":[]}}"
    # URL은 명령줄 인자(ps에 보임) 대신 설정으로 넘기고, curl 오류 메시지도 남기지 않는다
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time "$SEND_TIMEOUT" \
        -H 'Content-Type: application/json' --data-binary @- -K <(printf 'url = "%s"\n' "$url") \
        <<<"$payload" 2>/dev/null)" || code="000"
    if [[ "$code" =~ ^2[0-9][0-9]$ ]]; then
        return 0
    fi
    log "알림을 보내지 못함: HTTP $code"
    return 1
}

alerting_file="$STATE_DIR/alerting"
if [ "${#problems[@]}" -gt 0 ]; then
    detail="$(printf -- '- %s\n' "${problems[@]}")"
    log "이상: ${problems[*]}"
    last="$(cat "$alerting_file" 2>/dev/null || true)"
    if [ -z "$last" ]; then
        title="🚨 [봇 감시] 이상 감지"
    elif [ $((NOW - last)) -ge "$REMIND_SECONDS" ]; then
        title="🚨 [봇 감시] 이상이 계속됨"
    else
        exit 0
    fi
    if send "$title
$detail
매매와 손절이 멈췄을 수 있습니다. 서버에서 확인하세요: sudo docker compose ps -a / sudo docker compose logs --tail 50 bot"; then
        echo "$NOW" >"$alerting_file"
    fi
elif [ "$pending" = false ] && [ -f "$alerting_file" ]; then
    log "회복"
    if send "✅ [봇 감시] 회복: 컨테이너가 실행 중이고 모든 마켓의 캔들 처리가 정상입니다."; then
        rm -f "$alerting_file"
    fi
fi
