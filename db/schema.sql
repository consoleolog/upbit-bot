-- upbit-bot 스키마. 지금까지 구현한 기능(마켓별 캔들 마감 때 보조지표 계산과 신호 판정)에 필요한 테이블만 둔다.

-- 거래 대상 마켓
CREATE TABLE market (
    market_id      int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    exchange       varchar(20) NOT NULL DEFAULT 'upbit',
    symbol         varchar(20) NOT NULL,
    base_currency  varchar(10) NOT NULL,
    quote_currency varchar(10) NOT NULL,
    is_active      boolean     NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (exchange, symbol),
    CHECK (quote_currency IN ('KRW', 'BTC', 'USDT')),
    -- 마켓 코드와 통화 컬럼이 어긋나지 않게 한다
    CHECK (symbol = quote_currency || '-' || base_currency)
);

-- 마켓 × 지표 타입별 최신 값과 신호 상태. 값이 아직 없으면(캔들 부족) value, signal은 NULL이다.
-- 신호 상태는 마지막으로 발생한 이벤트이고, *_time은 그 이벤트가 발생한 캔들의 시작 시각이다 (매매 로직 4).
CREATE TABLE indicator_info (
    market_id      int         NOT NULL REFERENCES market (market_id),
    indicator_type varchar(20) NOT NULL,
    value          double precision,
    signal         double precision,
    candle_time    timestamptz NOT NULL,
    over_state     varchar(20),
    over_time      timestamptz,
    cross_state    varchar(20),
    cross_time     timestamptz,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (market_id, indicator_type),
    CHECK (indicator_type IN ('RSI', 'STOCHASTIC', 'MACD_SHORT', 'MACD_MID', 'MACD_LONG')),
    CHECK (over_state IN ('OVER_BOUGHT', 'OVER_SOLD')),
    CHECK (cross_state IN ('GOLDEN_CROSS', 'DEAD_CROSS')),
    -- 상태와 발생 캔들은 함께 있거나 함께 없다
    CHECK ((over_state IS NULL) = (over_time IS NULL)),
    CHECK ((cross_state IS NULL) = (cross_time IS NULL)),
    -- 신호는 지표를 계산한 캔들보다 뒤에 발생할 수 없다
    CHECK (over_time <= candle_time),
    CHECK (cross_time <= candle_time),
    -- MACD에는 과매수/과매도가 없다
    CHECK (over_state IS NULL OR indicator_type IN ('RSI', 'STOCHASTIC'))
);

-- 모의 포지션. 실제 주문 없이 매매 판단을 기록해 판단이 맞는지 검증한다.
-- 체결은 판단한 캔들의 종가로 가정하고, 평단가는 매수 수수료를 포함한다 (매매 로직 5.2).
CREATE TABLE paper_position (
    position_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    market_id       int         NOT NULL REFERENCES market (market_id),
    status          varchar(10) NOT NULL DEFAULT 'OPEN',
    entry_time      timestamptz NOT NULL,
    entry_price     numeric     NOT NULL,
    average_price   numeric     NOT NULL,
    -- 매수할 때의 매도 기준(%). 보유 중에 설정을 바꿔도 이 포지션은 이 값을 끝까지 쓴다 (매매 로직 2.3)
    take_profit_rate numeric    NOT NULL,
    stop_loss_rate  numeric     NOT NULL,
    min_profit_rate numeric     NOT NULL,
    exit_time       timestamptz,
    exit_price      numeric,
    exit_reason     varchar(20),
    net_profit_rate numeric,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('OPEN', 'CLOSED')),
    CHECK (entry_price > 0 AND average_price >= entry_price),
    CHECK (take_profit_rate > 0 AND stop_loss_rate > 0 AND min_profit_rate >= 0),
    CHECK (exit_reason IN ('STOP_LOSS', 'TAKE_PROFIT', 'OVERBOUGHT_EXIT', 'DEAD_CROSS_EXIT')),
    -- 열린 포지션은 매도 정보가 모두 없고, 닫힌 포지션은 모두 있다
    CHECK (
        (status = 'OPEN' AND exit_time IS NULL AND exit_price IS NULL AND exit_reason IS NULL AND net_profit_rate IS NULL)
        OR (status = 'CLOSED' AND exit_time IS NOT NULL AND exit_price > 0 AND exit_reason IS NOT NULL
            AND net_profit_rate IS NOT NULL)
    ),
    -- 매도는 매수한 캔들보다 뒤의 캔들에서만 한다
    CHECK (exit_time > entry_time)
);

-- 마켓당 열린 포지션은 하나 (매매 로직 5.1). 같은 마켓을 두 번 사는 것을 DB에서 막는다
CREATE UNIQUE INDEX paper_position_open_market ON paper_position (market_id) WHERE status = 'OPEN';

CREATE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER indicator_info_updated_at
    BEFORE UPDATE ON indicator_info
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
