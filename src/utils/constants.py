from enum import Enum

# =============================================================================
# Candle Enum
# =============================================================================


class CandleType(Enum):
    """
    업비트 캔들 종류 (WebSocket 응답의 type 필드 대응)

    Attributes:
        SECOND: 초봉 (candle.1s)
        MINUTE_1: 1분봉 (candle.1m)
        MINUTE_3: 3분봉 (candle.3m)
        MINUTE_5: 5분봉 (candle.5m)
        MINUTE_10: 10분봉 (candle.10m)
        MINUTE_15: 15분봉 (candle.15m)
        HALF_HOUR: 30분봉 (candle.30m)
        HOUR: 60분봉 (candle.60m)
        HOUR_4: 240분봉 (candle.240m)
    """

    SECOND = "candle.1s"
    MINUTE_1 = "candle.1m"
    MINUTE_3 = "candle.3m"
    MINUTE_5 = "candle.5m"
    MINUTE_10 = "candle.10m"
    MINUTE_15 = "candle.15m"
    HALF_HOUR = "candle.30m"
    HOUR = "candle.60m"
    HOUR_4 = "candle.240m"


class Unit(Enum):
    """
    분(Minute) 캔들 단위
    캔들 단위를 지정하여 캔들 조회를 할 수 있습니다.
    최대 240분(4시간) 캔들까지 조회할 수 있습니다.

    Attributes:
        MINUTE_1: 1분봉
        MINUTE_3: 3분봉
        MINUTE_5: 5분봉
        MINUTE_10: 10분봉
        MINUTE_15: 15분봉
        HALF_HOUR: 30분봉
        HOUR: 60분봉 (1시간)
        HOUR_4: 240분봉 (4시간)
    """

    MINUTE_1 = 1
    MINUTE_3 = 3
    MINUTE_5 = 5
    MINUTE_10 = 10
    MINUTE_15 = 15
    HALF_HOUR = 30
    HOUR = 60
    HOUR_4 = 240


class Timeframe(Enum):
    """
    업비트 REST API 캔들 조회 엔드포인트 경로에 사용하는 시간 단위

    Attributes:
        SECOND: 초봉 (seconds)
        MINUTE_1: 1분봉 (minutes/1)
        MINUTE_3: 3분봉 (minutes/3)
        MINUTE_5: 5분봉 (minutes/5)
        MINUTE_10: 10분봉 (minutes/10)
        MINUTE_15: 15분봉 (minutes/15)
        HALF_HOUR: 30분봉 (minutes/30)
        HOUR: 60분봉 (minutes/60)
        HOUR_4: 240분봉 (minutes/240)
        DAY: 일봉 (days)
        WEEK: 주봉 (weeks)
        MONTH: 월봉 (months)
        YEAR: 연봉 (years)
    """

    SECOND = "seconds"

    MINUTE_1 = f"minutes/{Unit.MINUTE_1.value}"
    MINUTE_3 = f"minutes/{Unit.MINUTE_3.value}"
    MINUTE_5 = f"minutes/{Unit.MINUTE_5.value}"
    MINUTE_10 = f"minutes/{Unit.MINUTE_10.value}"
    MINUTE_15 = f"minutes/{Unit.MINUTE_15.value}"

    HALF_HOUR = f"minutes/{Unit.HALF_HOUR.value}"
    HOUR = f"minutes/{Unit.HOUR.value}"
    HOUR_4 = f"minutes/{Unit.HOUR_4.value}"

    DAY = "days"
    WEEK = "weeks"
    MONTH = "months"
    YEAR = "years"


# =============================================================================
# Ticker Enum
# =============================================================================


class ChangeDirection(Enum):
    """
    전일 종가 대비 가격 변동 방향

    Attributes:
        RISE: 상승
        EVEN: 보합
        FALL: 하락
    """

    RISE = "RISE"
    EVEN = "EVEN"
    FALL = "FALL"


class MarketState(Enum):
    """
    거래 상태

    Attributes:
        PREVIEW: 입금지원
        ACTIVE: 거래지원가능
        DELISTED: 거래지원종료
    """

    PREVIEW = "PREVIEW"
    ACTIVE = "ACTIVE"
    DELISTED = "DELISTED"


