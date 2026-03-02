-- Runs once when the Postgres data directory is first created.
-- Safe to re-run (idempotent).

CREATE EXTENSION IF NOT EXISTS pgcrypto;
