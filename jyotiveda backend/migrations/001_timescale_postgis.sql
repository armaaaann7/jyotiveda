-- Jyotiveda production schema extensions (PostgreSQL 16 + TimescaleDB 2.x + PostGIS 3.x)
-- Applied by the `migrate` Kubernetes Job after SQLAlchemy creates base tables.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------- telemetry hypertable ---
SELECT create_hypertable('telemetry', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE, migrate_data => TRUE);
ALTER TABLE telemetry SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'transformer_id, metric',
  timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('telemetry', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_retention_policy('telemetry', INTERVAL '3 years', if_not_exists => TRUE);

-- 15-minute and daily rollups for dashboards and model features
CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_15m
WITH (timescaledb.continuous) AS
SELECT time_bucket('15 minutes', ts) AS bucket, transformer_id, metric,
       avg(value) AS avg, max(value) AS max, min(value) AS min, count(*) AS n
FROM telemetry GROUP BY bucket, transformer_id, metric WITH NO DATA;
SELECT add_continuous_aggregate_policy('telemetry_15m', start_offset => INTERVAL '2 days',
       end_offset => INTERVAL '15 minutes', schedule_interval => INTERVAL '15 minutes', if_not_exists => TRUE);

-- ---------------------------------------------------------------- assets & geography -----
CREATE TABLE IF NOT EXISTS transformer_asset (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  feeder_id     TEXT NOT NULL,
  rating_kva    NUMERIC NOT NULL,
  discom        TEXT NOT NULL,
  geom          geometry(Point, 4326) NOT NULL,
  commissioned  DATE
);
CREATE INDEX IF NOT EXISTS ix_transformer_geom ON transformer_asset USING GIST (geom);

CREATE TABLE IF NOT EXISTS household_asset (
  id              TEXT PRIMARY KEY,
  transformer_id  TEXT NOT NULL REFERENCES transformer_asset(id),
  meter_serial    TEXT UNIQUE,
  connection_type TEXT NOT NULL,
  income_band     TEXT NOT NULL,
  lifeline_kw     NUMERIC NOT NULL DEFAULT 0.25,
  critical_kw     NUMERIC NOT NULL DEFAULT 0,
  solar_kwp       NUMERIC NOT NULL DEFAULT 0,
  consent_id      TEXT,              -- DEPA-style consent artefact (DPDP Act, 2023)
  geom            geometry(Point, 4326)
);
CREATE INDEX IF NOT EXISTS ix_household_geom ON household_asset USING GIST (geom);

CREATE TABLE IF NOT EXISTS feeder_line (
  id          TEXT PRIMARY KEY,
  from_node   TEXT NOT NULL,
  to_node     TEXT NOT NULL,
  r_ohm_km    NUMERIC NOT NULL,
  x_ohm_km    NUMERIC NOT NULL,
  ampacity_a  NUMERIC NOT NULL,
  geom        geometry(LineString, 4326)
);

-- ---------------------------------------------------------------- audit immutability ------
CREATE OR REPLACE FUNCTION forbid_audit_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_no_update ON audit_log;
CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

-- Row-level security: operators see only their circle's transformers
ALTER TABLE dispatch ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS dispatch_scope ON dispatch;
CREATE POLICY dispatch_scope ON dispatch
  USING (transformer_id = ANY (string_to_array(current_setting('jyotiveda.scope', true), ',')) OR
         current_setting('jyotiveda.scope', true) = '*');