class AskBid(Enum):
    """
    매수/매도 구분

    Attributes:
        ASK: 매도
        BID: 매수
    """

    ASK = "ASK"
    BID = "BID"


class StreamType(Enum):
    """
    스트림 타입

    Attributes:
        SNAPSHOT: 스냅샷
        REALTIME: 실시간
    """

    SNAPSHOT = "SNAPSHOT"
    REALTIME = "REALTIME"


# =============================================================================
# Order Enum
# =============================================================================


class OrderSide(Enum):
    """
    주문 종류

    Attributes:
        BID: 매수
        ASK: 매도
    """

    BID = "bid"
    ASK = "ask"


class OrderType(Enum):
    """
    주문 유형

    Attributes:
        LIMIT: 지정가 매수/매도 주문
        PRICE: 시장가 매수 주문
        MARKET: 시장가 매도 주문
        BEST: 최유리 지정가 매수/매도 주문 (time_in_force 필드 설정 필수)
    """

    LIMIT = "limit"
    PRICE = "price"
    MARKET = "market"
    BEST = "best"


class OrderState(Enum):
    """
    주문 상태

    Attributes:
        WAIT: 체결 대기
        WATCH: 예약 주문 대기
        DONE: 체결 완료
        CANCEL: 주문 취소
    """

    WAIT = "wait"
    WATCH = "watch"
    DONE = "done"
    CANCEL = "cancel"


class TimeInForce(Enum):
    """
    주문 체결 옵션

    Attributes:
        IOC: 지정가 조건으로 체결 가능한 수량만 즉시 부분 체결하고, 잔여 수량은 취소
        FOK: 지정가 조건으로 주문량 전량 체결 가능할 때만 주문을 실행하고, 아닌 경우 전량 주문 취소
        POST_ONLY: 지정가 조건으로 부분 또는 전체에 대해 즉시 체결 가능한 상황인 경우 주문을 실행하지 않고 취소
    """

    IOC = "ioc"
    FOK = "fok"
    POST_ONLY = "post_only"


class SmpType(Enum):
    """
    자전거래 체결 방지 모드

    Attributes:
        CANCEL_MAKER: 메이커 주문(이전 주문)을 취소
        CANCEL_TAKER: 테이커 주문(신규 주문)을 취소
        REDUCE: 기존 주문과 신규 주문의 주문 수량을 줄여 체결을 방지. 잔량이 0인 경우 주문을 취소
    """

    CANCEL_MAKER = "cancel_maker"
    CANCEL_TAKER = "cancel_taker"
    REDUCE = "reduce"


# =============================================================================
# Signal Enum
# =============================================================================


class SignalType(Enum):
    """
    시그널 종류

    Attributes:
        CROSS_OVER: 두 지표선이 교차할 때 발생 (예: MA 골든/데드 크로스)
        THRESHOLD_CROSS: 지표가 0선을 교차할 때 발생 (예: MACD 0선 교차)
        LEVEL_BREAK: 지표 또는 가격이 특정 레벨을 돌파할 때 발생 (예: RSI 70/30, 지지/저항선)
    """

    CROSS_OVER = "cross_over"
    THRESHOLD_CROSS = "threshold_cross"
    LEVEL_BREAK = "level_break"


class SignalValue(Enum):
    """
    시그널 값 (방향 및 강도)

    Attributes:
        GOLDEN_CROSS: 골든 크로스 — 단기선이 장기선을 상향 돌파 (CROSS_OVER)
        DEAD_CROSS: 데드 크로스 — 단기선이 장기선을 하향 돌파 (CROSS_OVER)
        OVER_BOUGHT: 과매수 — 지표가 상단 레벨 초과 (LEVEL_BREAK)
        OVER_SOLD: 과매도 — 지표가 하단 레벨 미만 (LEVEL_BREAK)
    """

    GOLDEN_CROSS = "golden_cross"
    DEAD_CROSS = "dead_cross"
    OVER_BOUGHT = "over_bought"
    OVER_SOLD = "over_sold"


