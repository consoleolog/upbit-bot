from decimal import Decimal

import numpy as np
import structlog
import talib

from ..models import Candle, PortfolioState
from ..repositories import SignalRepository
from ..utils.constants import CandleType, MarketRegime
from .base_strategy import BaseStrategy
from .signal_aggregator import SignalAggregator

logger = structlog.get_logger(__name__)


class MacdRsiStochasticStrategy(BaseStrategy):
    """MACD·RSI·스토캐스틱 오실레이터를 조합해 매매 신호를 생성하는 전략.

    세 가지 모멘텀 지표를 병렬로 평가하고, 발생한 신호를 집계기에 누적한다.
    지원 국면은 UNKNOWN을 제외한 모든 국면이다.
    """

    def __init__(self, config: dict, aggregator: SignalAggregator, signal_repository: SignalRepository):
        """전략을 초기화한다.

        Args:
            config: 전략 설정 딕셔너리. 각 지표의 기간·임계값을 오버라이드할 수 있다.
            aggregator: 신호 집계기 인스턴스.
            signal_repository: 신호 저장소 인스턴스.
        """
        super().__init__(config, aggregator, signal_repository)

        self.macd_upper_period = config.get("macd_upper_period", 3)
        self.macd_mid_period = config.get("macd_mid_period", 5)
        self.macd_lower_period = config.get("macd_lower_period", 8)
        self.macd_signal_period = config.get("macd_signal_period", 9)

        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_signal_period = config.get("rsi_signal_period", 9)
        self.rsi_overbought = config.get("rsi_overbought", 75)
        self.rsi_oversold = config.get("rsi_oversold", 25)

        self.stoch_k_len = config.get("stoch_k_len", 10)
        self.stoch_k_smooth = config.get("stoch_k_smooth", 3)
        self.stoch_d_smooth = config.get("stoch_d_smooth", 3)
        self.stoch_overbought = config.get("stoch_overbought", 80)
        self.stoch_oversold = config.get("stoch_oversold", 20)

    async def evaluate(self, candles: list[Candle], regime: MarketRegime, portfolio: PortfolioState) -> None:
        """현재 시장 상황을 평가하고 MACD·RSI·스토캐스틱 신호를 집계기에 업데이트한다.

        Args:
            candles: 평가에 사용할 캔들 목록. 최소 1개 이상이어야 한다.
            regime: 현재 시장 국면.
            portfolio: 현재 포트폴리오 상태.
        """
        market = candles[-1].code
        candle_type = candles[-1].type

        logger.info(
            "strategy.evaluate.started",
            strategy=self.name,
            market=market,
            candle_type=candle_type.value,
            regime=regime.value,
            candle_count=len(candles),
        )

        macd = self.setup_macd(candles)
        await self.update_macd(macd, market, candle_type, regime)

        rsi = self.setup_rsi(candles)
        await self.update_rsi(rsi, market, candle_type, regime)

        stoch = self.setup_stoch(candles)
        await self.update_stoch(stoch, market, candle_type, regime)

        logger.info("strategy.evaluate.completed", strategy=self.name, market=market)

    def get_supported_regimes(self) -> list[MarketRegime]:
        """UNKNOWN을 제외한 모든 시장 국면을 반환한다.

        Returns:
            지원하는 :class:`MarketRegime` 목록.
        """
        return [
            MarketRegime.STABLE_BULL,
            MarketRegime.END_OF_BULL,
            MarketRegime.START_OF_BEAR,
            MarketRegime.STABLE_BEAR,
            MarketRegime.END_OF_BEAR,
            MarketRegime.START_OF_BULL,
        ]

    def setup_macd(self, candles: list[Candle]) -> dict[str, dict]:
        """단기·중기·장기 세 가지 MACD를 계산해 반환한다.

        각 밴드의 파라미터:
        - ``upper``: fast=macd_upper_period, slow=macd_mid_period
        - ``mid``:   fast=macd_upper_period, slow=macd_lower_period
        - ``lower``: fast=macd_mid_period,   slow=macd_lower_period

        Args:
            candles: 계산에 사용할 캔들 목록.

        Returns:
            ``upper``, ``mid``, ``lower`` 키를 가진 딕셔너리.
            각 값은 ``macd``, ``signal``, ``histogram`` 키를 포함한다.
        """
        macd_upper = self.calculate_macd(
            candles,
            fast_period=self.macd_upper_period,
            slow_period=self.macd_mid_period,
            signal_period=self.macd_signal_period,
        )
        macd_mid = self.calculate_macd(
            candles,
            fast_period=self.macd_upper_period,
            slow_period=self.macd_lower_period,
            signal_period=self.macd_signal_period,
        )
        macd_lower = self.calculate_macd(
            candles,
            fast_period=self.macd_mid_period,
            slow_period=self.macd_lower_period,
            signal_period=self.macd_signal_period,
        )

        return {"upper": macd_upper, "mid": macd_mid, "lower": macd_lower}

    async def update_macd(
        self, macds: dict[str, dict], market: str, candle_type: CandleType, regime: MarketRegime
    ) -> dict[str, object]:
        """세 밴드의 MACD 교차 신호를 감지하고 발생한 신호를 집계기에 추가한다.

        Args:
            macds: :meth:`setup_macd` 가 반환한 딕셔너리.
            market: 대상 마켓 코드.
            candle_type: 캔들 타입.
            regime: 현재 시장 국면.

        Returns:
            ``upper``, ``mid``, ``lower`` 키를 가진 교차 신호 딕셔너리.
            교차가 없는 밴드의 값은 ``None``.
        """
        macd_upper_crossover = await self.check_crossover(
            macds.get("upper").get("macd", np.array([])),
            macds.get("upper").get("signal", np.array([])),
            market=market,
            candle_type=candle_type,
            regime=regime,
            indicator_id="macd_upper",
        )
        if macd_upper_crossover is not None:
            self.aggregator.add_signal(macd_upper_crossover)

        macd_mid_crossover = await self.check_crossover(
            macds.get("mid").get("macd", np.array([])),
            macds.get("mid").get("signal", np.array([])),
            market=market,
            candle_type=candle_type,
            regime=regime,
            indicator_id="macd_mid",
        )
        if macd_mid_crossover is not None:
            self.aggregator.add_signal(macd_mid_crossover)

        macd_lower_crossover = await self.check_crossover(
            macds.get("lower").get("macd", np.array([])),
            macds.get("lower").get("signal", np.array([])),
            market=market,
            candle_type=candle_type,
            regime=regime,
            indicator_id="macd_lower",
        )
        if macd_lower_crossover is not None:
            self.aggregator.add_signal(macd_lower_crossover)

        return {"upper": macd_upper_crossover, "mid": macd_mid_crossover, "lower": macd_lower_crossover}

    def setup_rsi(self, candles: list[Candle]) -> dict[str, np.ndarray]:
        """RSI와 RSI 시그널 라인(EMA)을 계산해 반환한다.

        Args:
            candles: 계산에 사용할 캔들 목록.

        Returns:
            ``rsi``, ``signal`` 키를 가진 딕셔너리.
        """
        rsi = self.calculate_rsi(candles, self.rsi_period)
        rsi_signal = talib.EMA(rsi, self.rsi_signal_period)

        return {"rsi": rsi, "signal": rsi_signal}

    async def update_rsi(
        self, rsi: dict[str, np.ndarray], market: str, candle_type: CandleType, regime: MarketRegime
    ) -> dict[str, object]:
        """RSI 교차 및 레벨 돌파 신호를 감지한다.

        Args:
            rsi: :meth:`setup_rsi` 가 반환한 딕셔너리 (``rsi``, ``signal`` 키).
            market: 대상 마켓 코드.
            candle_type: 캔들 타입.
            regime: 현재 시장 국면.

        Returns:
            ``crossover``, ``level_break`` 키를 가진 신호 딕셔너리.
            신호가 없는 항목의 값은 ``None``.
        """
        rsi_crossover = await self.check_crossover(
            rsi.get("rsi", np.array([])),
            rsi.get("signal", np.array([])),
            market=market,
            candle_type=candle_type,
            regime=regime,
            indicator_id="rsi",
        )
        rsi_level_break = await self.check_level_break(
            Decimal(rsi.get("rsi", np.array([]))[-1]),
            overbought=self.rsi_overbought,
            oversold=self.rsi_oversold,
            market=market,
            candle_type=candle_type,
            regime=regime,
            indicator_id="rsi",
        )

        return {"crossover": rsi_crossover, "level_break": rsi_level_break}

    def setup_stoch(self, candles: list[Candle]) -> dict[str, np.ndarray]:
        """스토캐스틱 오실레이터(Slow %K, Slow %D)를 계산해 반환한다.

        Args:
            candles: 계산에 사용할 캔들 목록.

        Returns:
            ``k_slow``, ``d_slow`` 키를 가진 딕셔너리.
        """
        stoch = self.calculate_stoch(
            candles, k_len=self.stoch_k_len, k_smooth=self.stoch_k_smooth, d_smooth=self.stoch_d_smooth
        )

        return stoch

    async def update_stoch(
        self, stoch: dict[str, np.ndarray], market: str, candle_type: CandleType, regime: MarketRegime
    ) -> None:
        """스토캐스틱 교차 및 레벨 돌파 신호를 감지하고 집계기에 추가한다.

        %K와 %D의 교차 신호 1개, %K·%D 각각의 레벨 돌파 신호 최대 2개를 생성할 수 있다.

        Args:
            stoch: :meth:`setup_stoch` 가 반환한 딕셔너리 (``k_slow``, ``d_slow`` 키).
            market: 대상 마켓 코드.
            candle_type: 캔들 타입.
            regime: 현재 시장 국면.
        """
        stoch_crossover = await self.check_crossover(
            stoch.get("k_slow", np.array([])),
            stoch.get("d_slow", np.array([])),
            market=market,
            candle_type=candle_type,
            regime=regime,
            indicator_id="stoch",
        )
        if stoch_crossover is not None:
            self.aggregator.add_signal(stoch_crossover)

        stoch_k_slow_level_break = await self.check_level_break(
            Decimal(stoch.get("k_slow", np.array([]))[-1]),
            overbought=self.stoch_overbought,
            oversold=self.stoch_oversold,
            market=market,
            candle_type=candle_type,
            regime=regime,
            indicator_id="stoch_k_slow",
        )
        if stoch_k_slow_level_break is not None:
            self.aggregator.add_signal(stoch_k_slow_level_break)

        stoch_d_slow_level_break = await self.check_level_break(
            Decimal(stoch.get("d_slow", np.array([]))[-1]),
            overbought=self.stoch_overbought,
            oversold=self.stoch_oversold,
            market=market,
            candle_type=candle_type,
            regime=regime,
            indicator_id="stoch_d_slow",
        )
        if stoch_d_slow_level_break is not None:
            self.aggregator.add_signal(stoch_d_slow_level_break)
