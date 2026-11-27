"""scripts/watchdog.sh 시험. 임시 폴더와 가짜 docker, curl로 서버의 감시 상황을 재현한다."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "watchdog.sh"
WEBHOOK = "https://discord.com/api/webhooks/123/secret-token-abc"
T0 = 1_800_000_000
HEALTHY = "KRW-BTC|35\nKRW-ETH|35\nKRW-SOL|35"
MARKETS_LINE = re.compile(r'^( *"markets": *)\[.*\](,? *)$', re.MULTILINE)


def _config_with_three_markets() -> str:
    """저장소 설정 파일 형식 그대로, 마켓만 시험용 세 개로 바꾼다 (실거래 마켓이 바뀌어도 시험이 흔들리지 않게)."""
    text = (ROOT / "config" / "trading.json").read_text(encoding="utf-8")
    replaced, count = MARKETS_LINE.subn(r'\1["KRW-BTC", "KRW-ETH", "KRW-SOL"]\2', text)
    assert count == 1, "markets line not found in config/trading.json"
    return replaced


pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("bash", "flock")), reason="bash, flock 필요"
)

FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$CALLS"
case "$1 $2" in
    "compose ps") [ "${FAKE_PS_FAILS:-0}" = 1 ] && exit 1; printf '%b\\n' "${FAKE_RUNNING-db\\nbot}" ;;
    "compose exec")
        cat > /dev/null
        [ "${FAKE_QUERY_FAILS:-0}" = 1 ] && exit 2
        printf '%b\\n' "$FAKE_ROWS"
        ;;
esac
exit 0
"""

FAKE_CURL = """#!/usr/bin/env bash
# 받은 설정(-K)과 본문을 기록한다. 명령줄 인자도 기록해 URL이 인자로 넘어가지 않는지 본다
echo "$*" >> "$CALLS.curl_args"
while [ $# -gt 0 ]; do
    [ "$1" = -K ] && cat "$2" >> "$CALLS.curl_config"
    shift
done
cat >> "$CALLS.sent"
echo >> "$CALLS.sent"
[ "${FAKE_CURL_FAILS:-0}" = 1 ] && exit 7
printf '%s' "${FAKE_HTTP:-204}"
"""


class Server:
    """서버 저장소처럼 scripts/watchdog.sh, .env, config/trading.json을 둔 폴더."""

    def __init__(self, tmp_path: Path) -> None:
        self.repo = tmp_path / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "config").mkdir()
        shutil.copy(SCRIPT, self.repo / "scripts" / "watchdog.sh")
        (self.repo / "config" / "trading.json").write_text(_config_with_three_markets(), encoding="utf-8")
        (self.repo / ".env").write_text(f"DB_NAME=x\nDISCORD_WEBHOOK_URL={WEBHOOK}\n", encoding="utf-8")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL), ("logger", "#!/usr/bin/env bash\n")):
            (bin_dir / name).write_text(body, encoding="utf-8")
            (bin_dir / name).chmod(0o755)
        self.calls = tmp_path / "calls"
        self.calls.touch()
        self.state = tmp_path / "state"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(self.calls),
            "WATCHDOG_STATE_DIR": str(self.state),
            "FAKE_ROWS": HEALTHY,
        }

    def run(self, now: int = T0, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "scripts/watchdog.sh"],
            cwd=self.repo,
            env={**self.env, "WATCHDOG_NOW": str(now), **env},
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _read(self, suffix: str) -> str:
        path = self.calls.with_name(self.calls.name + suffix)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def sent(self) -> list[str]:
        return [json.loads(line)["content"] for line in self._read(".sent").splitlines() if line]

    def payloads(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self._read(".sent").splitlines() if line]

    def docker_calls(self) -> list[str]:
        return self.calls.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def server(tmp_path: Path) -> Server:
    return Server(tmp_path)


def test_healthy_sends_nothing(server: Server) -> None:
    result = server.run()

    assert result.returncode == 0, result.stderr
    assert server.sent() == []
    # 설정의 마켓으로 DB를 조회한다
    query = next(call for call in server.docker_calls() if call.startswith("compose exec"))
    assert query.endswith("sh KRW-BTC,KRW-ETH,KRW-SOL")


