-- 매매 중단 기록 테이블 (db/schema.sql의 market_halt와 같아야 한다).
-- scripts/update.sh가 이미 있는 DB에 적용한다. 여러 번 적용해도 결과가 같다
CREATE TABLE IF NOT EXISTS market_halt (
    market_id int         PRIMARY KEY REFERENCES market (market_id),
    reason    text        NOT NULL,
    halted_at timestamptz NOT NULL,
    CONSTRAINT market_halt_reason_check CHECK (btrim(reason) <> '')
);
