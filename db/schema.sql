CREATE TABLE IF NOT EXISTS products (
    id          SERIAL PRIMARY KEY,
    sku         VARCHAR NOT NULL UNIQUE,
    name        VARCHAR NOT NULL,
    description VARCHAR,
    category    VARCHAR,
    unit        VARCHAR,
    created_at  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_products_sku ON products (sku);

CREATE TABLE IF NOT EXISTS inventory (
    id                SERIAL PRIMARY KEY,
    product_id        INTEGER NOT NULL UNIQUE REFERENCES products (id),
    warehouse_id      VARCHAR NOT NULL,
    quantity          INTEGER NOT NULL DEFAULT 0,
    reserved_quantity INTEGER NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ,
    CONSTRAINT ck_inventory_quantity_nonneg CHECK (quantity >= 0),
    CONSTRAINT ck_inventory_reserved_nonneg CHECK (reserved_quantity >= 0)
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    id           SERIAL PRIMARY KEY,
    product_id   INTEGER NOT NULL,
    sku          VARCHAR NOT NULL,
    warehouse_id VARCHAR NOT NULL,
    type         VARCHAR NOT NULL,
    quantity     INTEGER NOT NULL,
    reference    VARCHAR,
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inventory_movements_product_id ON inventory_movements (product_id);
CREATE INDEX IF NOT EXISTS ix_inventory_movements_created_at ON inventory_movements (created_at);

-- eventos que faltan por publicar
CREATE TABLE IF NOT EXISTS outbox (
    id           BIGSERIAL PRIMARY KEY,
    event_id     VARCHAR NOT NULL UNIQUE,
    routing_key  VARCHAR NOT NULL,
    payload      JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    attempts     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_outbox_unpublished ON outbox (id) WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS processed_commands (
    command_id   VARCHAR PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL
);
