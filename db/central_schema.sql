CREATE TABLE IF NOT EXISTS transfers (
    transfer_id            VARCHAR(32)  PRIMARY KEY,
    sku                    VARCHAR(64)  NOT NULL,
    source_warehouse       VARCHAR(16)  NOT NULL,
    destination_warehouse  VARCHAR(16)  NOT NULL,
    quantity               INTEGER      NOT NULL CHECK (quantity > 0),
    status                 VARCHAR(32)  NOT NULL,
    reason                 TEXT,
    attempts               INTEGER      NOT NULL DEFAULT 0,
    correlation_id         VARCHAR(64),
    idempotency_key        VARCHAR(128) UNIQUE,
    fail_step              VARCHAR(32),
    history                JSONB        NOT NULL DEFAULT '[]',
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_transfers_sku ON transfers (sku);
CREATE INDEX IF NOT EXISTS ix_transfers_status ON transfers (status);

CREATE TABLE IF NOT EXISTS global_inventory (
    sku                VARCHAR(64) NOT NULL,
    warehouse_id       VARCHAR(16) NOT NULL,
    quantity           INTEGER     NOT NULL DEFAULT 0,
    reserved_quantity  INTEGER     NOT NULL DEFAULT 0,
    last_event_id      VARCHAR(64),
    last_event_at      TIMESTAMPTZ,
    PRIMARY KEY (sku, warehouse_id)
);

CREATE TABLE IF NOT EXISTS warehouse_status (
    warehouse_id   VARCHAR(16) PRIMARY KEY,
    last_event_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS processed_events (
    event_id      VARCHAR     NOT NULL,
    consumer      VARCHAR     NOT NULL,
    processed_at  TIMESTAMPTZ,
    PRIMARY KEY (event_id, consumer)
);

CREATE TABLE IF NOT EXISTS report_jobs (
    job_id       VARCHAR(32) PRIMARY KEY,
    report_type  VARCHAR(64) NOT NULL,
    status       VARCHAR(16) NOT NULL,
    s3_key       TEXT,
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    event_id        VARCHAR(64) PRIMARY KEY,
    event_type      VARCHAR(64) NOT NULL,
    source          VARCHAR(64),
    correlation_id  VARCHAR(64),
    payload         JSONB,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_log_event_type ON audit_log (event_type);
CREATE INDEX IF NOT EXISTS ix_audit_log_correlation_id ON audit_log (correlation_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_received_at ON audit_log (received_at);

CREATE TABLE IF NOT EXISTS batch_runs (
    run_id       TEXT        PRIMARY KEY,
    run_date     DATE        NOT NULL UNIQUE,
    status       TEXT        NOT NULL,
    s3_key       TEXT,
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS sensor_readings (
    event_id         VARCHAR(64) PRIMARY KEY,
    warehouse_id     VARCHAR(16) NOT NULL,
    sensor_id        VARCHAR(64) NOT NULL,
    sensor_type      VARCHAR(32),
    value            DOUBLE PRECISION NOT NULL,
    unit             VARCHAR(16),
    sequence_number  BIGINT      NOT NULL,
    measured_at      TIMESTAMPTZ,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sensor_readings_sensor_time ON sensor_readings (warehouse_id, sensor_id, measured_at);

CREATE TABLE IF NOT EXISTS sensor_state (
    warehouse_id          VARCHAR(16) NOT NULL,
    sensor_id             VARCHAR(64) NOT NULL,
    last_sequence_number  BIGINT      NOT NULL,
    last_measured_at      TIMESTAMPTZ,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (warehouse_id, sensor_id)
);

CREATE TABLE IF NOT EXISTS sensor_alerts (
    alert_id          VARCHAR(80) PRIMARY KEY,
    reading_event_id  VARCHAR(64) NOT NULL UNIQUE,
    warehouse_id      VARCHAR(16) NOT NULL,
    sensor_id         VARCHAR(64) NOT NULL,
    sensor_type       VARCHAR(32) NOT NULL,
    value             DOUBLE PRECISION NOT NULL,
    threshold         DOUBLE PRECISION NOT NULL,
    measured_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sensor_alerts_warehouse_id ON sensor_alerts (warehouse_id);
