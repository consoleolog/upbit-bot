# 기존 매매 로직

기존 AutoTrader(업비트 봇)의 매매 로직을 코드 기준으로 정리한 문서입니다.
DB 구조는 [기존 DB 스키마](../database/legacy-schema.md)를 참고하세요.

> - 코드 위치는 기존 저장소 기준입니다. (예: `trader/trader.py:209`)
> - 설정값은 운영에 사용한 `config.upbit.json` 기준입니다.

---

## 목차

1. [전략 요약](#1-전략-요약)
2. [실행 구조](#2-실행-구조)
3. [설정값](#3-설정값)
4. [캔들 수집](#4-캔들-수집)
5. [지표 계산](#5-지표-계산)
6. [신호 상태 갱신](#6-신호-상태-갱신)
7. [매매 판단](#7-매매-판단)
8. [매수 실행](#8-매수-실행)
9. [매도 실행](#9-매도-실행)
10. [상태 초기화 (refresh)](#10-상태-초기화-refresh)
11. [거래소 연동](#11-거래소-연동)
12. [예시 시나리오](#12-예시-시나리오)
13. [오류 처리와 동시성](#13-오류-처리와-동시성)
14. [알려진 문제점](#14-알려진-문제점)

---

## 1. 전략 요약

30분봉에서 RSI, 스토캐스틱, MACD 3종의 신호를 조합해 **과매도 구간에서 반등이 확인되면 매수**하고, **과매수 또는 수익 중 하락 전환이 확인되면 전량 매도**하는 전략입니다.

| 구분 | 내용 |
|------|------|
| 대상 | 업비트 KRW 마켓 7개 (`BTC`, `ETH`, `BCH`, `SOL`, `AAVE`, `BSV`, `ENS`) |
| 캔들 | 30분봉, 최근 200개 |
| 지표 | EMA 5 / 8 / 13, MACD 3종, RSI(14), 스토캐스틱 slow(14, 3, 4) |
| 실행 주기 | 30분마다 모든 티커 판단 |
| 신규 매수 | 1만 원 시장가 매수 |
| 추가 매수 (물타기) | 2만, 4만, 6만, 8만 원 … 으로 증가 |
| 매도 | 보유 수량 전량 시장가 매도 |
| 손절 | 없음 (코드에 주석 처리되어 있음) |

신호는 매 주기 새로 판단하지 않고 **DB에 마지막 상태를 저장(래치)** 해두고 판단합니다.
예를 들어 RSI가 과매도에 한 번 들어가면, 다른 상태로 바뀌거나 초기화되기 전까지 "과매도" 상태가 유지됩니다.
여러 지표의 신호가 **서로 다른 시점에 발생해도** 모두 쌓이면 매수 조건이 충족됩니다.

---

## 2. 실행 구조

### 2.1 구성 요소

```
trade.py (진입점)
 ├─ ConfigParser      config.upbit.json + CLI 인자로 설정 구성
 ├─ Exchange          거래소 API (ccxt + pyupbit)            modules/exchange.py
 ├─ DataGenerator     캔들 조회 + 지표 계산                  modules/data_generator.py
 ├─ TradeRepository   PostgreSQL 신호·매매 상태 저장          modules/trade_repository.py
 └─ Trader            신호 갱신, 매매 판단, 주문             trader/trader.py
```

### 2.2 실행 순서

1. **설정 로드** (`trade.py:51-95`)
   - `-c config.upbit.json`의 값을 읽고, CLI 인자(`--sp`, `--t`, `--s` 등)가 있으면 덮어씁니다.
   - 운영 실행 명령 (docker-compose): `python trade.py -c config.upbit.json -p 8000 --s upbit`
2. **객체 생성** (`trade.py:16-28`)
   - 설정의 `type`, `args`로 `Exchange`, `DataGenerator`, `TradeRepository`를 만들고 `Trader`에 넘깁니다.
3. **FastAPI 서버 시작** (`trade.py:38-48`)
   - 엔드포인트는 없고, 서버 생명주기(lifespan)에 맞춰 스케줄러를 시작·종료하는 용도로만 사용합니다.
4. **시작 시 초기화** (`trade.py:41`, `trader/trader.py:320`)
   - `init_trade_info(tickers)`: 티커마다 `TRADE_INFO`, `RSI_INFO`, `STOCHASTIC_INFO`, `MACD_INFO`에 빈 행을 INSERT합니다.
   - 이미 행이 있으면 오류가 출력되고 무시됩니다.
5. **스케줄러 시작** (`trade.py:30-36`)
   - APScheduler `BackgroundScheduler`로 `Trader.loop(tickers)`를 `interval = 30분`마다 실행합니다.
   - interval 트리거라 **첫 실행은 서버 시작 30분 뒤**이고, 이후 시작 시각 기준 30분 간격입니다. 캔들 마감 시각(정각, 30분)과는 맞춰져 있지 않습니다.

### 2.3 한 주기의 흐름

```
Trader.loop(tickers)                                   trader/trader.py:309
 └─ 스레드 4개로 티커마다 Trader.trading(ticker) 실행
     1. 캔들 200개 조회 + 지표 계산                       DataGenerator.load
     2. EMA 배열 단계 계산 (출력만 하고 판단에 사용 안 함)   DataGenerator.get_stage
     3. 신호 상태 갱신 (RSI → 스토캐스틱 → MACD 순서)      Trader.update_args
     4. DB에서 신호 상태 조회 (RSI, 스토캐스틱, MACD)
     5. 보유 수량 조회
     6. 매수 조건 확인 → 충족 시 매수 후 종료
     7. 보유 중이면 매도 조건 확인 → 충족 시 매도
 └─ 티커별 결과 dict 출력 (pprint)
```

---

## 3. 설정값

`config.upbit.json` 기준입니다.

### 3.1 대상 티커와 주문 금액

| 항목 | 값 |
|------|----|
| `tickers` (매매 대상) | `BTC/KRW`, `ETH/KRW`, `BCH/KRW`, `SOL/KRW`, `AAVE/KRW`, `BSV/KRW`, `ENS/KRW` |
| `trader.price_key` (기본 주문 금액) | 티커별 10,000원 (`COMP/KRW`도 있지만 매매 대상에서 제외됨) |

### 3.2 지표 파라미터 (`data_generator.args`)

| 항목 | 값 | 의미 |
|------|----|------|
| `short_period` | 5 | 단기 EMA 기간 |
| `mid_period` | 8 | 중기 EMA 기간 |
| `long_period` | 13 | 장기 EMA 기간 |
| `rsi_period` | 14 | RSI 기간 |
| `k_len` | 14 | 스토캐스틱 %K 기간 |
| `k_smooth` | 3 | slow %K 평활 기간 |
| `d_smooth` | 4 | slow %D 평활 기간 |
| `timeframe` | `30m` | 캔들 단위 |

### 3.3 과매수 / 과매도 기준 (`trader`)

| 지표 | 과매도 | 과매수 |
|------|--------|--------|
| RSI | 31 미만 | 60 초과 |
| 스토캐스틱 | %K, %D 모두 25 미만 | %K, %D 모두 75 초과 |

### 3.4 기타

| 항목 | 값 | 의미 |
|------|----|------|
| `server.interval` | 30 | 실행 주기 (분) |
| `trader.verbosity`, `server.verbosity` | 1, 2 | 로그 레벨 |

---

## 4. 캔들 수집

`Exchange.get_candles` (`modules/exchange.py:78`)

- ccxt `fetch_ohlcv(symbol, timeframe)`를 `limit` 없이 호출합니다. ccxt 4.4.42 업비트 구현의 기본값으로 **최근 200개** 캔들을 받습니다.
- 컬럼: `datetime`(캔들 시작 시각, epoch ms), `open`, `high`, `low`, `close`, `volume`
- 인덱스는 `datetime`을 한국 시간(KST)으로 바꾼 값입니다.
- **마지막 행(`iloc[-1]`)은 아직 진행 중인 캔들**입니다. 이후 모든 지표 판단은 이 진행 중인 캔들 기준으로 이뤄집니다.

---

## 5. 지표 계산

`DataGenerator.load` (`modules/data_generator.py:46`)에서 캔들 데이터프레임에 지표 컬럼을 추가합니다.
모든 지표는 종가(`close`) 기준이며, 스토캐스틱만 고가·저가를 함께 사용합니다.

### 5.1 EMA

```python
EMA(series, period) = series.ewm(span=period, adjust=False).mean()
```

| 컬럼 | 계산 |
|------|------|
| `EMA_SHORT` | EMA(close, 5) |
| `EMA_MIDDLE` | EMA(close, 8) |
| `EMA_LONG` | EMA(close, 13) |

### 5.2 MACD 3종

```python
MACD(data, a, b):
    val    = EMA(close, a) - EMA(close, b)     # MACD 선
    signal = EMA(val, 9)                        # 시그널 선
    oscillator = val - signal
    gc = (val[-2] < signal[-2]) and (val[-1] > signal[-1])   # 골든크로스
    dc = (val[-2] > signal[-2]) and (val[-1] < signal[-1])   # 데드크로스
```

| 이름 | 계산 | 설정값 기준 |
|------|------|------------|
| MACD short | EMA(short) − EMA(mid) | EMA 5 − EMA 8 |
| MACD mid | EMA(short) − EMA(long) | EMA 5 − EMA 13 |
| MACD long | EMA(mid) − EMA(long) | EMA 8 − EMA 13 |

- 시그널 기간은 3종 모두 9로 고정입니다.
- 크로스는 **직전 캔들과 현재(진행 중) 캔들 사이**에서 MACD 선이 시그널 선을 넘었는지로 판정합니다.

### 5.3 RSI

```python
delta = close.diff()
U = max(delta, 0),  D = max(-delta, 0)
AU = U.ewm(com=period - 1).mean()        # Wilder 평균 (alpha = 1/period)
AD = D.ewm(com=period - 1).mean()
rs = AU / AD                              # inf → NaN → 0 으로 치환
RSI = 100 - 100 / (1 + rs)
signal = EMA(RSI, 9)
gc / dc = RSI 가 signal 을 위로 / 아래로 교차
```

- RSI 자체의 기준선 돌파가 아니라 **RSI와 RSI의 EMA(9) 시그널의 교차**를 크로스로 봅니다.

### 5.4 스토캐스틱 (slow)

```python
low_n   = low.rolling(14).min()
high_n  = high.rolling(14).max()
k_fast  = (close - low_n) / (high_n - low_n) * 100
k_slow  = k_fast.rolling(3).mean()        # slow %K
d_slow  = k_slow.rolling(4).mean()        # slow %D
gc / dc = k_slow 가 d_slow 를 위로 / 아래로 교차
```

- 코드에서는 `d_fast`를 `k_fast.rolling(k_smooth).mean()`으로 따로 계산하지만 `k_slow`와 같은 값입니다. 결과적으로 slow %D = slow %K의 4기간 단순이동평균입니다.

### 5.5 EMA 배열 단계 (사용 안 함)

`DataGenerator.get_stage` (`modules/data_generator.py:24`)는 현재 캔들의 EMA 단기·중기·장기 순서로 단계를 계산합니다.

| 단계 | 배열 |
|------|------|
| 1 | 단기 > 중기 > 장기 (정배열) |
| 2 | 중기 > 단기 > 장기 |
| 3 | 중기 > 장기 > 단기 |
| 4 | 장기 > 중기 > 단기 (역배열) |
| 5 | 장기 > 단기 > 중기 |
| 6 | 단기 > 장기 > 중기 |
| 0 | 값이 같은 경우 등 위에 해당하지 않음 |

결과에 `stage`로 출력만 하고 매매 판단에는 사용하지 않습니다.

---

## 6. 신호 상태 갱신

`Trader.update_args` (`trader/trader.py:203`)가 RSI → 스토캐스틱 → MACD 순서로 실행합니다.
모두 **현재(진행 중) 캔들** 값으로 판단하고, 조건에 해당할 때만 DB를 UPDATE합니다.
해당하지 않으면 DB의 이전 상태가 그대로 유지됩니다.

DB에 저장하는 시각(`*_time`)은 현재 캔들의 시작 시각(`datetime`, epoch ms)입니다.

### 6.1 RSI (`update_rsi`, `trader/trader.py:89`)

| 조건 (현재 캔들) | DB 갱신 (`RSI_INFO`) |
|------------------|----------------------|
| RSI < 31 | `rsi_over = OVER_SOLD`, `over_time`, `rsi` |
| RSI > 60 | `rsi_over = OVER_BOUGHT`, `over_time`, `rsi` |
| RSI 골든크로스 | `rsi_cross = GOLDEN_CROSS`, `cross_time`, `rsi` |
| RSI 데드크로스 | `rsi_cross = DEAD_CROSS`, `cross_time`, `rsi` |

- 과매수/과매도 조건과 크로스 조건은 각각 따로 확인하므로, 한 주기에 둘 다 갱신될 수 있습니다.

### 6.2 스토캐스틱 (`update_stochastic`, `trader/trader.py:31`)

| 조건 (현재 캔들) | DB 갱신 (`STOCHASTIC_INFO`) |
|------------------|-----------------------------|
| %K < 25 **그리고** %D < 25 | `stochastic_over = OVER_SOLD`, `over_time`, `k_slow`, `d_slow` |
| 아니면 %K > 75 **그리고** %D > 75 | `stochastic_over = OVER_BOUGHT`, `over_time`, `k_slow`, `d_slow` |
| %K 골든크로스 | `stochastic_cross = GOLDEN_CROSS`, `cross_time`, `k_slow`, `d_slow` |
| 아니면 %K 데드크로스 | `stochastic_cross = DEAD_CROSS`, `cross_time`, `k_slow`, `d_slow` |

- %K, %D 중 하나만 기준을 넘으면 과매수/과매도로 보지 않습니다.
- `k_slow`, `d_slow`는 이벤트가 발생할 때만 저장되므로 DB 값은 **마지막 이벤트 시점의 값**입니다.

### 6.3 MACD (`update_macd`, `trader/trader.py:132`)

1. **MACD 3종이 현재 캔들에서 모두 데드크로스**이면 `RSI_INFO`, `STOCHASTIC_INFO`의 상태를 초기화합니다. ([10. 상태 초기화](#10-상태-초기화-refresh))
2. 이어서 MACD 3종을 각각 갱신합니다.

| 조건 (현재 캔들) | DB 갱신 (`MACD_INFO`) |
|------------------|-----------------------|
| MACD short 골든크로스 / 아니면 데드크로스 | `short_cross`, `short_time` |
| MACD mid 골든크로스 / 아니면 데드크로스 | `mid_cross`, `mid_time` |
| MACD long 골든크로스 / 아니면 데드크로스 | `long_cross`, `long_time` |

- MACD 값 자체는 저장하지 않고 크로스 방향만 저장합니다.
- 3종이 **같은 캔들에서 동시에** 데드크로스가 나는 경우는 드물어 1번 초기화는 거의 일어나지 않습니다.

### 6.4 래치 상태의 의미

크로스 상태는 마지막으로 발생한 크로스 방향이 유지되므로, 크로스를 놓치지 않았다면 아래와 같은 뜻이 됩니다.

| DB 상태 | 의미 |
|---------|------|
| MACD `*_cross = GOLDEN_CROSS` | 현재 MACD 선이 시그널 선 위에 있음 |
| RSI `rsi_cross = GOLDEN_CROSS` | 현재 RSI가 RSI 시그널 위에 있음 |
| 스토캐스틱 `stochastic_cross = GOLDEN_CROSS` | 현재 %K가 %D 위에 있음 |
| `rsi_over = OVER_SOLD` | 마지막으로 찍은 극단값이 과매도 (지금도 과매도라는 뜻은 아님) |
| `stochastic_over = OVER_SOLD` | 마지막으로 찍은 극단값이 과매도 |

---

## 7. 매매 판단

`Trader.trading` (`trader/trader.py:209`)

신호 상태를 갱신한 뒤 DB에서 RSI, 스토캐스틱, MACD 상태를 다시 읽고, 거래소에서 **해당 코인의 주문 가능 수량(`balance`)** 을 조회해 판단합니다.

```
balance = 주문 가능 수량 (조회 실패 시 0)

if balance != 0:                      # 보유 중
    if 추가 매수 조건: 매수 → 종료
else:                                 # 미보유
    if 신규 매수 조건: 매수 → 종료

if balance != 0:                      # 보유 중이고 매수하지 않았으면
    profit = 수익률(%)
    if 매도 조건 1: 전량 매도 → 종료
    if 매도 조건 2: 전량 매도 → 종료
```

### 7.1 신규 매수 조건 (미보유, `trader/trader.py:238-250`)

아래 **9개가 모두** 참이어야 합니다.

| # | 조건 | 의미 |
|---|------|------|
| 1 | `rsi_over == OVER_SOLD` | RSI가 마지막으로 과매도에 들어갔음 |
| 2 | `rsi_cross == GOLDEN_CROSS` | RSI가 시그널 위에 있음 |
| 3 | `stochastic_over == OVER_SOLD` | 스토캐스틱이 마지막으로 과매도에 들어갔음 |
| 4 | `stochastic_cross == GOLDEN_CROSS` | %K가 %D 위에 있음 |
| 5 | `short_cross == GOLDEN_CROSS` | MACD short 상승 |
| 6 | `mid_cross == GOLDEN_CROSS` | MACD mid 상승 |
| 7 | `long_cross == GOLDEN_CROSS` | MACD long 상승 |
| 8 | DB `d_slow < 50` | 마지막 스토캐스틱 이벤트 시점의 %D가 50 미만 |
| 9 | DB `k_slow < 50` | 마지막 스토캐스틱 이벤트 시점의 %K가 50 미만 |

### 7.2 추가 매수 조건 (보유 중, `trader/trader.py:223-236`)

아래 **6개가 모두** 참이어야 합니다. 신규 매수 조건에서 1, 8, 9번이 빠진 것입니다.

| # | 조건 |
|---|------|
| 1 | `rsi_cross == GOLDEN_CROSS` |
| 2 | `stochastic_over == OVER_SOLD` |
| 3 | `stochastic_cross == GOLDEN_CROSS` |
| 4 | `short_cross == GOLDEN_CROSS` |
| 5 | `mid_cross == GOLDEN_CROSS` |
| 6 | `long_cross == GOLDEN_CROSS` |

- 추가 매수는 수익·손실 여부를 보지 않습니다. 가격이 올라 있어도 조건이 맞으면 추가 매수합니다.
- 매수 조건이 충족되면 같은 주기에 매도 조건은 확인하지 않습니다.

### 7.3 매도 조건 (보유 중이고 매수하지 않은 경우, `trader/trader.py:255-268`)

| 순서 | 조건 | 비고 |
|------|------|------|
| 1 | `stochastic_over == OVER_BOUGHT` **그리고** `rsi_over == OVER_BOUGHT` | 수익률과 관계없이 매도 (손실이어도 매도) |
| 2 | 수익률 > 0 **그리고** `stochastic_cross == DEAD_CROSS` **그리고** `rsi_cross == DEAD_CROSS` | 수익 중일 때만 |

- 1번을 먼저 확인하고, 충족되지 않으면 2번을 확인합니다.
- 손절 조건(MACD 3종 모두 데드크로스이고 손실 중)은 주석 처리되어 있어 동작하지 않습니다.

### 7.4 수익률 계산 (`get_profit`, `trader/trader.py:314`)

```
수익률(%) = (현재가 - TRADE_INFO.price) / TRADE_INFO.price × 100
```

- `TRADE_INFO.price`는 봇이 기록한 평단가입니다. ([8.2 평단가 기록](#82-평단가-기록))
- 현재가는 pyupbit `get_current_price`로 조회합니다.
- 수수료는 반영하지 않습니다.

---

## 8. 매수 실행

`Trader.buy_and_update` (`trader/trader.py:285`)

### 8.1 주문 금액

`TRADE_INFO.status`(마지막 매매 방향)로 신규 매수와 추가 매수를 구분합니다.

| `status` | 구분 | 주문 금액 |
|----------|------|-----------|
| `bid` (마지막이 매수) | 추가 매수 | `price_key × 2 × counts[ticker]`, 계산 후 `counts[ticker] += 1` |
| 그 외 (`ask`, NULL) | 신규 매수 | `price_key` |

`counts`는 티커별 카운터로 **메모리에만** 있고 초기값은 1입니다.

| 매수 순서 | counts (계산 시점) | 주문 금액 (`price_key` = 10,000원) | 누적 투입 |
|-----------|-------------------|-----------------------------------|-----------|
| 신규 매수 | - | 10,000원 | 10,000원 |
| 추가 1회 | 1 | 20,000원 | 30,000원 |
| 추가 2회 | 2 | 40,000원 | 70,000원 |
| 추가 3회 | 3 | 60,000원 | 130,000원 |
| 추가 4회 | 4 | 80,000원 | 210,000원 |

- 추가 매수 횟수나 총 투입 금액에 상한이 없습니다. KRW 잔고가 부족해질 때까지 계속됩니다.
- `counts`는 매도 후에도 1로 돌아가지 않고, 서버를 재시작해야 1이 됩니다.

### 8.2 평단가 기록

| 구분 | `TRADE_INFO.price`에 저장하는 값 |
|------|-------------------------------|
| 신규 매수 | 현재가 |
| 추가 매수 | `(기존 price + 현재가) / 2` |

- 매수 금액을 반영하지 않은 단순 평균입니다. 추가 매수 금액이 더 크기 때문에 실제 평단가와 차이가 납니다.

### 8.3 실행 순서

1. `TRADE_INFO` 조회, 주문 가능 KRW 조회
2. 주문 금액, 평단가 계산 (추가 매수면 이 시점에 `counts += 1`)
3. **KRW 잔고 > 주문 금액**일 때만 아래를 실행
   1. 신호 상태 초기화 (`refresh`, RSI·스토캐스틱·MACD 모두)
   2. `TRADE_INFO`에 `price = 평단가`, `status = 'bid'`, `updated_at = NOW()` 저장
   3. 업비트 시장가 매수 주문 (주문 금액만큼 KRW로 매수)
4. 주문 결과는 확인하지 않습니다.

---

## 9. 매도 실행

`Trader.sell_and_update` (`trader/trader.py:278`)

1. `TRADE_INFO`에 `price = 현재가`, `status = 'ask'`, `updated_at = NOW()` 저장
2. 신호 상태 초기화 (`refresh`, RSI·스토캐스틱·MACD 모두)
3. 업비트 시장가 매도 주문 (조회한 **주문 가능 수량 전체**)
4. 주문 결과는 확인하지 않습니다.

---

## 10. 상태 초기화 (refresh)

초기화는 `*_over`, `*_cross` 상태 컬럼만 NULL로 바꾸고, `*_time`과 값 컬럼(`rsi`, `k_slow`, `d_slow`)은 그대로 둡니다.

| 시점 | 초기화 대상 | 코드 |
|------|-------------|------|
| 매수 실행 (KRW 잔고 충분할 때) | RSI, 스토캐스틱, MACD 3종 | `trader/trader.py:300` |
| 매도 실행 | RSI, 스토캐스틱, MACD 3종 | `trader/trader.py:282` |
| MACD 3종이 같은 캔들에서 모두 데드크로스 | RSI, 스토캐스틱 | `trader/trader.py:145-147` |

초기화의 효과

- **매수 후**: MACD 3종이 모두 NULL이 되므로, 다음 추가 매수에는 MACD 3종의 골든크로스가 **새로** 발생해야 합니다. RSI·스토캐스틱도 새 신호가 필요합니다.
- **매도 후**: 과매수 상태가 지워지므로, 매도 직후 같은 신호로 다시 매도하지 않습니다. 다음 신규 매수에도 모든 신호가 새로 발생해야 합니다.

---

## 11. 거래소 연동

`Exchange` (`modules/exchange.py`)는 ccxt와 pyupbit를 함께 사용합니다.

| 기능 | 메서드 | 사용 라이브러리 | 동작 |
|------|--------|-----------------|------|
| 캔들 조회 | `get_candles` | ccxt `fetch_ohlcv` | 최근 200개 |
| 보유 수량 | `get_balance` | ccxt `fetch_balance` | 해당 코인의 `free`(주문 가능 수량). 오류나 미보유 시 0 |
| KRW 잔고 | `get_krw` | ccxt `fetch_balance` | KRW `free` |
| 현재가 | `get_current_price` | pyupbit `get_current_price` | |
| 매수 | `create_buy_order` | pyupbit `buy_market_order` | 시장가, KRW 금액 지정 |
| 매도 | `create_sell_order` | pyupbit `sell_market_order` | 시장가, 수량 지정 |

- 티커 표기: 내부는 ccxt 형식(`BTC/KRW`), pyupbit 호출 시 업비트 형식(`KRW-BTC`)으로 바꿉니다.
- 주문 메서드의 반환값(주문 uuid 등)은 사용하지 않습니다.

---

## 12. 예시 시나리오

한 티커(BTC)의 상태 변화를 시간 순으로 보여주는 예시입니다. 시각은 스케줄러 실행 시각입니다.

| 시각 | 현재 캔들에서 발생한 이벤트 | DB 상태 (RSI / 스토캐스틱 / MACD s·m·l) | 동작 |
|------|-----------------------------|----------------------------------------|------|
| 09:10 | RSI 28, %K·%D 20 / 18 | over: 과매도 / 과매도, cross: - / - / -·-·- | 없음 |
| 09:40 | 스토캐스틱 골든크로스 | cross: - / GC / -·-·- | 없음 |
| 10:10 | RSI 골든크로스, MACD short·mid 골든크로스 | cross: GC / GC / GC·GC·- | 없음 (MACD long 없음) |
| 10:40 | MACD long 골든크로스 | cross: GC / GC / GC·GC·GC, %K·%D(마지막 이벤트 시점) < 50 | **신규 매수 1만 원**, 상태 전부 초기화 |
| … | 가격 하락, 스토캐스틱 과매도, 각종 골든크로스 재발생 | 추가 매수 조건 충족 | **추가 매수 2만 원**, 초기화 |
| … | RSI 65, %K·%D 80 / 77 | over: 과매수 / 과매수 | **전량 매도** (수익률과 무관), 초기화 |

- 신호가 서로 다른 캔들(09:10 ~ 10:40)에서 발생했지만 래치되어 있어 매수가 실행됩니다.
- 09:10의 과매도 신호는 이후 과매수로 바뀌거나 초기화되기 전까지 계속 유효합니다.

---

## 13. 오류 처리와 동시성

| 항목 | 동작 |
|------|------|
| 스레드 | `ThreadPoolExecutor(max_workers=4)`로 티커를 병렬 처리 |
| DB 연결 | psycopg2 연결 1개를 모든 스레드가 공유 (조회는 SQLAlchemy 엔진 사용) |
| DB 쓰기 오류 | `catch_db_exception`이 오류를 `print`하고 rollback한 뒤 무시 |
| 티커 처리 중 예외 | `loop`에서 `f.result()`가 예외를 다시 던져, 그 뒤 티커들의 결과 출력이 중단됨. 스케줄러가 예외를 기록하고 다음 주기에 다시 실행 |
| 실행이 길어질 때 | APScheduler 기본 설정(`max_instances=1`)으로 이전 실행이 끝나지 않으면 이번 실행을 건너뜀 |
| 로그 | `Trader`는 logger를 만들지만 매매 기록에는 사용하지 않고, 결과는 `pprint`로만 출력 |

---

## 14. 알려진 문제점

### 14.1 신호 판정

| 문제 | 내용 | 위치 |
|------|------|------|
| 진행 중인 캔들로 판단 | `iloc[-1]`이 마감되지 않은 캔들이라 크로스가 생겼다가 사라질 수 있음 (리페인트) | `trader/trader.py` 전반 |
| 실행 시각과 캔들 마감 불일치 | 30분 간격이 서버 시작 시각 기준이라, 크로스가 발생한 캔들을 확인하지 못하고 지나가거나 진행 중 상태만 보고 기록할 수 있음. 놓친 크로스는 래치 상태를 실제와 어긋나게 함 | `trade.py:31-36` |
| 신호 시점 무관 | 신호 발생 시각(`*_time`)을 저장하지만 판단에 사용하지 않아, 오래된 과매도 신호와 최근 크로스가 합쳐져 매수될 수 있음 | `trader/trader.py:223-250` |
| %K, %D 조건이 과거 값 | 신규 매수의 `k_slow < 50`, `d_slow < 50`이 현재 값이 아니라 마지막 이벤트 시점의 DB 값 | `trader/trader.py:247-248` |
| MACD 삼중 데드크로스 초기화 | 3종이 같은 캔들에서 동시에 데드크로스가 나야 해서 사실상 동작하지 않음 | `trader/trader.py:145` |
| RSI 계산 오류 (경계값) | 하락분 평균(AD)이 0이면 RSI가 100이 아니라 0이 됨 (`inf → 0` 치환 때문). 상승만 있는 데이터에서 확인됨 | `modules/data_generator.py:131-133` |
| 스토캐스틱 NaN | 14개 캔들의 고가·저가가 모두 같으면 0으로 나눠 NaN. 이 경우 이벤트가 발생하지 않음 | `modules/data_generator.py:147` |

### 14.2 포지션·주문

| 문제 | 내용 | 위치 |
|------|------|------|
| 평단가 단순 평균 | 추가 매수 금액이 커질수록 실제 평단가와 차이가 커지고, 수익률과 매도 판단이 왜곡됨 | `trader/trader.py:292-294` |
| `counts` 미초기화 | 매도 후에도 리셋되지 않아 다음 사이클 첫 추가 매수가 큰 금액부터 시작. 재시작하면 1로 돌아감 | `trader/trader.py:27-29, 291` |
| KRW 부족 시에도 `counts` 증가 | 주문 금액 계산 시점에 증가하므로 잔고 부족으로 주문하지 않아도 다음 금액이 커짐 | `trader/trader.py:290-291` |
| 상한 없는 추가 매수 | 횟수·총액 제한이 없음 | `trader/trader.py:285-307` |
| 손절 없음 | 손절 조건이 주석 처리됨. 하락 추세에서 추가 매수만 반복 | `trader/trader.py:270-274` |
| 손실 매도 | 매도 조건 1(과매수)은 손실 중이어도 전량 매도 | `trader/trader.py:259-261` |
| 수수료 미반영 | 수익률 > 0 이어도 수수료(왕복 약 0.1%)를 빼면 손실일 수 있음 | `trader/trader.py:263, 314-318` |
| 주문 결과 미확인 | DB를 먼저 갱신하고 주문을 보내며 결과를 확인하지 않음. 주문 실패(최소 주문 금액 미달, API 오류)해도 DB에는 매수·매도로 기록되고 신호도 초기화됨 | `trader/trader.py:278-307` |
| 체결가 미사용 | 평단가·매도가로 체결가가 아닌 주문 직전 현재가를 저장 | `trader/trader.py:280, 293, 297` |
| 보유 판정 | `free` 수량만 보므로 주문에 묶인 수량은 미보유로, 매도 후 남은 소량(dust)은 보유로 판단할 수 있음 | `modules/exchange.py:69-76` |
| `status`와 실제 보유 불일치 | 수동 매도 등으로 미보유인데 `status = 'bid'`이면 신규 매수가 추가 매수 금액(2배 이상)으로 실행됨 | `trader/trader.py:289` |

### 14.3 실행 환경

| 문제 | 내용 | 위치 |
|------|------|------|
| DB 연결 공유 | psycopg2 연결 하나를 스레드 4개가 함께 써서 한 스레드의 commit·rollback이 다른 스레드에 영향 | `modules/trade_repository.py:25` |
| 오류 무시 | DB 쓰기 오류를 출력만 하고 넘어가 상태가 어긋나도 알기 어려움 | `modules/trade_repository.py:11-20` |
| API 중복 호출 | 티커마다 잔고·현재가를 여러 번 조회 | `trader/trader.py` 전반 |
| 첫 실행 지연 | 서버 시작 후 30분이 지나야 첫 판단 | `trade.py:31-36` |
| 매매 이력 없음 | 결과를 `pprint`로만 출력하고 DB는 최신 상태만 덮어써서 매매 기록이 남지 않음 | `trader/trader.py:309-312` |
