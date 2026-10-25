# 매매 로직

upbit-bot의 매매 로직을 정리한 문서입니다.

---

## 목차

1. [개요](#1-개요)
2. [설정](#2-설정)
3. [지표](#3-지표)
4. [신호 상태](#4-신호-상태)
5. [매매 판단](#5-매매-판단)
6. [주문 금액](#6-주문-금액)
7. [상태 초기화](#7-상태-초기화)

---

## 1. 개요

RSI, 스토캐스틱, MACD 3종의 신호를 조합해 **과매도 구간에서 반등이 확인되면 매수**하고, **과매수 또는 하락 전환이 확인되면 수익 중일 때 전량 매도**합니다. 손실이 손절 기준에 닿으면 바로 전량 매도합니다.

| 항목 | 내용 |
|------|------|
| 캔들 | 30분봉 |
| 지표 | MACD 3종, RSI(14), 스토캐스틱 slow(14, 3, 4) |
| 신호 | 지표 이벤트(과매수/과매도 진입, 골든/데드 크로스)가 발생하면 마지막 상태를 저장하고, 저장된 상태로 판단 |
| 신규 매수 | 기본 금액 |
| 추가 매수 | n번째 추가 매수 `기본 금액 × 2 × n`, 횟수·총액 상한 |
| 매도 | 보유 수량 전량 |
| 손절 | 순수익률이 손절 기준 이하 |

### 1.1 판단 시점

| 판단 | 시점 | 기준 가격 |
|------|------|-----------|
| 신호 상태 갱신, 신호 매도, 매수 | 캔들 마감 직후 | 마감 캔들 종가 |
| 손절 | 실시간 + 캔들 마감 직후 | 실시간 현재가 / 마감 캔들 종가 |

- 신호에 기반한 판단은 **마감된 캔들로만** 합니다. 진행 중인 캔들의 지표 값은 가격에 따라 계속 바뀌어, 생겼다 사라지는 신호가 저장되기 때문입니다.
- 손절은 신호가 아니라 가격 기준이므로 캔들 마감을 기다리지 않습니다.

---

## 2. 설정

### 2.1 설정 예시

```json
{
  "timeframe": "30m",
  "indicator": {
    "ema": { "short": 5, "mid": 8, "long": 13 },
    "macd_signal": 9,
    "rsi": { "period": 14, "signal": 9 },
    "stochastic": { "k_len": 14, "k_smooth": 3, "d_smooth": 4 }
  },
  "threshold": {
    "rsi": { "over_sold": 31, "over_bought": 60 },
    "stochastic": { "over_sold": 25, "over_bought": 75 },
    "stochastic_entry_max": 50
  },
  "order": {
    "base_amount": { "KRW": 10000, "BTC": 0.0001, "USDT": 10 },
    "max_add_buys": 4,
    "max_position_amount": { "KRW": 210000, "BTC": 0.0021, "USDT": 210 },
    "min_order_amount": { "KRW": 5000, "BTC": 0.00005, "USDT": 0.5 },
    "fee_rate": { "KRW": 0.0005, "BTC": 0.0025, "USDT": 0.0025 }
  },
  "exit": {
    "min_profit_rate": 0.0,
    "stop_loss_rate": -10.0
  }
}
```

### 2.2 항목 설명

| 항목 | 예시 값 | 의미 |
|------|---------|------|
| `timeframe` | `30m` | 캔들 단위 |
| `indicator.ema` | 5 / 8 / 13 | MACD 계산에 쓰는 단기·중기·장기 EMA 기간 |
| `indicator.macd_signal` | 9 | MACD 시그널 기간 |
| `indicator.rsi` | 14 / 9 | RSI 기간, RSI 시그널 기간 |
| `indicator.stochastic` | 14 / 3 / 4 | %K 기간, slow %K 평활 기간, slow %D 평활 기간 |
| `threshold.rsi` | 31 / 60 | RSI 과매도 / 과매수 기준 |
| `threshold.stochastic` | 25 / 75 | 스토캐스틱 과매도 / 과매수 기준 (%K, %D 모두) |
| `threshold.stochastic_entry_max` | 50 | 신규 매수 시 %K, %D 상한 |
| `order.base_amount` | 통화별 | 신규 매수 금액 (호가 통화 단위) |
| `order.max_add_buys` | 4 | 포지션당 추가 매수 최대 횟수 |
| `order.max_position_amount` | 통화별 | 포지션 총 매입금액 상한 (수수료 포함) |
| `order.min_order_amount` | 통화별 | 최소 주문 금액 |
| `order.fee_rate` | 통화별 | 순수익률 계산에 쓰는 예상 수수료율 |
| `exit.min_profit_rate` | 0.0 | 신호 매도를 허용하는 최소 순수익률 (%) |
| `exit.stop_loss_rate` | -10.0 | 손절 기준 순수익률 (%) |

`max_add_buys = 4`, `base_amount = 10,000원`이면 포지션 총 매입금액은 최대 10,000 + 20,000 + 40,000 + 60,000 + 80,000 = 210,000원입니다. 예시의 `max_position_amount`는 이 값에 맞췄습니다.

---

## 3. 지표

모든 지표는 **마감된 캔들**의 값으로 계산합니다.

### 3.1 MACD 3종

| 이름 | 계산 |
|------|------|
| MACD short | EMA(close, 5) − EMA(close, 8) |
| MACD mid | EMA(close, 5) − EMA(close, 13) |
| MACD long | EMA(close, 8) − EMA(close, 13) |
| 시그널 | 각 MACD의 EMA(9) |

- EMA는 `ewm(span=period, adjust=False)`입니다.
- 크로스는 마감 캔들 i-1과 i 사이에서 MACD 선이 시그널 선을 넘었는지로 판정합니다.

### 3.2 RSI

Wilder 방식입니다.

```python
delta = close.diff()
U = delta.clip(lower=0)
D = (-delta).clip(lower=0)
AU = U.ewm(alpha=1/period, adjust=False).mean()
AD = D.ewm(alpha=1/period, adjust=False).mean()

if AD == 0 and AU == 0: RSI = 50        # 변동 없음
elif AD == 0:           RSI = 100       # 상승만 있음
else:                   RSI = 100 - 100 / (1 + AU / AD)

signal = EMA(RSI, 9)
```

- RSI는 전체 움직임 중 상승이 차지하는 비율(`100 × AU / (AU + AD)`)이므로, 하락이 없으면 100입니다.
- 첫 `period`개 캔들은 평균이 안정되지 않아 판단에서 제외합니다.

### 3.3 스토캐스틱 (slow)

```python
k_fast = (close - low_14) / (high_14 - low_14) * 100
k_slow = SMA(k_fast, 3)     # slow %K
d_slow = SMA(k_slow, 4)     # slow %D
```

- 14개 캔들의 고가·저가 범위가 0이면 `k_fast`가 정의되지 않으므로, 그 캔들에서는 스토캐스틱 상태를 갱신하지 않습니다.

---

## 4. 신호 상태

### 4.1 이벤트

마감 캔들마다 아래 이벤트를 판정해 상태를 갱신합니다. 이벤트가 없으면 이전 상태가 유지됩니다.

| 지표 | 이벤트 | 조건 (마감 캔들 기준) | 저장되는 상태 |
|------|--------|-----------------------|---------------|
| RSI | 과매도 | RSI < 31 | `over_state = OVER_SOLD` |
| RSI | 과매수 | RSI > 60 | `over_state = OVER_BOUGHT` |
| RSI | 골든크로스 | RSI가 시그널을 아래→위로 교차 | `cross_state = GOLDEN_CROSS` |
| RSI | 데드크로스 | RSI가 시그널을 위→아래로 교차 | `cross_state = DEAD_CROSS` |
| 스토캐스틱 | 과매도 | %K < 25 그리고 %D < 25 | `over_state = OVER_SOLD` |
| 스토캐스틱 | 과매수 | %K > 75 그리고 %D > 75 | `over_state = OVER_BOUGHT` |
| 스토캐스틱 | 골든/데드크로스 | %K가 %D를 교차 | `cross_state` |
| MACD 3종 | 골든/데드크로스 | MACD가 시그널을 교차 | `cross_state` |

- 상태와 함께 이벤트가 발생한 캔들 시각(`over_time`, `cross_time`)을 저장합니다.
- 지표의 현재 값(`value`, `signal`)은 이벤트와 관계없이 매 캔들 갱신합니다.
- 마지막으로 처리한 캔들 이후의 마감 캔들은 **하나도 빠짐없이 순서대로** 처리합니다. 처리가 늦어져도 크로스를 놓치지 않습니다.

### 4.2 상태의 의미

마감 캔들을 빠짐없이 처리하므로 저장된 상태는 아래 뜻을 유지합니다. 상태에는 유효 기간을 두지 않습니다.

| 상태 | 의미 |
|------|------|
| `cross_state = GOLDEN_CROSS` | 지금 메인선이 시그널선 위에 있음 |
| `cross_state = DEAD_CROSS` | 지금 메인선이 시그널선 아래에 있음 |
| `over_state = OVER_SOLD` | 마지막으로 찍은 극단값이 과매도 |
| `over_state = OVER_BOUGHT` | 마지막으로 찍은 극단값이 과매수 |

- 크로스 상태는 오래전 크로스라도 그 뒤 반대 크로스가 없었다면 현재 상태와 같습니다.
- 과매도가 오래 이어지면 과매도 상태가 계속 유지되고, 과매수에 들어가면 상태가 바뀝니다.
- 상태가 지워지는 경우는 [상태 초기화](#7-상태-초기화)뿐입니다.

### 4.3 지표별 `value` / `signal`

| 지표 | `value` (메인선) | `signal` (시그널선) |
|------|------------------|---------------------|
| RSI | RSI | RSI 시그널 |
| 스토캐스틱 | slow %K | slow %D |
| MACD 3종 | MACD | MACD 시그널 |

---

## 5. 매매 판단

### 5.1 판단 순서

캔들 마감 직후 마켓마다 아래 순서로 판단합니다. 마켓당 주문은 한 번에 최대 1건입니다.

```
if 보유 중:
    if 손절 조건:             손절 매도 → 종료
    if 과매수 매도 조건:       매도 → 종료
    if 데드크로스 매도 조건:   매도 → 종료
    if 추가 매수 조건:         추가 매수 → 종료
else:
    if 신규 매수 조건:         신규 매수 → 종료
```

- 손실이 큰 상태에서 추가 매수하지 않도록 손절과 매도를 먼저 확인합니다.
- 해당 마켓에 체결이 끝나지 않은 주문이 있으면 판단하지 않습니다.

### 5.2 순수익률

보유 중인 포지션의 수익률은 매도 수수료까지 뺀 값입니다.

```
순수익률(%) = (기준 가격 × (1 - fee_rate) - 평단가) / 평단가 × 100
평단가     = (매수 체결 금액 합계 + 매수 수수료 합계) / 매수 체결 수량 합계
```

- 기준 가격: 실시간 손절은 현재가, 캔들 마감 직후 판단은 마감 캔들 종가
- 평단가는 체결 기준 가중평균이며 매수 수수료가 포함되어 있습니다. (`POSITION.avg_buy_price`)

### 5.3 손절

| 사유 (`reason`) | 조건 |
|-----------------|------|
| `STOP_LOSS` | 순수익률 ≤ `stop_loss_rate` |

- 실시간 현재가로 계속 확인하고, 캔들 마감 직후에도 마감 캔들 종가로 확인합니다.
- 조건을 충족하면 전량 매도합니다.

### 5.4 신호 매도

손절에 해당하지 않을 때 확인합니다. 처음 충족된 조건으로 **전량 매도**합니다.

| 순서 | 사유 (`reason`) | 조건 |
|------|-----------------|------|
| 1 | `OVERBOUGHT_EXIT` | 스토캐스틱 `over_state = OVER_BOUGHT` 그리고 RSI `over_state = OVER_BOUGHT` 그리고 순수익률 > `min_profit_rate` |
| 2 | `DEAD_CROSS_EXIT` | 스토캐스틱 `cross_state = DEAD_CROSS` 그리고 RSI `cross_state = DEAD_CROSS` 그리고 순수익률 > `min_profit_rate` |

- 신호 매도는 순수익일 때만 하고, 손실 상태의 매도는 손절로만 합니다.

### 5.5 매수

**신규 매수 (미보유)**: 9개 모두 참

| # | 조건 |
|---|------|
| 1 | RSI `over_state = OVER_SOLD` |
| 2 | RSI `cross_state = GOLDEN_CROSS` |
| 3 | 스토캐스틱 `over_state = OVER_SOLD` |
| 4 | 스토캐스틱 `cross_state = GOLDEN_CROSS` |
| 5 | MACD short `cross_state = GOLDEN_CROSS` |
| 6 | MACD mid `cross_state = GOLDEN_CROSS` |
| 7 | MACD long `cross_state = GOLDEN_CROSS` |
| 8 | 스토캐스틱 현재 %D < `stochastic_entry_max`(50) |
| 9 | 스토캐스틱 현재 %K < `stochastic_entry_max`(50) |

**추가 매수 (보유 중)**: 6개 모두 참

| # | 조건 |
|---|------|
| 1 | RSI `cross_state = GOLDEN_CROSS` |
| 2 | 스토캐스틱 `over_state = OVER_SOLD` |
| 3 | 스토캐스틱 `cross_state = GOLDEN_CROSS` |
| 4 | MACD short `cross_state = GOLDEN_CROSS` |
| 5 | MACD mid `cross_state = GOLDEN_CROSS` |
| 6 | MACD long `cross_state = GOLDEN_CROSS` |

조건이 모두 참이어도 [6. 주문 금액](#6-주문-금액)의 확인을 통과해야 주문합니다.

---

## 6. 주문 금액

금액은 모두 마켓의 호가 통화 단위입니다. (KRW 마켓이면 원, BTC 마켓이면 BTC)

### 6.1 매수 금액

| 구분 | 주문 금액 |
|------|-----------|
| 신규 매수 | `base_amount` |
| n번째 추가 매수 | `base_amount × 2 × n` (n = 지금까지 체결된 매수 횟수) |

- 매수 횟수는 **체결된 매수**만 셉니다. 주문하지 않았거나 체결되지 않은 매수는 세지 않습니다.
- 포지션을 새로 열면 매수 횟수는 0부터 시작합니다.

`base_amount` = 10,000원 기준

| 매수 | 체결된 매수 횟수 (주문 전) | 주문 금액 | 누적 매입금액 |
|------|---------------------------|-----------|---------------|
| 신규 | 0 | 10,000 | 10,000 |
| 추가 1 | 1 | 20,000 | 30,000 |
| 추가 2 | 2 | 40,000 | 70,000 |
| 추가 3 | 3 | 60,000 | 130,000 |
| 추가 4 | 4 | 80,000 | 210,000 |
| 추가 5 | 5 | - | `max_add_buys` = 4 초과로 주문 안 함 |

### 6.2 주문 전 확인

아래를 모두 통과해야 매수 주문을 냅니다. 하나라도 맞지 않으면 주문하지 않고, 매수 횟수와 신호 상태도 바뀌지 않습니다.

| 확인 | 조건 |
|------|------|
| 추가 매수 횟수 | 지금까지의 추가 매수 횟수 < `max_add_buys` |
| 포지션 총액 | 현재 매입금액(수수료 포함) + 주문 금액 ≤ `max_position_amount` |
| 잔고 | 호가 통화 주문 가능 잔고 ≥ 주문 금액 × (1 + `fee_rate`) |
| 최소 주문 금액 | 주문 금액 ≥ `min_order_amount` |

### 6.3 매도 수량

- 포지션의 보유 수량 전량을 매도합니다.
- 매도 후 남은 수량의 가치가 `min_order_amount` 미만이면 포지션을 닫습니다.

---

## 7. 상태 초기화

신호 상태(`over_state`, `cross_state`)를 비워, 다음 판단에 **새로 발생한 신호**만 쓰이게 합니다. 지표의 현재 값과 시각은 그대로 둡니다.

| 시점 | 초기화 대상 |
|------|-------------|
| 매수 체결 | RSI, 스토캐스틱, MACD 3종 |
| 매도 체결 (신호 매도, 손절) | RSI, 스토캐스틱, MACD 3종 |
| MACD 3종이 모두 데드크로스 상태가 됨 | RSI, 스토캐스틱 |

- 매수·매도 초기화는 주문이 **체결된 뒤**에 합니다. 체결되지 않은 주문으로는 상태를 지우지 않습니다.
- 매수 후에는 MACD 3종이 비워지므로, 다음 추가 매수에는 MACD 3종의 골든크로스가 새로 발생해야 합니다.
- 매도 후에는 과매수 상태가 비워지므로, 같은 신호로 다시 매도하지 않습니다.
- MACD 초기화는 마감 캔들을 처리한 뒤 3종의 `cross_state`가 모두 `DEAD_CROSS`이고, 그중 하나라도 이번 캔들에서 바뀌었을 때 실행합니다. MACD가 계속 데드크로스 상태인 동안 매 캔들 반복해서 초기화하지 않기 위해서입니다.
