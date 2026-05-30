# MEXC Futures Technical Analysis Assistant

Python-based technical analysis assistant for selected MEXC Futures symbols. It only reads public MEXC Futures market data, evaluates predefined technical criteria, stores the results in SQLite, and can notify a Telegram chat.

This project is not a trading bot. It does not use private MEXC endpoints, account data, API keys, leverage controls, position controls, or order endpoints.

## Tracked Markets

- `BTC_USDT`
- `ETH_USDT`
- `SOL_USDT`
- `XRP_USDT`
- `ADA_USDT`

Timeframes:

- `Hour4` for 4H candles
- `Day1` for daily candles

## Installation

```bash
cd mexc_ta_assistant
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```text
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
ENABLE_SUMMARY_MESSAGES=true
ENABLE_ALERT_MESSAGES=true
LOG_LEVEL=INFO
```

Telegram credentials are only used for Telegram Bot API `sendMessage`.

## Manual Execution

Run one timeframe:

```bash
python -m app.main --once --timeframe Hour4
python -m app.main --once --timeframe Day1
```

Run all configured timeframes:

```bash
python -m app.main --once --all
```

If Telegram is not configured, the analysis still runs and stores SQLite rows, but messages are not sent.

## Scheduler Execution

```bash
python -m app.main --scheduler
```

The scheduler runs in UTC:

- 4H analysis: 2 minutes after every 4-hour candle close.
- Daily analysis: 3 minutes after the daily candle close.

## Docker Usage

```bash
cd mexc_ta_assistant
cp .env.example .env
docker compose up --build
```

SQLite data is stored under `./data` through the compose volume.

## Scoring System

For every symbol and timeframe, both analytical directions are evaluated separately.

Criteria:

1. RSI
2. Stoch RSI
3. MACD histogram fading
4. Candlestick pattern
5. Supertrend

Labels:

- `0/5`, `1/5`, `2/5`, `3/5`: `Not suitable`
- `4/5`: `Criteria satisfied`
- `5/5`: `Strong technical alignment`

If both analytical directions score at least `4/5` for the same symbol and timeframe, the result is marked as `Conflicting signal`.

Every Telegram message ends with:

```text
Note: This is an automated technical analysis check and is not financial advice.
```

## Tests

```bash
cd mexc_ta_assistant
pytest
```

The tests cover indicator calculations, crossover and fading logic, Supertrend direction, rule labels, candlestick patterns, and SQLite duplicate prevention.

