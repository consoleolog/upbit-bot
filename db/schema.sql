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
