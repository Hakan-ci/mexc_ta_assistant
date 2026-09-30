# MEXC Futures Technical Analysis Assistant

Python-based technical analysis assistant for selected MEXC Futures symbols. It only reads public MEXC Futures market data, evaluates predefined technical criteria, stores results in PostgreSQL (production) or SQLite (local development), and can notify a Telegram chat.

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

If Telegram is not configured, analysis still runs and stores results. Actionable notifications remain pending and blocked; the command exits nonzero. The scheduler requires Telegram credentials at startup.

## Scheduler Execution

```bash
python -m app.main --scheduler
```

The scheduler reconciles each timeframe independently at seconds `00` and `30`, and immediately at startup. Candles become eligible two minutes after their UTC close. Completed candles are checked in the database before fetching MEXC data. If MEXC has not published the expected candle, the next tick retries it; older data is never silently substituted.

After downtime, only the latest eligible candle is analyzed. Skipped intervals are recorded in `recovery_gaps` and logged. Previously pending notifications are retained and sent with their original candle timestamps. Indicators and scoring are unchanged.

Delivery runs independently, wakes immediately after analysis, and checks retries every 30 seconds. Transient failures back off by 30, 60, 120, then 300 seconds, honoring longer Telegram `retry_after` limits across both timeframes. Permanent failures remain blocked and unhealthy until explicitly reset. Retries are durable across restarts. Telegram delivery is at-least-once: a lost acknowledgement can cause a duplicate.

## Production Docker deployment

Use one Linux VPS with Docker and the Compose plugin, accurate system time, and outbound connectivity to MEXC, Telegram, and your PostgreSQL/Supabase endpoint. No inbound application port is needed. Run exactly one service replica.

```bash
cp .env.example .env
chmod 600 .env
# Edit .env with DATABASE_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
docker compose build
```

Use your existing production database so candle IDs and notification state survive cutover. Compose requires a PostgreSQL `DATABASE_URL`; SQLite remains available for local runs outside production Compose. Runtime schema additions are applied automatically and preserve existing tables and rows. Back up the database before cutover. Keep credentials out of version control; `.dockerignore` excludes environment files, databases, and local data from the image.

Cutover sequence:

1. Validate the image against an isolated test database and test Telegram chat. Confirm public MEXC data is reachable from the VPS and that both timeframes complete correctly.
2. Disable the existing GitHub scheduled monitor and wait for any active run to finish. Deploy this revision, which retains only manual dispatch for monitoring.
3. Start the service with `docker compose up -d --build`.
4. Check `docker compose ps` and `docker compose logs --tail=100 mexc-ta-assistant`. Confirm both startup scans and the delivery job are running.
5. Run `docker compose exec mexc-ta-assistant python -m app.health` and investigate any unresolved candles or blocked delivery.

The container restarts after process failure or host reboot. Logs rotate at 10 MB with three files. Docker health checks detect missing scheduler ticks after 90 seconds, scan errors, permanently blocked delivery, and candles/notifications unresolved beyond five minutes. A Docker `unhealthy` status alone does **not** restart the container: connect your VPS monitoring to the container health status, and use the health command to investigate.

If Telegram credentials or chat permissions were wrong, correct `.env`, recreate the container, then explicitly re-enable the blocked notifications:

```bash
docker compose up -d --force-recreate
docker compose exec mexc-ta-assistant python -m app.health --reset-blocked
```

Healthy scans record `scan_start` and `scan_end`; notifications record candle close, acknowledgement time, attempt count, and end-to-end latency. `runtime_health` retains the last tick, last successful tick, and current error for each job. `candle_runs` retains retry deadlines, errors, and `notified_at`. A lack of trading signals is normal and does not make a completed scan unhealthy.

Observe at least seven days, including daily boundaries. Target: at least 95% of actionable candle notifications acknowledged within 180 seconds of close while external services are healthy. Use `notified_at - candle_close_time` (timestamps are stored as UTC ISO strings) to calculate latency; count unresolved actionable candles as misses and report outages separately. This is an operational target, not a guaranteed platform SLA.

## GitHub Actions and manual recovery

- `Tests` runs on push, pull request, and manual dispatch, including an isolated PostgreSQL service for integration tests.
- `MEXC TA Monitor` is manual-only. It requires `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` repository secrets.
- **Stop the VPS service before dispatching manual recovery:** `docker compose stop mexc-ta-assistant`. Wait for the workflow to finish, then restart with `docker compose up -d`. Do not run both schedulers against the production database.
- Manual scans keep `--once --all` and `--once --timeframe Hour4|Day1`. They return nonzero on scan failure or undelivered notifications. A manual pass delivers one due candle-close batch per timeframe; the continuous service drains additional pending batches.
- For rollback, stop the new service before starting an older monitor. The schema migration is additive; do not delete candle or notification state. Older versions do not honor the new delivery retry metadata, so use rollback only as a controlled recovery action.

## Scoring System

For every symbol and timeframe, both analytical directions are evaluated separately.

Criteria:

1. RSI state on the latest closed candle
2. Stoch RSI crossover within the configured recent-candle window
3. MACD histogram fading on the latest closed candles
4. Candlestick pattern within the configured recent-candle window
5. Supertrend direction on the latest closed candle

Signal validity windows are configured in `app/config.py` through
`SIGNAL_VALIDITY_WINDOWS`. The default for both `Hour4` and `Day1` allows
Stoch RSI crossover and candlestick pattern events from the latest closed candle
through two candles ago.

Labels:

- `0/5`, `1/5`, `2/5`, `3/5`: `Not suitable`
- `4/5`: `Criteria satisfied`
- `5/5`: `Strong technical alignment`

If both analytical directions score at least `4/5` for the same symbol and timeframe, the result is marked as `Conflicting signal`.

Telegram messages keep a compact summary table. Alert rows include event ages
for Stoch RSI and candlestick pattern criteria when those event-based criteria
are valid.

## Tests

```bash
cd mexc_ta_assistant
pytest
```

Tests cover indicators, formatting, UTC boundaries, candle availability, transactional completion, competing claims, lease recovery, durable retries, Telegram error handling, and scheduler health. SQLite tests run by default. To also run PostgreSQL tests against a disposable database:

```bash
TEST_DATABASE_URL=postgresql://test:test@localhost:5432/test pytest
```

Each PostgreSQL test creates and removes its own schema. The test user needs schema creation permission; use an isolated test database, never production. CI runs both backends.
