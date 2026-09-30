-- =============================================================================
-- Supabase Cron Migration: MEXC TA Assistant – Database Cron Model
-- =============================================================================
--
-- PURPOSE
--   Replace delayed GitHub-Actions / external schedulers with precision
--   database-level cron jobs that fire HTTP webhooks at exact candle-close
--   times using Supabase's built-in pg_cron + pg_net extensions.
--
-- HOW TO APPLY
--   1. Open your Supabase project dashboard → SQL Editor.
--   2. Replace the two placeholder values below:
--        • <YOUR_API_HOST>   → your deployed API hostname
--                               e.g. mexc-ta.koyeb.app
--        • <YOUR_API_KEY>    → the value of WEBHOOK_API_KEY in your .env
--   3. Run the entire script.
--   4. Verify jobs are registered:
--        SELECT * FROM cron.job;
--
-- UNINSTALL
--   SELECT cron.unschedule('mexc-4h-analysis');
--   SELECT cron.unschedule('mexc-1d-analysis');
-- =============================================================================

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 1. Enable required extensions (idempotent)                              │
-- └──────────────────────────────────────────────────────────────────────────┘
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 2. Remove previous versions of these jobs (safe if they don't exist)    │
-- └──────────────────────────────────────────────────────────────────────────┘
SELECT cron.unschedule('mexc-4h-analysis');
SELECT cron.unschedule('mexc-1d-analysis');

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 3. Schedule 4-Hour analysis                                             │
-- │    Runs at **minute 2** past every 4th hour (UTC):                      │
-- │    00:02, 04:02, 08:02, 12:02, 16:02, 20:02                            │
-- │    The 2-minute offset allows the exchange to finalise the candle.      │
-- └──────────────────────────────────────────────────────────────────────────┘
SELECT cron.schedule(
    'mexc-4h-analysis',                     -- job name
    '2 0,4,8,12,16,20 * * *',              -- cron expression (UTC)
    $$
    SELECT net.http_post(
        url    := 'https://<YOUR_API_HOST>/api/v1/analyze?timeframe=Hour4',
        body   := '{}'::jsonb,
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'X-API-Key',    '<YOUR_API_KEY>'
        )
    );
    $$
);

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 4. Schedule Daily (1D) analysis                                         │
-- │    Runs at **00:02 UTC** every day.                                     │
-- └──────────────────────────────────────────────────────────────────────────┘
SELECT cron.schedule(
    'mexc-1d-analysis',                     -- job name
    '2 0 * * *',                            -- cron expression (UTC)
    $$
    SELECT net.http_post(
        url    := 'https://<YOUR_API_HOST>/api/v1/analyze?timeframe=Day1',
        body   := '{}'::jsonb,
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'X-API-Key',    '<YOUR_API_KEY>'
        )
    );
    $$
);

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 5. Verify                                                               │
-- └──────────────────────────────────────────────────────────────────────────┘
-- Run after applying:
--   SELECT jobid, jobname, schedule, command FROM cron.job;
--
-- Monitor execution history:
--   SELECT * FROM cron.job_run_details ORDER BY start_time DESC LIMIT 20;
