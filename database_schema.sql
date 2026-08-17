-- ============================================================
-- StockPilot BD AI — Market Module Database Schema (PostgreSQL)
-- ============================================================

-- ---------- Sectors ----------
CREATE TABLE sectors (
    sector_id       SERIAL PRIMARY KEY,
    sector_name     VARCHAR(100) NOT NULL UNIQUE,   -- e.g. Bank, Pharmaceuticals, Textile
    sector_code     VARCHAR(20) UNIQUE,
    description     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ---------- Companies (listed on DSE) ----------
CREATE TABLE companies (
    company_id      SERIAL PRIMARY KEY,
    trading_code    VARCHAR(20) NOT NULL UNIQUE,     -- e.g. BEXIMCO, GP
    company_name    VARCHAR(255) NOT NULL,
    sector_id       INTEGER REFERENCES sectors(sector_id),
    market_type     VARCHAR(10) CHECK (market_type IN ('A','B','N','Z','G')),
    listing_date    DATE,
    paid_up_capital NUMERIC(18,2),
    total_shares    BIGINT,
    face_value      NUMERIC(10,2) DEFAULT 10,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_companies_sector ON companies(sector_id);
CREATE INDEX idx_companies_trading_code ON companies(trading_code);

-- ---------- Daily Market Index (DSEX, DS30, DSES) ----------
CREATE TABLE market_index (
    index_id        BIGSERIAL PRIMARY KEY,
    index_name      VARCHAR(10) NOT NULL CHECK (index_name IN ('DSEX','DS30','DSES')),
    trade_date      DATE NOT NULL,
    open_value      NUMERIC(12,2),
    high_value      NUMERIC(12,2),
    low_value       NUMERIC(12,2),
    close_value     NUMERIC(12,2) NOT NULL,
    change_value    NUMERIC(12,2),
    change_percent  NUMERIC(6,3),
    total_volume    BIGINT,
    total_turnover  NUMERIC(20,2),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(index_name, trade_date)
);
CREATE INDEX idx_market_index_date ON market_index(trade_date DESC);

-- ---------- Daily Stock Price / OHLCV ----------
CREATE TABLE stock_prices (
    price_id        BIGSERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(company_id),
    trade_date      DATE NOT NULL,
    open_price      NUMERIC(12,2),
    high_price      NUMERIC(12,2),
    low_price       NUMERIC(12,2),
    close_price     NUMERIC(12,2) NOT NULL,
    ltp             NUMERIC(12,2),                  -- Last Traded Price
    ycp             NUMERIC(12,2),                  -- Yesterday's Closing Price
    change_value    NUMERIC(12,2),
    change_percent  NUMERIC(6,3),
    volume          BIGINT DEFAULT 0,
    trades_count    INTEGER DEFAULT 0,
    turnover        NUMERIC(20,2),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(company_id, trade_date)
);
CREATE INDEX idx_stock_prices_date ON stock_prices(trade_date DESC);
CREATE INDEX idx_stock_prices_company_date ON stock_prices(company_id, trade_date DESC);

-- ---------- Block Trades ----------
CREATE TABLE block_trades (
    block_trade_id  BIGSERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(company_id),
    trade_date      DATE NOT NULL,
    trade_time      TIME,
    price            NUMERIC(12,2) NOT NULL,
    quantity        BIGINT NOT NULL,
    value           NUMERIC(20,2) GENERATED ALWAYS AS (price * quantity) STORED,
    seller_broker   VARCHAR(50),
    buyer_broker    VARCHAR(50),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_block_trades_date ON block_trades(trade_date DESC);

-- ---------- Sector Heatmap Snapshot (derived / cached daily) ----------
CREATE TABLE sector_performance (
    sector_perf_id  BIGSERIAL PRIMARY KEY,
    sector_id       INTEGER NOT NULL REFERENCES sectors(sector_id),
    trade_date      DATE NOT NULL,
    avg_change_pct  NUMERIC(6,3),
    total_turnover  NUMERIC(20,2),
    total_volume    BIGINT,
    advancers       INTEGER DEFAULT 0,
    decliners       INTEGER DEFAULT 0,
    unchanged       INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(sector_id, trade_date)
);

-- ---------- Market Movers (Top Gainers / Losers / Volume Leaders) ----------
-- Materialized/cached table refreshed after each trading session (see scheduler)
CREATE TABLE market_movers (
    mover_id        BIGSERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(company_id),
    trade_date      DATE NOT NULL,
    category        VARCHAR(20) NOT NULL CHECK (category IN ('GAINER','LOSER','VOLUME','TURNOVER')),
    rank            INTEGER NOT NULL,
    value           NUMERIC(20,2),         -- % change or volume/turnover depending on category
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(trade_date, category, rank)
);
CREATE INDEX idx_market_movers_lookup ON market_movers(trade_date, category);

-- ---------- Watchlist (per user) ----------
CREATE TABLE watchlist (
    watchlist_id    BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL,          -- references users(user_id) in auth module
    company_id      INTEGER NOT NULL REFERENCES companies(company_id),
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, company_id)
);

-- ---------- AI Market Commentary (Agent 1: Market Analyst output, cached) ----------
CREATE TABLE ai_market_commentary (
    commentary_id   BIGSERIAL PRIMARY KEY,
    trade_date      DATE NOT NULL UNIQUE,
    summary_bn      TEXT NOT NULL,          -- Bengali summary
    sentiment       VARCHAR(20) CHECK (sentiment IN ('BULLISH','BEARISH','NEUTRAL','MIXED')),
    key_drivers     JSONB,                   -- e.g. ["Bank sector rally", "PSI on X company"]
    ai_model_used   VARCHAR(50),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ---------- Views for quick consumption ----------
CREATE VIEW v_latest_index AS
SELECT DISTINCT ON (index_name) *
FROM market_index
ORDER BY index_name, trade_date DESC;

CREATE VIEW v_latest_prices AS
SELECT DISTINCT ON (company_id) sp.*, c.trading_code, c.company_name, c.sector_id
FROM stock_prices sp
JOIN companies c ON c.company_id = sp.company_id
ORDER BY company_id, trade_date DESC;
