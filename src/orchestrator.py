import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import structlog

from src.connections import MarketDataFeed, PostgresPool, RedisClient, UpbitAdapter
from src.decision import DecisionEngine
from src.decision.confluence_checker import ConfluenceChecker
from src.models import Candle, Decision, ExecutionResult, PortfolioState, Position, RiskContext, RiskLimitsConfig
from src.repositories import OrderRepository, SignalRepository
from src.risk import RiskEngine
from src.risk.risk_rule import RiskRule
from src.risk.rules import (
    DailyLossLimitRule,
    MaxDrawdownRule,
    MaxPositionsRule,
    PortfolioExposureRule,
    PositionSizeRule,
    WeeklyLossLimitRule,
)
from src.strategies import MacdRsiStochasticStrategy, RegimeDetector, SignalAggregator
from src.strategies.base_strategy import BaseStrategy
from src.utils.constants import (
    CandleType,
    DecisionState,
    ExecutionState,
    OrderSide,
    OrderState,
    RiskDecision,
    SignalDirection,
    Timeframe,
)

logger = structlog.get_logger(__name__)


class Orchestrator:
    """트레이딩 봇의 전체 생애주기를 관리하는 최상위 오케스트레이터.

    연결(DB·Redis·거래소), 전략, 의사결정 엔진, 리스크 엔진 등
    모든 컴포넌트를 초기화하고 조율한다.

    Attributes:
        mode (str): 트레이딩 실행 모드. ``DRY_RUN`` | ``PAPER`` | ``LIVE``
        markets (list[str]): 감시할 마켓 코드 목록.
        candle_types (list[str]): 구독할 타임프레임 목록.
        config (dict): 컴포넌트별 설정 딕셔너리.
    """

    def __init__(self, mode: str, markets: list[str], candle_types: list[str], config: dict):
        """Orchestrator 를 초기화한다.

        Args:
            mode: 트레이딩 실행 모드. ``DRY_RUN`` | ``PAPER`` | ``LIVE``
            markets: 감시할 마켓 코드 목록.
            candle_types: 구독할 타임프레임 목록.
            config: 컴포넌트별 설정 딕셔너리.
        """
        self.mode = mode
        self.markets = markets
        self.candle_types = candle_types
        self.config = config or {}

        # 런타임 상태
        self._running = False
        self._started_at: datetime | None = None
        self._candles_processed = 0
        self._signals_generated = 0
        self._trades_executed = 0
        self._initial_capital: Decimal | None = None

        # Connections
        self._pool: PostgresPool | None = None
        self._redis: RedisClient | None = None
        self._market_feed: MarketDataFeed | None = None
        self._adapter: UpbitAdapter | None = None

        # Strategies
        self._regime_detector: RegimeDetector | None = None
        self._aggregator: SignalAggregator | None = None
        self._strategies: list[BaseStrategy] = []
        self._signal_count: int = 3

        # Decision
        self._confluence_checker: ConfluenceChecker | None = None
        self._decision_engine: DecisionEngine | None = None

        # Repositories
        self._order_repository: OrderRepository | None = None
        self._signal_repository: SignalRepository | None = None

        # Risk
        self._risk_engine: RiskEngine | None = None
        self._risk_config = RiskLimitsConfig(
            max_drawdown=0.20,  # 최대 낙폭 20%
            daily_loss_limit=0.05,  # 일일 손실 한도 5%
            weekly_loss_limit=0.10,  # 주간 손실 한도 10%
            max_position_size=0.30,  # 단일 포지션 최대 비중 30%
            max_risk_per_trade=0.02,  # 거래당 최대 리스크 2%
            max_positions=5,  # 동시 보유 최대 포지션 수 5개
            max_portfolio_exposure=0.80,  # 포트폴리오 최대 노출도 80%
        )

        # Portfolio State
        self._portfolio: PortfolioState | None = None

    async def setup(self) -> bool:
        """모든 컴포넌트를 순서대로 초기화한다.

        DB 풀 → Redis → 거래소 어댑터 → 포트폴리오 → 시장 데이터 피드 →
        레짐 감지기 → 신호 집계기 → 컨플루언스 체커 → 의사결정 엔진 →
        리스크 엔진 → 전략 순으로 초기화한다.

        Returns:
            초기화 성공 시 ``True``, 예외 발생 시 ``False``.
        """
        logger.info("orchestrator.setup.started")

        try:
            # 1. DB Pool 초기화
            self._pool = PostgresPool(self.config.get("database"))
            await self._pool.connect()
            logger.info("orchestrator.setup.postgres_ready")

            # 1.1 Repositories 초기화
            self._order_repository = OrderRepository(pool=self._pool)
            self._signal_repository = SignalRepository(pool=self._pool)

            # 2. Redis 초기화
            self._redis = RedisClient(self.config.get("redis"))
            await self._redis.connect()
            logger.info("orchestrator.setup.redis_ready")

            # 3. UpbitAdapter 초기화
            self._adapter = UpbitAdapter(self.config.get("upbit"))
            await self._adapter.connect()
            logger.info("orchestrator.setup.adapter_ready")

            # 3.1. Portfolio state 초기화 — 거래소에서 KRW 잔고를 조회하여 초기화
            krw = await self._adapter.get_krw()
            total_capital = krw.balance + krw.locked
            self._initial_capital = total_capital
            self._portfolio = PortfolioState(
                total_capital=total_capital,
                available_capital=krw.balance,
                high_water_mark=krw.balance,
                daily_pnl=Decimal("0"),
                weekly_pnl=Decimal("0"),
                total_pnl=Decimal("0"),
            )

            # 4. MarketDataFeed 초기화
            self._market_feed = MarketDataFeed(codes=self.markets, types=self.candle_types, on_candle=self._on_candle)
            logger.info("orchestrator.setup.market_feed_ready")

            # 5. RegimeDetector 초기화
            self._regime_detector = RegimeDetector(self.config.get("regime_detector"))

            # 6. SignalAggregator 초기화
            self._aggregator = SignalAggregator()
            logger.info("orchestrator.setup.aggregator_ready")

            # 7. ConfluenceChecker 초기화
            self._confluence_checker = ConfluenceChecker()
            logger.info("orchestrator.setup.confluence_checker_ready")

            # 8. DecisionEngine 초기화
            self._decision_engine = DecisionEngine(
                risk_config=self._risk_config, aggregator=self._aggregator, confluence_checker=self._confluence_checker
            )
            logger.info("orchestrator.setup.decision_engine_ready")

            # 9. RiskEngine 초기화
            risk_rules: list[RiskRule] = [
                DailyLossLimitRule(),
                MaxDrawdownRule(),
                MaxPositionsRule(),
                PortfolioExposureRule(),
                PositionSizeRule(),
                WeeklyLossLimitRule(),
            ]
            self._risk_engine = RiskEngine(rules=risk_rules)
            logger.info("orchestrator.setup.risk_engine_ready", rule_count=len(risk_rules))

            # 10. Strategies 초기화
            self._setup_strategies()
            logger.info("orchestrator.setup.strategies_ready", strategy_count=len(self._strategies))

            logger.info("orchestrator.setup.completed")
            return True

        except Exception as e:
            logger.exception("orchestrator.setup.failed", error=str(e))
            return False

    def _setup_strategies(self) -> None:
        """트레이딩 전략 인스턴스를 생성하여 전략 목록에 등록한다."""
        # MACD·RSI·스토캐스틱 복합 전략 등록
        macd_rsi_stoch_strategy = MacdRsiStochasticStrategy(
            config=self.config.get("macd_rsi_stochastic_strategy"),
            aggregator=self._aggregator,
            signal_repository=self._signal_repository,
        )
        self._strategies.append(macd_rsi_stoch_strategy)

        logger.info("orchestrator.strategies.registered", strategies=[s.name for s in self._strategies])

    async def shutdown(self) -> None:
        """모든 컴포넌트 연결을 해제하고 운용 요약을 출력한다.

        이미 중지 상태이면 아무 동작도 하지 않는다.
        """
        if not self._running:
            return

        logger.info("orchestrator.shutdown.started")
        self._running = False

        # MarketDataFeed 연결 해제
        if self._market_feed:
            try:
                await self._market_feed.disconnect()
            except Exception as e:
                logger.warning("orchestrator.shutdown.market_feed_error", error=str(e))

        # UpbitAdapter 연결 해제
        if self._adapter:
            try:
                await self._adapter.disconnect()
            except Exception as e:
                logger.warning("orchestrator.shutdown.adapter_error", error=str(e))

        # DB Pool 연결 해제
        if self._pool:
            try:
                await self._pool.disconnect()
            except Exception as e:
                logger.warning("orchestrator.shutdown.pool_error", error=str(e))

        # Redis 연결 해제
        if self._redis:
            try:
                await self._redis.disconnect()
            except Exception as e:
                logger.warning("orchestrator.shutdown.redis_error", error=str(e))

        # Print summary
        runtime = datetime.now() - self._started_at if self._started_at else timedelta(0)
        logger.info(
            "orchestrator.shutdown.completed",
            runtime=str(runtime),
            candles_processed=self._candles_processed,
            signals_generated=self._signals_generated,
            trades_executed=self._trades_executed,
            final_portfolio=str(self._portfolio.total_capital) if self._portfolio else None,
        )

    async def run(self) -> None:
        """오케스트레이터를 시작하고 시장 데이터 피드를 구동한다.

        아직 setup() 이 완료되지 않은 경우 자동으로 호출한다.
        피드가 종료(정상 또는 예외)되면 항상 shutdown() 을 호출한다.

        Raises:
            RuntimeError: setup() 이 실패한 경우.
        """
        if self._running:
            logger.warning("orchestrator.run.already_running")
            return  # Bug fix: 이미 실행 중이면 중복 시작 방지를 위해 조기 반환

        # setup() 이 아직 호출되지 않은 경우 자동 초기화
        if self._pool is None:
            success = await self.setup()
            if not success:
                raise RuntimeError("Failed to setup orchestrator")

        self._running = True
        self._started_at = datetime.now()

        # 시장 데이터 피드 구동 — 취소 또는 오류 발생 시 shutdown() 보장
        try:
            await self._market_feed.connect()
        except asyncio.CancelledError:
            logger.info("orchestrator.run.feed_cancelled")
        except Exception as e:
            logger.error("orchestrator.run.feed_error", error=str(e))
        finally:
            await self.shutdown()

    async def _on_candle(self, candle: Candle) -> None:
        if not self._running:
            return

        self._candles_processed += 1

        if self._candles_processed % 100 == 0:
            logger.info(
                "orchestrator.heartbeat",
                candles_processed=self._candles_processed,
                trades_executed=self._trades_executed,
                uptime=str(datetime.now() - self._started_at) if self._started_at else None,
                portfolio_value=str(self._portfolio.total_capital) if self._portfolio else None,
                open_positions=self._portfolio.num_positions if self._portfolio else 0,
            )

        key = f"{candle.code}:{candle.type}"
        await self._redis.hset("latest_price", key, candle.trade_price)

        if candle.type == CandleType.HOUR_4:
            candles = await self._adapter.get_candles(market=candle.code, timeframe=Timeframe.HOUR_4)
        else:
            candles = await self._adapter.get_candles(market=candle.code, timeframe=Timeframe.DAY)

        candles[-1] = candle
        await self._redis.hset("candles", key, candles)

        regime = self._regime_detector.detect(candles)

        try:
            for strategy in self._strategies:
                if regime not in strategy.get_supported_regimes():
                    continue
                await strategy.evaluate(candles, regime, self._portfolio)

            ticker = await self._adapter.get_ticker(candle.code)
            decisions = self._decision_engine.process(self._portfolio, ticker.trade_price)

            logger.info("orchestrator.candle_close.completed", market=candle.code, decision_count=len(decisions))

            for d in decisions:
                await self._execute_decision(d)

        except Exception as e:
            logger.error("orchestrator.candle_close.error", error=str(e))

    async def _execute_decision(self, decision: Decision) -> ExecutionResult | None:
        """리스크 검증 후 주문을 실행하고 ExecutionResult 를 반환한다.

        리스크 엔진이 ALLOW 또는 REDUCE_SIZE 를 반환하면 주문을 실행한다.
        REDUCE_SIZE 의 경우 max_allowed_size_krw 에 맞게 수량을 줄인 뒤 주문한다.
        그 외(FORCE_NO_ACTION, EMERGENCY_STOP)는 decision 을 REJECTED 로 표시하고 None 을 반환한다.

        Args:
            decision: 실행할 거래 결정 객체.

        Returns:
            주문 실행 성공 시 ExecutionResult, 리스크 거부 또는 주문 실패 시 None.
        """
        logger.info(
            "orchestrator.execute_decision.started",
            market=decision.market,
            direction=decision.direction.value,
            volume=str(decision.volume),
        )

        initial = self._initial_capital or self._portfolio.total_capital
        context = RiskContext(
            system_state="RUNNING",
            mode=self.mode,
            open_positions_count=self._portfolio.num_positions,
            total_position_value_krw=self._portfolio.positions_value,
            portfolio_value_krw=self._portfolio.total_capital,
            starting_capital_krw=initial,
            daily_pnl_krw=self._portfolio.daily_pnl,
            daily_pnl_percent=Decimal(str(self._portfolio.daily_pnl / initial * 100)) if initial > 0 else Decimal("0"),
            weekly_pnl_krw=self._portfolio.weekly_pnl,
            weekly_pnl_percent=Decimal(str(self._portfolio.weekly_pnl / initial * 100))
            if initial > 0
            else Decimal("0"),
            peak_portfolio_value_krw=self._portfolio.high_water_mark,
            current_drawdown_percent=Decimal(str(self._portfolio.current_drawdown * 100)),
            proposed_trade_size_krw=decision.volume * decision.entry_price,
            proposed_trade_risk_percent=Decimal(str(decision.risk_percent * 100)),
        )

        risk_record = self._risk_engine.evaluate(context, str(decision.decision_id))

        if risk_record.risk_decision in [RiskDecision.ALLOW, RiskDecision.REDUCE_SIZE]:
            decision.state = DecisionState.APPROVED
            logger.info("orchestrator.execute_decision.risk_approved", reason=risk_record.reason)

            # REDUCE_SIZE: max_allowed_size_krw 내로 수량 축소
            if risk_record.risk_decision == RiskDecision.REDUCE_SIZE and risk_record.max_allowed_size_krw:
                max_vol = Decimal(str(risk_record.max_allowed_size_krw)) / decision.entry_price
                if max_vol < decision.volume:
                    logger.info(
                        "orchestrator.execute_decision.size_reduced", before=str(decision.volume), after=str(max_vol)
                    )
                    decision.volume = max_vol

            result = await self._place_order(decision)

            if result and result.success:
                decision.state = DecisionState.EXECUTED
                self._trades_executed += 1
                await self._update_portfolio_after_trade(decision, result)
                logger.info(
                    "orchestrator.execute_decision.trade_executed",
                    direction=decision.direction.value,
                    market=decision.market,
                    filled_quantity=str(result.filled_quantity),
                    average_price=str(result.average_price),
                )
                return result
            else:
                logger.error(
                    "orchestrator.execute_decision.trade_failed",
                    market=decision.market,
                    error=result.error_message if result else "unknown",
                )
                return None
        else:
            decision.state = DecisionState.REJECTED
            logger.warning("orchestrator.execute_decision.risk_blocked", reason=risk_record.reason)

            # 발화된 리스크 규칙 목록 기록
            for rule in risk_record.triggered_rules:
                logger.warning(
                    "orchestrator.execute_decision.triggered_rule", rule_name=rule.rule_name, message=rule.message
                )

            return None

    async def _place_order(self, decision: Decision) -> ExecutionResult | None:
        """지정가 주문을 거래소에 제출하고 ExecutionResult 를 반환한다.

        SignalDirection 을 OrderSide 로 변환한 뒤 UpbitAdapter.limit_order() 를 호출한다.
        HOLD 방향은 실행 불가 방향이므로 None 을 반환한다.
        예외 발생 시 오류를 기록하고 None 을 반환한다.

        Args:
            decision: 주문에 필요한 마켓·방향·수량·진입가를 담은 결정 객체.

        Returns:
            주문 성공 시 ExecutionResult, 실패 시 None.
        """
        # SignalDirection → OrderSide 매핑 (LONG: 매수, SHORT/CLOSE: 매도)
        direction_to_side = {
            SignalDirection.LONG: OrderSide.BID,
            SignalDirection.SHORT: OrderSide.ASK,
            SignalDirection.CLOSE: OrderSide.ASK,
        }
        side = direction_to_side.get(decision.direction)
        if side is None:
            logger.warning(
                "orchestrator.place_order.unsupported_direction",
                direction=decision.direction.value,
                market=decision.market,
            )
            return None

        try:
            order = await self._adapter.limit_order(
                market=decision.market, side=side, volume=decision.volume, price=decision.entry_price
            )
            await self._order_repository.save(order)

            # OrderState → ExecutionState 매핑
            order_state_map = {
                OrderState.DONE: ExecutionState.FILLED,
                OrderState.CANCEL: ExecutionState.CANCELLED,
                OrderState.WAIT: ExecutionState.PENDING,
                OrderState.WATCH: ExecutionState.PENDING,
            }
            exec_state = order_state_map.get(order.state, ExecutionState.PENDING)

            # 평균 체결가: 주문 단가 우선, 없으면 진입 예정가 사용
            average_price = order.price if order.price is not None else decision.entry_price

            result = ExecutionResult(
                success=True,
                decision_id=decision.decision_id,
                order_uuid=order.uuid,
                filled_quantity=order.executed_volume,
                average_price=average_price,
                fee=order.paid_fee,
                fee_asset="KRW",
                state=exec_state,
            )
            logger.info(
                "orchestrator.place_order.success",
                market=decision.market,
                direction=decision.direction.value,
                filled_quantity=str(order.executed_volume),
                average_price=str(average_price),
                order_uuid=str(order.uuid),
            )
            return result

        except Exception as e:
            logger.error(
                "orchestrator.place_order.failed",
                market=decision.market,
                direction=decision.direction.value,
                error=str(e),
            )
            return None

    async def _update_portfolio_after_trade(self, decision: Decision, result: ExecutionResult) -> None:
        """체결 결과를 포트폴리오 상태에 반영한다.

        LONG 방향이면 신규 포지션을 개설하고 가용 자본을 차감한다.
        CLOSE 방향이면 기존 포지션을 청산하고 실현 손익을 PnL 에 반영한다.
        처리 후 total_capital 과 high_water_mark 를 최신 상태로 갱신한다.

        Args:
            decision: 체결된 거래 결정 객체.
            result: 거래소로부터 수신한 체결 결과.
        """
        strategy_id = decision.contributing_signals[0].strategy_id if decision.contributing_signals else ""
        trade_value = result.filled_quantity * result.average_price

        if decision.direction == SignalDirection.LONG:
            # 신규 롱 포지션 개설
            position = Position(
                market=decision.market,
                direction=decision.direction,
                entry_price=result.average_price,
                current_price=result.average_price,
                volume=result.filled_quantity,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                strategy_id=strategy_id,
            )
            self._portfolio.positions[decision.market] = position
            # 매수 대금 + 수수료 차감
            self._portfolio.available_capital -= trade_value + result.fee

            logger.info(
                "orchestrator.portfolio.position_opened",
                market=decision.market,
                direction=decision.direction.value,
                volume=str(result.filled_quantity),
                entry_price=str(result.average_price),
                fee=str(result.fee),
            )

        elif decision.direction == SignalDirection.SHORT:
            logger.warning("orchestrator.portfolio.short_not_supported", market=decision.market)

        elif decision.direction == SignalDirection.CLOSE:
            # 기존 포지션 청산
            if decision.market in self._portfolio.positions:
                pos = self._portfolio.positions[decision.market]

                # 포지션 방향에 따라 실현 손익 계산
                if pos.direction == SignalDirection.LONG:
                    pnl = (result.average_price - pos.entry_price) * result.filled_quantity
                else:
                    pnl = (pos.entry_price - result.average_price) * result.filled_quantity
                # 수수료 차감 후 순 손익
                pnl -= result.fee

                self._portfolio.available_capital += trade_value - result.fee
                self._portfolio.total_pnl += pnl
                self._portfolio.daily_pnl += pnl
                self._portfolio.weekly_pnl += pnl

                del self._portfolio.positions[decision.market]

                logger.info(
                    "orchestrator.portfolio.position_closed",
                    market=decision.market,
                    pnl=str(pnl),
                    exit_price=str(result.average_price),
                    fee=str(result.fee),
                )
            else:
                logger.warning("orchestrator.portfolio.close_without_position", market=decision.market)

        # total_capital 갱신 (가용 현금 + 포지션 평가금액)
        self._portfolio.total_capital = self._portfolio.available_capital + self._portfolio.positions_value

        # high_water_mark 갱신
        if self._portfolio.total_capital > self._portfolio.high_water_mark:
            self._portfolio.high_water_mark = self._portfolio.total_capital

        self._portfolio.trade_count_today += 1
        self._portfolio.last_updated = datetime.now()
