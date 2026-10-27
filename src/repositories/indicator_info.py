import asyncpg

from src.repositories.models import IndicatorInfo, IndicatorType


class IndicatorInfoRepository:
    async def list_by_market(self, conn: asyncpg.Connection, market_id: int) -> dict[IndicatorType, IndicatorInfo]:
        """마켓의 지표 타입별 최신 상태. 아직 저장한 적 없는 타입은 들어 있지 않다."""
        rows = await conn.fetch("SELECT * FROM indicator_info WHERE market_id = $1", market_id)
        infos = [IndicatorInfo.from_record(row) for row in rows]
        return {info.indicator_type: info for info in infos}

    async def save(self, conn: asyncpg.Connection, info: IndicatorInfo) -> IndicatorInfo | None:
        """최신 상태를 저장한다. 이미 같거나 더 새로운 캔들로 저장돼 있으면 바꾸지 않고 None.

        같은 캔들 마감이 두 번 처리되거나 늦게 도착한 처리가 새 상태를 덮어쓰지 않게 한다.
        """
        row = await conn.fetchrow(
            """
            INSERT INTO indicator_info AS current (
                market_id, indicator_type, value, signal, candle_time,
                over_state, over_time, cross_state, cross_time
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (market_id, indicator_type) DO UPDATE SET
                value = EXCLUDED.value,
                signal = EXCLUDED.signal,
                candle_time = EXCLUDED.candle_time,
                over_state = EXCLUDED.over_state,
                over_time = EXCLUDED.over_time,
                cross_state = EXCLUDED.cross_state,
                cross_time = EXCLUDED.cross_time
            WHERE current.candle_time < EXCLUDED.candle_time
            RETURNING *
            """,
            info.market_id,
            info.indicator_type,
            info.value,
            info.signal,
            info.candle_time,
            info.over_state,
            info.over_time,
            info.cross_state,
            info.cross_time,
        )
        return None if row is None else IndicatorInfo.from_record(row)