class SignalDirection(Enum):
    """
    트레이딩 신호의 포지션 방향

    Attributes:
        LONG: 매수 진입 — 상승 예상 시 롱 포지션 진입
        SHORT: 매도 진입 — 하락 예상 시 숏 포지션 진입 (선물/공매도)
        CLOSE: 포지션 종료 — 보유 중인 포지션 청산
        HOLD: 유지 — 현재 포지션 또는 대기 상태 유지
    """

    LONG = "long"
    SHORT = "short"
    CLOSE = "close"
    HOLD = "hold"


# =============================================================================
# Decision Enum
# =============================================================================


class DecisionState(Enum):
    """
    거래 결정의 처리 상태

    Attributes:
        PENDING: 대기 중 — 생성되었으나 아직 승인/거부되지 않은 상태
        APPROVED: 승인됨 — 실행 조건을 충족하여 주문 실행 대기 중
        REJECTED: 거부됨 — 리스크 검증 등의 이유로 실행이 거부된 상태
        EXECUTED: 실행됨 — 주문이 실제로 제출된 상태
        CANCELLED: 취소됨 — 승인 후 실행 전에 취소된 상태
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"


# =============================================================================
# ExecutionResult Enum
# =============================================================================


class ExecutionState(Enum):
    """
    주문 체결 실행 상태

    Attributes:
        FILLED: 전량 체결 완료
        PARTIALLY_FILLED: 부분 체결 — 주문 수량 중 일부만 체결된 상태
        PENDING: 대기 중 — 체결 결과가 아직 확인되지 않은 상태
        CANCELLED: 취소됨 — 체결 전 주문이 취소된 상태
        REJECTED: 거부됨 — 거래소에 의해 주문이 거부된 상태
        FAILED: 실패 — 시스템 오류 등으로 실행에 실패한 상태
    """

    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    PENDING = "PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


# =============================================================================
# Strategy Enum
# =============================================================================


class MarketRegime(Enum):
    """
    이동평균선(단기/중기/장기) 배열로 판단하는 시장 국면

    Attributes:
        STABLE_BULL: 안정 상승장 — short > middle > long (정배열 완성)
        END_OF_BULL: 상승장 말기 — middle > short > long (단기선 꺾임)
        START_OF_BEAR: 하락장 초기 — middle > long > short (단기선 장기선 하향 이탈)
        STABLE_BEAR: 안정 하락장 — long > middle > short (역배열 완성)
        END_OF_BEAR: 하락장 말기 — long > short > middle (단기선 반등)
        START_OF_BULL: 상승장 초기 — short > long > middle (단기선 장기선 상향 돌파)
        UNKNOWN: 판단 불가 — 데이터 부족 또는 정의되지 않은 배열
    """

    STABLE_BULL = 1  # short > middle > long
    END_OF_BULL = 2  # middle > short > long
    START_OF_BEAR = 3  # middle > long > short
    STABLE_BEAR = 4  # long > middle > short
    END_OF_BEAR = 5  # long > short > middle
    START_OF_BULL = 6  # short > long > middle
    UNKNOWN = 0


# =============================================================================
# Risk Enum
# =============================================================================


class RiskDecision(Enum):
    """
    리스크 검증 결과에 따른 거래 허용 결정

    Attributes:
        ALLOW: 거래 진행 허용
        REDUCE_SIZE: 축소된 수량으로 거래 허용
        FORCE_NO_ACTION: 거래 차단
        EMERGENCY_STOP: 모든 포지션 즉시 청산
    """

    ALLOW = "ALLOW"
    REDUCE_SIZE = "REDUCE_SIZE"
    FORCE_NO_ACTION = "FORCE_NO_ACTION"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class RiskSeverity(Enum):
    """
    리스크 조건의 심각도

    Attributes:
        INFO: 정보성 — 정상 범위 내 모니터링
        WARNING: 경고 — 주의 필요
        CRITICAL: 위험 — 즉각적인 조치 필요
        EMERGENCY: 비상 — 시스템 전체 위기
    """

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"
