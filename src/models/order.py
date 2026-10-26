from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from ..utils.constants import OrderSide, OrderState, OrderType, SmpType, TimeInForce
from .base import Base


@dataclass
class Order(Base):
    """
    업비트 주문(Order) 응답 데이터 모델

    Attributes:
        uuid (UUID): 주문의 고유 식별자
        side (OrderSide): 주문 종류 (bid: 매수, ask: 매도)
        ord_type (OrderType): 주문 유형 (limit: 지정가, price: 시장가 매수, market: 시장가 매도, best: 최유리 지정가)
        price (Optional[Decimal]): 주문 단가 또는 총액
        state (OrderState): 주문 상태 (wait, watch, done, cancel)
        market (str): 마켓 ID (예: KRW-BTC)
        created_at (datetime): 주문 생성 시각 (ISO 8601, KST 기준)
        volume (Optional[Decimal]): 주문 요청 수량
        remaining_volume (Decimal): 체결 후 남은 주문 수량
        executed_volume (Decimal): 체결된 수량
        trades_count (int): 해당 주문에 대한 체결 건수
        reserved_fee (Decimal): 수수료로 예약된 비용
        remaining_fee (Decimal): 남은 수수료
        paid_fee (Decimal): 사용된 수수료
        locked (Decimal): 거래에 사용 중인 자산 (잠금 상태)
        identifier (Optional[str]): 클라이언트 지정 주문 식별자
        time_in_force (Optional[TimeInForce]): 주문 체결 옵션 (ioc, fok, post_only)
        smp_type (Optional[SmpType]): 자전거래 체결 방지 모드 (cancel_maker, cancel_taker, reduce)
        prevented_volume (Optional[Decimal]): 자전거래 방지(SMP)로 인해 취소된 수량
        prevented_locked (Optional[Decimal]): 자전거래 방지(SMP)로 인해 해제된 자산
    """

    uuid: UUID
    side: OrderSide
    ord_type: OrderType
    price: Decimal | None
    state: OrderState
    market: str
    created_at: datetime
    volume: Decimal | None
    remaining_volume: Decimal
    executed_volume: Decimal
    trades_count: int
    reserved_fee: Decimal
    remaining_fee: Decimal
    paid_fee: Decimal
    locked: Decimal
    identifier: str | None = None
    time_in_force: TimeInForce | None = None
    smp_type: SmpType | None = None
    prevented_volume: Decimal | None = None
    prevented_locked: Decimal | None = None

    def __post_init__(self):
        for field_name in [
            "remaining_volume",
            "executed_volume",
            "reserved_fee",
            "remaining_fee",
            "paid_fee",
            "locked",
        ]:
            value = getattr(self, field_name)
            if isinstance(value, (int, float, str)):
                setattr(self, field_name, Decimal(str(value)))

        for field_name in ["price", "volume", "prevented_volume", "prevented_locked"]:
            value = getattr(self, field_name)
            if value is not None and isinstance(value, (int, float, str)):
                setattr(self, field_name, Decimal(str(value)))

        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)

        if isinstance(self.uuid, str):
            self.uuid = UUID(self.uuid)

        if isinstance(self.side, str):
            self.side = OrderSide(self.side)
        if isinstance(self.ord_type, str):
            self.ord_type = OrderType(self.ord_type)
        if isinstance(self.state, str):
            self.state = OrderState(self.state)
        if isinstance(self.time_in_force, str):
            self.time_in_force = TimeInForce(self.time_in_force)
        if isinstance(self.smp_type, str):
            self.smp_type = SmpType(self.smp_type)