def test_reads_markets_from_repository_config(server: Server) -> None:
    # 서버의 실제 설정 파일 형식에서도 마켓을 모두 읽는다
    shutil.copy(ROOT / "config" / "trading.json", server.repo / "config" / "trading.json")
    markets = json.loads((ROOT / "config" / "trading.json").read_text(encoding="utf-8"))["markets"]

    server.run(FAKE_ROWS="\n".join(f"{market}|35" for market in markets))

    query = next(call for call in server.docker_calls() if call.startswith("compose exec"))
    assert query.endswith("sh " + ",".join(markets))
    assert server.sent() == []


def test_container_down_once_is_ignored_twice_alerts(server: Server) -> None:
    # 배포 중 잠깐 멈추는 것은 넘긴다
    assert server.run(FAKE_RUNNING="db").returncode == 0
    assert server.sent() == []

    assert server.run(T0 + 300, FAKE_RUNNING="db").returncode == 0

    [message] = server.sent()
    assert message.startswith("🚨 [봇 감시] 이상 감지\n- 컨테이너가 실행 중이 아님: bot")


def test_container_back_between_checks_resets_count(server: Server) -> None:
    server.run(FAKE_RUNNING="db")
    server.run(T0 + 300)
    server.run(T0 + 600, FAKE_RUNNING="db")

    assert server.sent() == []


def test_db_down_skips_query(server: Server) -> None:
    server.run(FAKE_RUNNING="bot")
    server.run(T0 + 300, FAKE_RUNNING="bot")

    assert server.sent()[0].startswith("🚨 [봇 감시] 이상 감지\n- 컨테이너가 실행 중이 아님: db")
    assert not any(call.startswith("compose exec") for call in server.docker_calls())


def test_stale_market_alerts(server: Server) -> None:
    # 30분봉: 캔들 시작 뒤 75분(마감 뒤 45분)까지는 정상, 넘으면 이상
    assert server.run(FAKE_ROWS="KRW-BTC|75\nKRW-ETH|35\nKRW-SOL|35").returncode == 0
    assert server.sent() == []

    server.run(FAKE_ROWS="KRW-BTC|76\nKRW-ETH|35\nKRW-SOL|-1")

    [message] = server.sent()
    assert "- KRW-BTC 마지막 캔들 처리 46분 전" in message
    assert "- KRW-SOL 처리 기록 없음" in message
    assert "KRW-ETH" not in message


def test_query_failure_alerts(server: Server) -> None:
    server.run(FAKE_QUERY_FAILS="1")

    assert "- DB에서 캔들 처리 기록을 조회하지 못함" in server.sent()[0]


def test_docker_failure_alerts(server: Server) -> None:
    server.run(FAKE_PS_FAILS="1")

    assert "- docker compose 상태를 확인하지 못함" in server.sent()[0]


def test_alert_is_not_repeated_until_remind_interval(server: Server) -> None:
    stale = "KRW-BTC|200\nKRW-ETH|35\nKRW-SOL|35"
    server.run(FAKE_ROWS=stale)
    server.run(T0 + 300, FAKE_ROWS=stale)
    server.run(T0 + 6 * 3600 - 1, FAKE_ROWS=stale)
    assert len(server.sent()) == 1

    server.run(T0 + 6 * 3600, FAKE_ROWS=stale)

    assert len(server.sent()) == 2
    assert server.sent()[1].startswith("🚨 [봇 감시] 이상이 계속됨")


def test_recovery_is_sent_once(server: Server) -> None:
    server.run(FAKE_ROWS="KRW-BTC|200\nKRW-ETH|35\nKRW-SOL|35")
    server.run(T0 + 300)
    server.run(T0 + 600)

    assert len(server.sent()) == 2
    assert server.sent()[1].startswith("✅ [봇 감시] 회복")


@pytest.mark.parametrize("failure", [{"FAKE_CURL_FAILS": "1"}, {"FAKE_HTTP": "500"}])
def test_send_failure_is_retried_next_run(server: Server, failure: dict[str, str]) -> None:
    stale = "KRW-BTC|200\nKRW-ETH|35\nKRW-SOL|35"
    result = server.run(FAKE_ROWS=stale, **failure)
    assert "알림을 보내지 못함" in result.stdout

    server.run(T0 + 300, FAKE_ROWS=stale)

    # 보내지 못했으면 이상 시작 알림을 다시 보낸다
    assert [m.split("\n")[0] for m in server.sent()] == ["🚨 [봇 감시] 이상 감지"] * 2


