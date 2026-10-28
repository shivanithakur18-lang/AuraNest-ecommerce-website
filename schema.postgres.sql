-- AuraNest — PostgreSQL schema
-- Used when DATABASE_URL points at Postgres. Mirrors schema.sql; the only
-- differences are identity columns and the absence of SQLite-only PRAGMAs.

CREATE TABLE IF NOT EXISTS categories (
    id          SERIAL PRIMARY KEY,
    slug        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    tagline     TEXT,
    image       TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS products (
    id           SERIAL PRIMARY KEY,
    slug         TEXT    NOT NULL UNIQUE,
    name         TEXT    NOT NULL,
    category_id  INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    -- Money stored as integer paise (1 rupee = 100 paise) to avoid float rounding.
    price_paise  INTEGER NOT NULL CHECK (price_paise >= 0),
    description  TEXT    NOT NULL DEFAULT '',
    image        TEXT,
    stock        INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    featured     INTEGER NOT NULL DEFAULT 0 CHECK (featured IN (0, 1)),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_products_category  ON products (category_id);
CREATE INDEX IF NOT EXISTS idx_products_featured  ON products (featured);
CREATE INDEX IF NOT EXISTS idx_products_price     ON products (price_paise);

CREATE TABLE IF NOT EXISTS cart_items (
    id          SERIAL PRIMARY KEY,
    cart_id     TEXT    NOT NULL,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (cart_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_cart_items_cart ON cart_items (cart_id);

CREATE TABLE IF NOT EXISTS subscribers (
    id          SERIAL PRIMARY KEY,
    email       TEXT    NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bookings (
    id              SERIAL PRIMARY KEY,
    name            TEXT    NOT NULL,
    email           TEXT    NOT NULL,
    phone           TEXT,
    service         TEXT    NOT NULL DEFAULT 'Full-home styling',
    preferred_date  TEXT,
    message         TEXT    NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings (preferred_date);