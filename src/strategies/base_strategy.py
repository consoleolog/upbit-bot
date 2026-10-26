from abc import ABC, abstractmethod
from decimal import Decimal

import numpy as np
import structlog
import talib
from talib import MA_Type

from ..models import Candle, PortfolioState, Signal
from ..repositories import SignalRepository
from ..utils.constants import CandleType, MarketRegime, SignalDirection, SignalType, SignalValue
from .signal_aggregator import SignalAggregator

logger = structlog.get_logger(__name__)


class BaseStrategy(ABC):
    """트레이딩 전략의 추상 기반 클래스.

    모든 전략은 이 클래스를 상속하고 :meth:`evaluate` 와
    :meth:`get_supported_regimes` 를 구현해야 한다.

    Attributes:
        name: 전략 식별자. ``config["strategy_id"]`` 가 없으면 클래스명을 사용한다.
        aggregator: 신호 집계기. 여러 지표 신호를 하나의 판단으로 합산한다.
        signal_repository: 신호 저장소. 생성된 신호를 영속화하는 데 사용된다.
    """

    def __init__(self, config: dict, aggregator: SignalAggregator, signal_repository: SignalRepository):
        """전략을 초기화한다.

        Args:
            config: 전략 설정 딕셔너리. ``strategy_id`` 키로 전략명을 지정할 수 있다.
            aggregator: 신호 집계기 인스턴스.
            signal_repository: 신호 저장소 인스턴스.
        """
        self.name = config.get("strategy_id", self.__class__.__name__)
        self.aggregator = aggregator
        self.signal_repository = signal_repository

    # ========================================================================
    # ABSTRACT METHODS
    # ========================================================================

    @abstractmethod
    async def evaluate(self, candles: list[Candle], regime: MarketRegime, portfolio: PortfolioState) -> None:
        """현재 시장 상황을 평가한다.

        Args:
            candles: 평가에 사용할 캔들 목록.
            regime: 현재 시장 국면.
            portfolio: 현재 포트폴리오 상태.
        """
        raise NotImplementedError()

    @abstractmethod
    def get_supported_regimes(self) -> list[MarketRegime]:
        """이 전략이 동작하는 시장 국면 목록을 반환한다.

        Returns:
            전략이 지원하는 :class:`MarketRegime` 목록.
        """
        raise NotImplementedError()

    # ========================================================================
    # PUBLIC METHODS
    # ========================================================================

    def should_run(self, regime: MarketRegime) -> bool:
        """현재 시장 국면에서 이 전략을 실행해야 하는지 판단한다.

        Args:
            regime: 현재 시장 국면.

        Returns:
            지원 국면 목록에 포함되면 ``True``, 아니면 ``False``.
        """
        return regime in self.get_supported_regimes()

    def create_signal(
        self,
        indicator_id: str,
        signal_type: SignalType,
        value: SignalValue,
        direction: SignalDirection,
        regime: MarketRegime,
        market: str,
        candle_type: CandleType,
        metadata: dict,
    ) -> Signal:
        """Signal 객체를 생성해 반환한다.

        Args:
            indicator_id: 신호를 생성한 지표 식별자.
            signal_type: 신호 종류.
            value: 신호 값.
            direction: 포지션 방향.
            regime: 신호 생성 시점의 시장 국면.
            market: 대상 마켓 코드 (예: KRW-BTC).
            candle_type: 캔들 타입 (타임프레임 결정에 사용).
            metadata: 추가 분석 데이터.

        Returns:
            생성된 :class:`Signal` 인스턴스.
        """
        return Signal(
            strategy_id=self.name,
            indicator_id=indicator_id,
            type=signal_type,
            value=value,
            direction=direction,
            regime=regime,
            market=market,
            timeframe=candle_type.value,
            metadata=metadata,
        )

    async def check_crossover(
        self,
        one_values: np.ndarray,
        two_values: np.ndarray,
        market: str,
        candle_type: CandleType,
        indicator_id: str,
        regime: MarketRegime,
    ) -> Signal | None:
        """두 값 시리즈 간의 교차(crossover)를 감지하고 신호를 저장한다.

        이전 값과 현재 값을 비교해 골든 크로스(``one``이 ``two``를 상향 돌파) 또는
        데드 크로스(``one``이 ``two``를 하향 돌파)를 판단한다.
        교차가 발생하면 신호를 생성해 저장소에 저장하고 반환한다.
        교차가 없으면 ``None``을 반환한다.

        Args:
            one_values: 비교 기준 첫 번째 값 시리즈 (예: 단기 이동평균).
            two_values: 비교 기준 두 번째 값 시리즈 (예: 장기 이동평균).
            market: 대상 마켓 코드.
            candle_type: 캔들 타입 (타임프레임 결정에 사용).
            indicator_id: 지표 식별자.
            regime: 현재 시장 국면.

        Returns:
            교차 발생 시 저장된 :class:`Signal`, 아니면 ``None``.
        """
        if len(one_values) < 2 or len(two_values) < 2:
            return None

        prev_one, curr_one = one_values[-2], one_values[-1]
        prev_two, curr_two = two_values[-2], two_values[-1]

        metadata = {
            "prev_one": float(prev_one),
            "curr_one": float(curr_one),
            "prev_two": float(prev_two),
            "curr_two": float(curr_two),
        }

        # 과거 (one < two) and 현재 (one > two) -> GOLDEN CROSS
        if prev_one < prev_two and curr_one > curr_two:
            signal = self.create_signal(
                indicator_id=indicator_id,
                signal_type=SignalType.CROSS_OVER,
                value=SignalValue.GOLDEN_CROSS,
                direction=SignalDirection.LONG,
                regime=regime,
                market=market,
                candle_type=candle_type,
                metadata=metadata,
            )
            logger.info(
                "strategy.signal.crossover", indicator=indicator_id, market=market, cross="golden", regime=regime.value
            )
            return await self.signal_repository.save(signal)
        # 과거 (one > two) and 현재 (one < two) -> DEAD CROSS
        elif prev_one > prev_two and curr_one < curr_two:
            signal = self.create_signal(
                indicator_id=indicator_id,
                signal_type=SignalType.CROSS_OVER,
                value=SignalValue.DEAD_CROSS,
                direction=SignalDirection.CLOSE,
                regime=regime,
                market=market,
                candle_type=candle_type,
                metadata=metadata,
            )
            logger.info(
                "strategy.signal.crossover", indicator=indicator_id, market=market, cross="dead", regime=regime.value
            )
            return await self.signal_repository.save(signal)
        else:
            return await self.signal_repository.find_by_strategy_id_and_indicator_id_and_type(
                self.name, indicator_id, SignalType.CROSS_OVER
            )

    async def check_level_break(
        self,
        value: Decimal,
        overbought: int,
        oversold: int,
        market: str,
        candle_type: CandleType,
        indicator_id: str,
        regime: MarketRegime,
    ) -> Signal | None:
        """지표 값이 과매수/과매도 임계값을 벗어났는지 감지하고 신호를 저장한다.

        Args:
            value: 현재 지표 값 (예: RSI).
            overbought: 과매수 임계값. ``value > overbought`` 이면 OVER_BOUGHT 신호 생성.
            oversold: 과매도 임계값. ``value < oversold`` 이면 OVER_SOLD 신호 생성.
            market: 대상 마켓 코드.
            candle_type: 캔들 타입 (타임프레임 결정에 사용).
            indicator_id: 지표 식별자.
            regime: 현재 시장 국면.

        Returns:
            임계값 돌파 시 저장된 :class:`Signal`, 아니면 ``None``.
        """
        metadata = {"value": float(value), "overbought": overbought, "oversold": oversold}

        # value > 과매수 임계값 -> OVER BOUGHT
        if value > Decimal(str(overbought)):
            signal = self.create_signal(
                indicator_id=indicator_id,
                signal_type=SignalType.LEVEL_BREAK,
                value=SignalValue.OVER_BOUGHT,
                direction=SignalDirection.LONG,
                regime=regime,
                market=market,
                candle_type=candle_type,
                metadata=metadata,
            )
            logger.info(
                "strategy.signal.level_break",
                indicator=indicator_id,
                market=market,
                level="overbought",
                value=float(value),
                regime=regime.value,
            )
            return await self.signal_repository.save(signal)
        elif value < Decimal(str(oversold)):
            signal = self.create_signal(
                indicator_id=indicator_id,
                signal_type=SignalType.LEVEL_BREAK,
                value=SignalValue.OVER_SOLD,
                direction=SignalDirection.CLOSE,
                regime=regime,
                market=market,
                candle_type=candle_type,
                metadata=metadata,
            )
            logger.info(
                "strategy.signal.level_break",
                indicator=indicator_id,
                market=market,
                level="oversold",
                value=float(value),
                regime=regime.value,
            )
            return await self.signal_repository.save(signal)
        else:
            return await self.signal_repository.find_by_strategy_id_and_indicator_id_and_type(
                self.name, indicator_id, SignalType.LEVEL_BREAK
            )

    @staticmethod
    def calculate_ema(candles: list[Candle], period: int = 9) -> np.ndarray:
        """캔들 목록에서 지수 이동평균(EMA)을 계산한다.

        캔들 수가 ``period`` 미만이면 빈 배열을 반환한다.

        Args:
            candles: 계산에 사용할 캔들 목록.
            period: EMA 기간. 기본값 ``9``.

        Returns:
            EMA 값 배열. 캔들 수 부족 시 빈 배열.
        """
        if len(candles) < period:
            return np.array([])
        trade_prices = np.array([float(candle.trade_price) for candle in candles])
        return talib.EMA(trade_prices, period)

    @staticmethod
    def calculate_macd(
        candles: list[Candle], fast_period: int = 13, slow_period: int = 26, signal_period: int = 9
    ) -> dict[str, np.ndarray]:
        """캔들 목록에서 MACD 지표를 계산한다.

        캔들 수가 ``slow_period`` 미만이면 빈 배열로 채워진 딕셔너리를 반환한다.

        Args:
            candles: 계산에 사용할 캔들 목록.
            fast_period: 단기 EMA 기간. 기본값 ``13``.
            slow_period: 장기 EMA 기간. 기본값 ``26``.
            signal_period: 시그널 라인 EMA 기간. 기본값 ``9``.

        Returns:
            ``macd``, ``signal``, ``histogram`` 키를 가진 딕셔너리.
            캔들 수 부족 시 각 값은 빈 배열.
        """
        return_dictionary = {"macd": np.array([]), "signal": np.array([]), "histogram": np.array([])}
        if len(candles) < slow_period:
            return return_dictionary

        trade_prices = np.array([float(candle.trade_price) for candle in candles])
        macd, signal, histogram = talib.MACD(
            trade_prices, fastperiod=fast_period, slowperiod=slow_period, signalperiod=signal_period
        )
        return_dictionary["macd"] = macd
        return_dictionary["signal"] = signal
        return_dictionary["histogram"] = histogram
        return return_dictionary

    @staticmethod
    def calculate_rsi(candles: list[Candle], period: int = 14) -> np.ndarray:
        """캔들 목록에서 RSI(상대 강도 지수)를 계산한다.

        캔들 수가 ``period`` 미만이면 빈 배열을 반환한다.

        Args:
            candles: 계산에 사용할 캔들 목록.
            period: RSI 기간. 기본값 ``14``.

        Returns:
            RSI 값 배열. 캔들 수 부족 시 빈 배열.
        """
        if len(candles) < period:
            return np.array([])
        trade_prices = np.array([float(candle.trade_price) for candle in candles])
        return talib.RSI(trade_prices, period)

    @staticmethod
    def calculate_stoch(
        candles: list[Candle], k_len: int = 10, k_smooth: int = 6, d_smooth: int = 6
    ) -> dict[str, np.ndarray]:
        """캔들 목록에서 스토캐스틱 오실레이터(Stochastic)를 계산한다.

        캔들 수가 ``k_len`` 미만이면 빈 배열로 채워진 딕셔너리를 반환한다.

        Args:
            candles: 계산에 사용할 캔들 목록.
            k_len: Fast %K 기간 (원시 스토캐스틱 윈도우).
            k_smooth: Slow %K 스무딩 기간.
            d_smooth: Slow %D 스무딩 기간.

        Returns:
            ``k_slow``, ``d_slow`` 키를 가진 딕셔너리.
            캔들 수 부족 시 각 값은 빈 배열.
        """
        return_dictionary = {"k_slow": np.array([]), "d_slow": np.array([])}
        if len(candles) < k_len:
            return return_dictionary

        high_prices = np.array([float(candle.high_price) for candle in candles])
        low_prices = np.array([float(candle.low_price) for candle in candles])
        trade_prices = np.array([float(candle.trade_price) for candle in candles])

        k_slow, d_slow = talib.STOCH(
            high_prices,
            low_prices,
            trade_prices,
            fastk_period=k_len,
            slowk_period=k_smooth,
            slowk_matype=MA_Type.SMA,
            slowd_period=d_smooth,
            slowd_matype=MA_Type.SMA,
        )

        return_dictionary["k_slow"] = k_slow
        return_dictionary["d_slow"] = d_slow
        return return_dictionary