def test_webhook_url_is_never_exposed(server: Server) -> None:
    result = server.run(FAKE_ROWS="KRW-BTC|200\nKRW-ETH|35\nKRW-SOL|35", FAKE_HTTP="500")

    # URL은 설정(-K)으로만 넘기고, 명령줄 인자·출력에 남기지 않는다
    assert server._read(".curl_config") == f'url = "{WEBHOOK}"\n'
    assert "secret-token-abc" not in server._read(".curl_args")
    assert "secret-token-abc" not in result.stdout + result.stderr
    # 멘션이 섞여도 울리지 않게 한다
    assert server.payloads()[0]["allowed_mentions"] == {"parse": []}


@pytest.mark.parametrize("line", ["", "DISCORD_WEBHOOK_URL=", "DISCORD_WEBHOOK_URL=https://example.com/api/webhooks/1"])
def test_missing_or_invalid_webhook_is_logged(server: Server, line: str) -> None:
    (server.repo / ".env").write_text(f"DB_NAME=x\n{line}\n", encoding="utf-8")

    result = server.run(FAKE_ROWS="KRW-BTC|200\nKRW-ETH|35\nKRW-SOL|35")

    assert result.returncode == 0
    assert "DISCORD_WEBHOOK_URL이 없거나" in result.stdout
    assert server.sent() == []


def test_quoted_webhook_url_is_accepted(server: Server) -> None:
    (server.repo / ".env").write_text(f'DISCORD_WEBHOOK_URL="{WEBHOOK}"\r\n', encoding="utf-8")

    server.run(FAKE_ROWS="KRW-BTC|200\nKRW-ETH|35\nKRW-SOL|35")

    assert server._read(".curl_config") == f'url = "{WEBHOOK}"\n'


def test_unreadable_config_alerts(server: Server) -> None:
    (server.repo / "config" / "trading.json").write_text('{"markets": "KRW-BTC; DROP TABLE x"}', encoding="utf-8")

    server.run()

    assert "- 설정(config/trading.json)에서 캔들 주기나 마켓을 읽지 못해" in server.sent()[0]
    # 형식을 확인하지 못한 값으로는 DB를 조회하지 않는다
    assert not any(call.startswith("compose exec") for call in server.docker_calls())


def test_concurrent_run_exits_quietly(server: Server) -> None:
    # 앞 실행이 잠금을 쥐고 있으면 겹쳐 돌지 않고 바로 끝난다
    server.state.mkdir()
    holder = subprocess.Popen(["flock", "-x", str(server.state / "lock"), "sleep", "5"])
    try:
        subprocess.run(["sleep", "0.5"], check=True)
        result = server.run(FAKE_ROWS="KRW-BTC|200\nKRW-ETH|35\nKRW-SOL|35")
    finally:
        holder.kill()
        holder.wait()

    assert result.returncode == 0
    assert server.docker_calls() == []


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ("", "DB 조회 결과의 마켓 수가 설정과 다름 (0/3)"),
        ("KRW-BTC|35\nKRW-ETH|35", "DB 조회 결과의 마켓 수가 설정과 다름 (2/3)"),
        ("KRW-BTC|35\nKRW-ETH|35\nKRW-SOL|NOTICE", "DB 조회 결과를 해석하지 못함"),
    ],
    ids=["empty", "missing-market", "not-a-number"],
)
def test_unexpected_query_result_is_not_treated_as_healthy(server: Server, rows: str, expected: str) -> None:
    server.run(FAKE_ROWS=rows)

    assert f"- {expected}" in server.sent()[0]


def test_no_recovery_while_container_down_is_pending(server: Server) -> None:
    server.run(FAKE_ROWS="KRW-BTC|200\nKRW-ETH|35\nKRW-SOL|35")
    # 처리 기록은 정상이 됐지만 봇이 한 번 꺼진 것을 봤다. 아직 정상이 아니므로 회복을 알리지 않는다
    server.run(T0 + 300, FAKE_RUNNING="db")
    assert len(server.sent()) == 1

    server.run(T0 + 600, FAKE_RUNNING="db")

    assert len(server.sent()) == 1  # 이상 중이므로 6시간 전에는 다시 알리지 않음
    server.run(T0 + 900)
    assert server.sent()[1].startswith("✅ [봇 감시] 회복")
