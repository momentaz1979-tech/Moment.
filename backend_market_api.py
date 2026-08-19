"""
StockPilot BD AI — Market Module Backend (FastAPI)
চালানোর নিয়ম:
    pip install fastapi uvicorn --break-system-packages
    uvicorn backend_market_api:app --reload --port 8000
    ব্রাউজারে দেখুন: http://127.0.0.1:8000/docs (Swagger UI)

এই ফাইলে DSE-এর মক (নমুনা) ডেটা ব্যবহার করা হয়েছে যেহেতু DSE-এর
পাবলিক লাইভ API নেই। প্রোডাকশনে database_schema.sql-এর টেবিলগুলো থেকে
ডেটা আসবে (SQLAlchemy/asyncpg দিয়ে PostgreSQL-এর সাথে সংযোগ করে)।
"""

from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import date, timedelta
import random
import os
import json

app = FastAPI(title="StockPilot BD AI — Market API", version="1.0.0")

# Admin ডেটা এন্ট্রি সুরক্ষার জন্য কী (Railway Variables-এ ADMIN_KEY সেট করুন;
# না করলে ডিফল্ট মান ব্যবহার হবে, যা প্রোডাকশনে নিরাপদ নয়)
ADMIN_KEY = os.environ.get("ADMIN_KEY", "changeme123")

# UI (Claude আর্টিফ্যাক্ট / ব্রাউজার) থেকে ভিন্ন origin থেকে API কল করার অনুমতি
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------
# ডাটাবেস স্তর (SQLAlchemy + PostgreSQL)
# DATABASE_URL এনভায়রনমেন্ট ভ্যারিয়েবল না থাকলে বা সংযোগ ব্যর্থ হলে
# স্বয়ংক্রিয়ভাবে in-memory স্টোরেজে ফিরে যায় (অ্যাপ কখনো ক্র্যাশ করবে না)
# ---------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
DB_AVAILABLE = False
_engine = None

if DATABASE_URL:
    try:
        from sqlalchemy import create_engine, text
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=3, max_overflow=2)
        with _engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS manual_index (
                    trade_date DATE PRIMARY KEY,
                    close_value NUMERIC(12,2) NOT NULL,
                    change_value NUMERIC(12,2) NOT NULL,
                    change_percent NUMERIC(6,3) NOT NULL,
                    total_volume BIGINT DEFAULT 0,
                    total_turnover NUMERIC(20,2) DEFAULT 0,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS manual_movers (
                    id SERIAL PRIMARY KEY,
                    trade_date DATE NOT NULL,
                    category VARCHAR(10) NOT NULL CHECK (category IN ('gainer','loser')),
                    trading_code VARCHAR(20) NOT NULL,
                    company_name VARCHAR(255),
                    ltp NUMERIC(12,2),
                    change_percent NUMERIC(6,3),
                    volume BIGINT DEFAULT 0,
                    UNIQUE(trade_date, category, trading_code)
                );
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS portfolio_holdings (
                    trading_code VARCHAR(20) PRIMARY KEY,
                    quantity INTEGER NOT NULL,
                    avg_buy_price NUMERIC(12,2) NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """))
            conn.commit()
        DB_AVAILABLE = True
    except Exception as _db_err:  # noqa: BLE001 — ইচ্ছাকৃতভাবে ব্রড, যাতে DB সমস্যায় অ্যাপ ক্র্যাশ না করে
        DB_AVAILABLE = False
        print(f"[DB] সংযোগ ব্যর্থ, in-memory স্টোরেজে ফিরে যাওয়া হচ্ছে: {_db_err}")

# ---------------------------------------------------------------
# মক ডেটা (ডেমো/টেস্টিং-এর জন্য; বাস্তব ডেটাবেস দিয়ে প্রতিস্থাপন করুন)
# ---------------------------------------------------------------
COMPANIES = [
    {"trading_code": "SQUARPHARMA", "company_name": "Square Pharmaceuticals", "sector": "Pharmaceuticals"},
    {"trading_code": "GP", "company_name": "Grameenphone", "sector": "Telecommunication"},
    {"trading_code": "BEXIMCO", "company_name": "Beximco Limited", "sector": "Miscellaneous"},
    {"trading_code": "BATBC", "company_name": "British American Tobacco Bangladesh", "sector": "Food & Allied"},
    {"trading_code": "ISLAMIBANK", "company_name": "Islami Bank Bangladesh", "sector": "Bank"},
    {"trading_code": "BRACBANK", "company_name": "BRAC Bank", "sector": "Bank"},
    {"trading_code": "RENATA", "company_name": "Renata Limited", "sector": "Pharmaceuticals"},
    {"trading_code": "TITASGAS", "company_name": "Titas Gas", "sector": "Fuel & Power"},
    {"trading_code": "OLYMPIC", "company_name": "Olympic Industries", "sector": "Food & Allied"},
    {"trading_code": "SUMITPOWER", "company_name": "Summit Power", "sector": "Fuel & Power"},
]

SECTORS = sorted(set(c["sector"] for c in COMPANIES))


def _seeded_price(code: str, d: date) -> dict:
    """একই code+date-এ সবসময় একই সংখ্যা দেবে (deterministic mock)।"""
    rnd = random.Random(f"{code}-{d.isoformat()}")
    base = rnd.uniform(20, 400)
    change_pct = rnd.uniform(-9.9, 9.9)
    ltp = round(base, 2)
    change_value = round(base * change_pct / 100, 2)
    volume = rnd.randint(5_000, 3_000_000)
    return {
        "trading_code": code,
        "ltp": ltp,
        "change_value": change_value,
        "change_percent": round(change_pct, 2),
        "volume": volume,
        "turnover": round(ltp * volume, 2),
    }


def _company_by_code(code: str):
    for c in COMPANIES:
        if c["trading_code"] == code.upper():
            return c
    return None


# ---------------------------------------------------------------
# ⚠️ DEMO DATA DISCLAIMER — প্রতিটা রেসপন্সে যুক্ত করা হয়
# বাস্তব বিনিয়োগ সিদ্ধান্তে এই ডেটা ব্যবহার করা যাবে না।
# ---------------------------------------------------------------
DEMO_DISCLAIMER = {
    "is_demo_data": True,
    "disclaimer_bn": "⚠️ এটি ডেমো/মক ডেটা, প্রকৃত DSE ডেটা নয়। বিনিয়োগ সিদ্ধান্তের জন্য ব্যবহার করবেন না — dsebd.org বা আপনার ব্রোকারের অফিসিয়াল তথ্য দেখুন।",
}

VERIFIED_DISCLAIMER = {
    "is_demo_data": False,
    "disclaimer_bn": "✅ dsebd.org থেকে ম্যানুয়ালি যাচাই করে দেওয়া বাস্তব ডেটা। তবুও বড় সিদ্ধান্তের আগে সরাসরি dsebd.org-এ ক্রস-চেক করার পরামর্শ দেওয়া হচ্ছে।",
}

# ---------------------------------------------------------------
# Manual Data Entry স্টোর — ডাটাবেস থাকলে PostgreSQL-এ স্থায়ীভাবে সেভ হয়,
# না থাকলে in-memory ফলব্যাক (সার্ভার রিস্টার্ট হলে হারিয়ে যাবে)
# ---------------------------------------------------------------
_MANUAL_DATA_FALLBACK: dict[str, dict] = {}  # শুধু DB না থাকলে ব্যবহৃত হয়


def _get_manual_index(d: date) -> dict | None:
    if DB_AVAILABLE:
        from sqlalchemy import text
        with _engine.connect() as conn:
            row = conn.execute(
                text("SELECT close_value, change_value, change_percent, total_volume, total_turnover "
                     "FROM manual_index WHERE trade_date = :d"),
                {"d": d},
            ).mappings().first()
            if not row:
                return None
            return {
                "close_value": float(row["close_value"]),
                "change_value": float(row["change_value"]),
                "change_percent": float(row["change_percent"]),
                "total_volume": int(row["total_volume"] or 0),
                "total_turnover": float(row["total_turnover"] or 0),
            }
    return _MANUAL_DATA_FALLBACK.get(d.isoformat(), {}).get("index")


def _get_manual_movers(d: date, category: str) -> list[dict]:
    if DB_AVAILABLE:
        from sqlalchemy import text
        with _engine.connect() as conn:
            rows = conn.execute(
                text("SELECT trading_code, company_name, ltp, change_percent, volume "
                     "FROM manual_movers WHERE trade_date = :d AND category = :c ORDER BY id"),
                {"d": d, "c": category},
            ).mappings().all()
            return [
                {
                    "trading_code": r["trading_code"],
                    "company_name": r["company_name"] or r["trading_code"],
                    "ltp": float(r["ltp"] or 0),
                    "change_percent": float(r["change_percent"] or 0),
                    "volume": int(r["volume"] or 0),
                }
                for r in rows
            ]
    key = "gainers" if category == "gainer" else "losers"
    return _MANUAL_DATA_FALLBACK.get(d.isoformat(), {}).get(key, [])


def _save_manual_entry(d: date, index_data: dict | None, gainers: list[dict], losers: list[dict]):
    if DB_AVAILABLE:
        from sqlalchemy import text
        with _engine.connect() as conn:
            if index_data:
                conn.execute(text("""
                    INSERT INTO manual_index (trade_date, close_value, change_value, change_percent, total_volume, total_turnover, updated_at)
                    VALUES (:d, :close_value, :change_value, :change_percent, :total_volume, :total_turnover, NOW())
                    ON CONFLICT (trade_date) DO UPDATE SET
                        close_value = EXCLUDED.close_value,
                        change_value = EXCLUDED.change_value,
                        change_percent = EXCLUDED.change_percent,
                        total_volume = EXCLUDED.total_volume,
                        total_turnover = EXCLUDED.total_turnover,
                        updated_at = NOW()
                """), {"d": d, **index_data})
            for category, movers in (("gainer", gainers), ("loser", losers)):
                if not movers:
                    continue
                conn.execute(text("DELETE FROM manual_movers WHERE trade_date = :d AND category = :c"), {"d": d, "c": category})
                for m in movers:
                    conn.execute(text("""
                        INSERT INTO manual_movers (trade_date, category, trading_code, company_name, ltp, change_percent, volume)
                        VALUES (:d, :c, :trading_code, :company_name, :ltp, :change_percent, :volume)
                    """), {"d": d, "c": category, **m})
            conn.commit()
    else:
        key = d.isoformat()
        existing = _MANUAL_DATA_FALLBACK.get(key, {})
        if index_data:
            existing["index"] = index_data
        if gainers:
            existing["gainers"] = gainers
        if losers:
            existing["losers"] = losers
        _MANUAL_DATA_FALLBACK[key] = existing


def _clear_manual_entry(d: date):
    if DB_AVAILABLE:
        from sqlalchemy import text
        with _engine.connect() as conn:
            conn.execute(text("DELETE FROM manual_index WHERE trade_date = :d"), {"d": d})
            conn.execute(text("DELETE FROM manual_movers WHERE trade_date = :d"), {"d": d})
            conn.commit()
    else:
        _MANUAL_DATA_FALLBACK.pop(d.isoformat(), None)


# ---------------------------------------------------------------
# ১. Market Index
# ---------------------------------------------------------------
@app.get("/v1/market/index/{index_name}")
def get_index(index_name: str, from_date: date | None = None, to_date: date | None = None):
    index_name = index_name.upper()
    if index_name not in ("DSEX", "DS30", "DSES"):
        raise HTTPException(400, "অবৈধ ইনডেক্স নাম। DSEX, DS30, অথবা DSES ব্যবহার করুন।")
    today = to_date or date.today()

    if index_name == "DSEX":
        manual = _get_manual_index(today)
        if manual:
            return {**manual, "index_name": "DSEX", "trade_date": today.isoformat(), **VERIFIED_DISCLAIMER}

    rnd = random.Random(f"{index_name}-{today.isoformat()}")
    close = round(rnd.uniform(4500, 6800), 2)
    change = round(rnd.uniform(-80, 80), 2)
    return {
        "index_name": index_name,
        "trade_date": today.isoformat(),
        "close_value": close,
        "change_value": change,
        "change_percent": round(change / close * 100, 3),
        "total_volume": rnd.randint(200_000_000, 600_000_000),
        "total_turnover": round(rnd.uniform(8_000_000_000, 18_000_000_000), 2),
        **DEMO_DISCLAIMER,
    }


# ---------------------------------------------------------------
# ২-৪. Gainers / Losers / Volume Leaders
# ---------------------------------------------------------------
def _all_prices(d: date):
    return [_seeded_price(c["trading_code"], d) | {"company_name": _company_by_code(c["trading_code"])["company_name"]}
            for c in COMPANIES]


@app.get("/v1/market/gainers")
def get_gainers(target_date: date = Query(default_factory=date.today), limit: int = 20):
    manual = _get_manual_movers(target_date, "gainer")
    if manual:
        return {"trade_date": target_date.isoformat(), "data": manual[:limit], **VERIFIED_DISCLAIMER}
    rows = sorted(_all_prices(target_date), key=lambda r: r["change_percent"], reverse=True)
    return {"trade_date": target_date.isoformat(), "data": rows[:limit], **DEMO_DISCLAIMER}


@app.get("/v1/market/losers")
def get_losers(target_date: date = Query(default_factory=date.today), limit: int = 20):
    manual = _get_manual_movers(target_date, "loser")
    if manual:
        return {"trade_date": target_date.isoformat(), "data": manual[:limit], **VERIFIED_DISCLAIMER}
    rows = sorted(_all_prices(target_date), key=lambda r: r["change_percent"])
    return {"trade_date": target_date.isoformat(), "data": rows[:limit], **DEMO_DISCLAIMER}


@app.get("/v1/market/volume-leaders")
def get_volume_leaders(target_date: date = Query(default_factory=date.today), limit: int = 20):
    rows = sorted(_all_prices(target_date), key=lambda r: r["volume"], reverse=True)
    return {"trade_date": target_date.isoformat(), "data": rows[:limit], **DEMO_DISCLAIMER}


# ---------------------------------------------------------------
# ৫. Block Trades
# ---------------------------------------------------------------
@app.get("/v1/market/block-trades")
def get_block_trades(target_date: date = Query(default_factory=date.today), company: str | None = None):
    rnd = random.Random(f"blocks-{target_date.isoformat()}")
    pool = [c for c in COMPANIES if not company or c["trading_code"] == company.upper()]
    data = []
    for c in rnd.sample(pool, k=min(3, len(pool))):
        qty = rnd.randint(50_000, 800_000)
        price = round(rnd.uniform(20, 400), 2)
        data.append({
            "trading_code": c["trading_code"],
            "price": price,
            "quantity": qty,
            "value": round(price * qty, 2),
            "trade_time": f"{rnd.randint(10,14)}:{rnd.randint(0,59):02d}:00",
        })
    return {"trade_date": target_date.isoformat(), "data": data, **DEMO_DISCLAIMER}


# ---------------------------------------------------------------
# ৬. Sector Heatmap
# ---------------------------------------------------------------
@app.get("/v1/market/sector-heatmap")
def get_sector_heatmap(target_date: date = Query(default_factory=date.today)):
    prices = _all_prices(target_date)
    result = []
    for sec in SECTORS:
        codes = [c["trading_code"] for c in COMPANIES if c["sector"] == sec]
        rows = [p for p in prices if p["trading_code"] in codes]
        avg_change = round(sum(r["change_percent"] for r in rows) / len(rows), 2) if rows else 0
        advancers = sum(1 for r in rows if r["change_percent"] > 0)
        decliners = sum(1 for r in rows if r["change_percent"] < 0)
        unchanged = len(rows) - advancers - decliners
        result.append({
            "sector_name": sec,
            "avg_change_pct": avg_change,
            "advancers": advancers,
            "decliners": decliners,
            "unchanged": unchanged,
        })
    return {"trade_date": target_date.isoformat(), "sectors": result, **DEMO_DISCLAIMER}


# ---------------------------------------------------------------
# ৭-৮. Company detail & history
# ---------------------------------------------------------------
@app.get("/v1/market/company/{trading_code}")
def get_company(trading_code: str):
    c = _company_by_code(trading_code)
    if not c:
        raise HTTPException(404, "কোম্পানির তথ্য পাওয়া যায়নি")
    price = _seeded_price(c["trading_code"], date.today())
    return c | price


@app.get("/v1/market/company/{trading_code}/history")
def get_company_history(trading_code: str, days: int = 30):
    c = _company_by_code(trading_code)
    if not c:
        raise HTTPException(404, "কোম্পানির তথ্য পাওয়া যায়নি")
    today = date.today()
    series = []
    for i in range(days, 0, -1):
        d = today - timedelta(days=i)
        p = _seeded_price(trading_code, d)
        series.append({"date": d.isoformat(), "close": p["ltp"], "volume": p["volume"]})
    return {"trading_code": trading_code, "series": series, **DEMO_DISCLAIMER}


# ---------------------------------------------------------------
# Company Detail মডিউল (Overview, Financials, Technical, Shareholders)
# সবটাই ডেমো ডেটা — এখনও বাস্তব DSE/BSEC ফাইলিং-এর সাথে সংযুক্ত নয়
# ---------------------------------------------------------------
def _seeded_financials(code: str) -> dict:
    rnd = random.Random(f"fin-{code}")
    eps = round(rnd.uniform(1.5, 25), 2)
    pe = round(rnd.uniform(6, 35), 2)
    nav = round(rnd.uniform(15, 150), 2)
    return {
        "eps_ttm": eps,
        "pe_ratio": pe,
        "nav_per_share": nav,
        "dividend_yield_pct": round(rnd.uniform(0, 8), 2),
        "market_cap": rnd.randint(500_000_000, 200_000_000_000),
        "year_high": round(rnd.uniform(150, 500), 2),
        "year_low": round(rnd.uniform(20, 150), 2),
    }


def _seeded_shareholding(code: str) -> dict:
    rnd = random.Random(f"share-{code}")
    sponsor = rnd.randint(30, 55)
    institute = rnd.randint(10, 30)
    foreign = rnd.randint(0, 15)
    govt = rnd.randint(0, 5)
    public = max(0, 100 - sponsor - institute - foreign - govt)
    return {
        "sponsor_director_pct": sponsor,
        "institute_pct": institute,
        "foreign_pct": foreign,
        "govt_pct": govt,
        "public_pct": public,
    }


@app.get("/v1/company/{trading_code}/overview")
def company_overview(trading_code: str):
    c = _company_by_code(trading_code)
    if not c:
        raise HTTPException(404, "কোম্পানির তথ্য পাওয়া যায়নি")
    price = _get_current_price(trading_code, date.today())
    fin = _seeded_financials(trading_code)
    price_disclaimer = VERIFIED_DISCLAIMER if price.get("is_verified") else DEMO_DISCLAIMER
    return {
        **c,
        **price,
        "financials": fin,
        "shareholding": _seeded_shareholding(trading_code),
        "price_is_verified": price.get("is_verified", False),
        "fundamentals_disclaimer_bn": "⚠️ EPS/P-E/NAV/শেয়ারহোল্ডিং এখনো সবসময় ডেমো ডেটা — শুধুমাত্র দাম (LTP) আজকের Admin এন্ট্রি থাকলে যাচাইকৃত হতে পারে।",
        **price_disclaimer,
    }


@app.get("/v1/company/{trading_code}/financials")
def company_financials(trading_code: str):
    c = _company_by_code(trading_code)
    if not c:
        raise HTTPException(404, "কোম্পানির তথ্য পাওয়া যায়নি")
    rnd = random.Random(f"quarterly-{trading_code}")
    quarters = []
    for i in range(4, 0, -1):
        q_year = 2026 if i <= 2 else 2025
        q_num = ((4 - i) % 4) + 1
        quarters.append({
            "period": f"Q{q_num} {q_year}",
            "revenue": rnd.randint(50_000_000, 5_000_000_000),
            "net_profit": rnd.randint(-50_000_000, 800_000_000),
            "eps": round(rnd.uniform(-2, 8), 2),
        })
    return {"trading_code": trading_code, "quarterly": quarters, **DEMO_DISCLAIMER}


@app.get("/v1/company/{trading_code}/technical")
def company_technical(trading_code: str):
    c = _company_by_code(trading_code)
    if not c:
        raise HTTPException(404, "কোম্পানির তথ্য পাওয়া যায়নি")
    rnd = random.Random(f"tech-{trading_code}")
    price = _seeded_price(trading_code, date.today())
    ltp = price["ltp"]
    return {
        "trading_code": trading_code,
        "ltp": ltp,
        "sma_20": round(ltp * rnd.uniform(0.92, 1.08), 2),
        "sma_50": round(ltp * rnd.uniform(0.85, 1.15), 2),
        "rsi_14": round(rnd.uniform(20, 80), 1),
        "support": round(ltp * rnd.uniform(0.85, 0.95), 2),
        "resistance": round(ltp * rnd.uniform(1.05, 1.15), 2),
        **DEMO_DISCLAIMER,
    }


# ---------------------------------------------------------------
# ৯. AI Market Summary (Agent 1 placeholder — real version calls Claude/Gemini API)
# ---------------------------------------------------------------
@app.get("/v1/market/ai-summary")
def get_ai_summary(target_date: date = Query(default_factory=date.today)):
    idx = get_index("DSEX", to_date=target_date)
    sentiment = "BULLISH" if idx["change_value"] >= 0 else "BEARISH"
    direction = "বৃদ্ধি পেয়ে" if idx["change_value"] >= 0 else "কমে"
    summary = (
        f"আজ DSEX {abs(idx['change_value'])} পয়েন্ট {direction} "
        f"{idx['close_value']}-এ অবস্থান করছে। মোট লেনদেনের পরিমাণ প্রায় "
        f"{idx['total_turnover']:,.0f} টাকা। [এটি একটি ডেমো সারাংশ — বাস্তব সংস্করণে "
        f"এই টেক্সট Claude/Gemini API দিয়ে দৈনিক ডেটা বিশ্লেষণ করে তৈরি হবে।]"
    )
    return {
        "trade_date": target_date.isoformat(),
        "summary_bn": summary,
        "sentiment": sentiment,
        "key_drivers": ["ব্যাংক খাতে ক্রয়চাপ (ডেমো)", "বৈদেশিক বিনিয়োগ প্রবাহ (ডেমো)"],
    }


# ---------------------------------------------------------------
# ১০-১১. Watchlist (in-memory demo store — প্রোডাকশনে DB টেবিল ব্যবহার করুন)
# ---------------------------------------------------------------
_WATCHLIST: set[str] = set()


@app.post("/v1/market/watchlist")
def add_watchlist(trading_code: str):
    c = _company_by_code(trading_code)
    if not c:
        raise HTTPException(404, "কোম্পানির তথ্য পাওয়া যায়নি")
    _WATCHLIST.add(trading_code.upper())
    return {"message_bn": "ওয়াচলিস্টে যোগ করা হয়েছে", "watchlist": sorted(_WATCHLIST)}


@app.delete("/v1/market/watchlist/{trading_code}")
def remove_watchlist(trading_code: str):
    _WATCHLIST.discard(trading_code.upper())
    return {"message_bn": "ওয়াচলিস্ট থেকে বাদ দেওয়া হয়েছে", "watchlist": sorted(_WATCHLIST)}


# ---------------------------------------------------------------
# বর্তমান দাম বের করার সাধারণ ফাংশন — আগে আজকের ম্যানুয়াল
# (যাচাইকৃত) গেইনার্স/লুজার্স তালিকায় খোঁজে, না পেলে মক ডেটা দেয়
# ---------------------------------------------------------------
def _get_current_price(code: str, d: date) -> dict:
    code = code.upper()
    for category in ("gainer", "loser"):
        for m in _get_manual_movers(d, category):
            if m["trading_code"].upper() == code:
                return {
                    "trading_code": code,
                    "ltp": m["ltp"],
                    "change_percent": m["change_percent"],
                    "volume": m["volume"],
                    "change_value": round(m["ltp"] * m["change_percent"] / 100, 2),
                    "turnover": round(m["ltp"] * m["volume"], 2),
                    "is_verified": True,
                }
    price = _seeded_price(code, d)
    price["is_verified"] = False
    return price


# ---------------------------------------------------------------
# Portfolio মডিউল — ডাটাবেস থাকলে PostgreSQL-এ স্থায়ীভাবে সেভ হয়
# ⚠️ এখানে যোগ করা হোল্ডিং সম্পূর্ণ কাল্পনিক অনুশীলনের জন্য।
# এটি আপনার প্রকৃত ব্রোকারেজ অ্যাকাউন্টের সাথে সংযুক্ত নয়,
# এবং যতক্ষণ না দাম "is_verified: true" দেখাচ্ছে, ততক্ষণ P&L ডেমো।
# ---------------------------------------------------------------
class HoldingIn(BaseModel):
    trading_code: str
    quantity: int
    avg_buy_price: float


_PORTFOLIO_FALLBACK: dict[str, dict] = {}  # শুধু DB না থাকলে ব্যবহৃত হয়


def _portfolio_get_all() -> dict[str, dict]:
    if DB_AVAILABLE:
        from sqlalchemy import text
        with _engine.connect() as conn:
            rows = conn.execute(text("SELECT trading_code, quantity, avg_buy_price FROM portfolio_holdings")).mappings().all()
            return {r["trading_code"]: {"quantity": r["quantity"], "avg_buy_price": float(r["avg_buy_price"])} for r in rows}
    return dict(_PORTFOLIO_FALLBACK)


def _portfolio_upsert(code: str, quantity: int, avg_buy_price: float):
    if DB_AVAILABLE:
        from sqlalchemy import text
        with _engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO portfolio_holdings (trading_code, quantity, avg_buy_price, updated_at)
                VALUES (:code, :quantity, :price, NOW())
                ON CONFLICT (trading_code) DO UPDATE SET
                    quantity = EXCLUDED.quantity, avg_buy_price = EXCLUDED.avg_buy_price, updated_at = NOW()
            """), {"code": code, "quantity": quantity, "price": avg_buy_price})
            conn.commit()
    else:
        _PORTFOLIO_FALLBACK[code] = {"quantity": quantity, "avg_buy_price": avg_buy_price}


def _portfolio_delete(code: str):
    if DB_AVAILABLE:
        from sqlalchemy import text
        with _engine.connect() as conn:
            conn.execute(text("DELETE FROM portfolio_holdings WHERE trading_code = :code"), {"code": code})
            conn.commit()
    else:
        _PORTFOLIO_FALLBACK.pop(code, None)


@app.post("/v1/portfolio/holdings")
def add_holding(holding: HoldingIn):
    c = _company_by_code(holding.trading_code)
    if not c:
        raise HTTPException(404, "কোম্পানির তথ্য পাওয়া যায়নি")
    if holding.quantity <= 0 or holding.avg_buy_price <= 0:
        raise HTTPException(400, "পরিমাণ ও ক্রয়মূল্য অবশ্যই ধনাত্মক হতে হবে")
    _portfolio_upsert(holding.trading_code.upper(), holding.quantity, holding.avg_buy_price)
    return {
        "message_bn": "হোল্ডিং যোগ করা হয়েছে" + (" (স্থায়ীভাবে সেভ হলো)" if DB_AVAILABLE else " (ডেমো — শুধু অনুশীলনের জন্য)"),
        **DEMO_DISCLAIMER,
    }


@app.delete("/v1/portfolio/holdings/{trading_code}")
def remove_holding(trading_code: str):
    _portfolio_delete(trading_code.upper())
    return {"message_bn": "হোল্ডিং মুছে ফেলা হয়েছে", **DEMO_DISCLAIMER}


@app.get("/v1/portfolio/summary")
def portfolio_summary():
    rows = []
    total_invested = 0.0
    total_current = 0.0
    any_verified = False
    for code, h in _portfolio_get_all().items():
        price = _get_current_price(code, date.today())
        company = _company_by_code(code)
        invested = h["quantity"] * h["avg_buy_price"]
        current = h["quantity"] * price["ltp"]
        pnl = current - invested
        pnl_pct = (pnl / invested * 100) if invested else 0
        total_invested += invested
        total_current += current
        any_verified = any_verified or price.get("is_verified", False)
        rows.append({
            "trading_code": code,
            "company_name": company["company_name"] if company else code,
            "quantity": h["quantity"],
            "avg_buy_price": h["avg_buy_price"],
            "ltp": price["ltp"],
            "is_verified": price.get("is_verified", False),
            "invested_value": round(invested, 2),
            "current_value": round(current, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })
    total_pnl = total_current - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0
    disclaimer = VERIFIED_DISCLAIMER if (any_verified and rows and all(r["is_verified"] for r in rows)) else DEMO_DISCLAIMER
    return {
        "holdings": rows,
        "total_invested": round(total_invested, 2),
        "total_current_value": round(total_current, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "storage": "database (স্থায়ী)" if DB_AVAILABLE else "in-memory (সার্ভার রিস্টার্ট হলে হারিয়ে যাবে)",
        **disclaimer,
    }


# ---------------------------------------------------------------
# Admin Manual Data Entry মডিউল
# dsebd.org ব্রাউজ করে (মানুষ হিসেবে, বৈধভাবে) দেখা প্রকৃত ডেটা
# এখানে দিলে সেটাই "যাচাইকৃত বাস্তব ডেটা" হিসেবে অ্যাপে দেখানো হয়।
# X-Admin-Key হেডার দিয়ে সুরক্ষিত।
# ---------------------------------------------------------------
class ManualMoverIn(BaseModel):
    trading_code: str
    company_name: str
    ltp: float
    change_percent: float
    volume: int = 0


class ManualIndexIn(BaseModel):
    close_value: float
    change_value: float
    change_percent: float
    total_volume: int = 0
    total_turnover: float = 0


class ManualEntryIn(BaseModel):
    index: ManualIndexIn | None = None
    gainers: list[ManualMoverIn] = []
    losers: list[ManualMoverIn] = []


def _check_admin(x_admin_key: str | None):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(401, "ভুল Admin Key")


@app.post("/v1/admin/manual-entry")
def submit_manual_entry(entry: ManualEntryIn, x_admin_key: str | None = Header(default=None)):
    _check_admin(x_admin_key)
    today = date.today()
    index_data = entry.index.model_dump() if entry.index else None
    gainers = [g.model_dump() for g in entry.gainers] if entry.gainers else []
    losers = [l.model_dump() for l in entry.losers] if entry.losers else []
    _save_manual_entry(today, index_data, gainers, losers)
    return {
        "message_bn": f"{today.isoformat()}-এর জন্য যাচাইকৃত ডেটা সেভ করা হয়েছে",
        "storage": "database (স্থায়ী)" if DB_AVAILABLE else "in-memory (সার্ভার রিস্টার্ট হলে হারিয়ে যাবে)",
    }


@app.get("/v1/admin/manual-entry/today")
def get_today_manual_entry(x_admin_key: str | None = Header(default=None)):
    _check_admin(x_admin_key)
    today = date.today()
    return {
        "index": _get_manual_index(today),
        "gainers": _get_manual_movers(today, "gainer"),
        "losers": _get_manual_movers(today, "loser"),
        "storage": "database" if DB_AVAILABLE else "in-memory",
    }


@app.delete("/v1/admin/manual-entry/today")
def clear_today_manual_entry(x_admin_key: str | None = Header(default=None)):
    _check_admin(x_admin_key)
    _clear_manual_entry(date.today())
    return {"message_bn": "আজকের যাচাইকৃত ডেটা মুছে ফেলা হয়েছে, ডেমো ডেটায় ফিরে যাওয়া হয়েছে"}


@app.get("/")
def root():
    return {
        "message": "StockPilot BD AI Market API চলছে। /app এ যান লাইভ ড্যাশবোর্ড দেখতে, অথবা /docs এ API বিস্তারিত দেখতে।",
        "database": "connected" if DB_AVAILABLE else "not connected (in-memory fallback active)",
    }


# ---------------------------------------------------------------
# Bookmarklet ইনস্টল পেজ — dsebd.org থেকে ডেটা তোলার সহায়ক টুল
# ---------------------------------------------------------------
BOOKMARKLET_URI = """javascript:%0A(function%20()%20%7B%0A%20%20%22use%20strict%22%3B%0A%0A%20%20const%20API_BASE%20%3D%20%22https%3A%2F%2Fmarket-backend-v4-production.up.railway.app%22%3B%0A%0A%20%20%2F%2F%20----------%20%E0%A7%A7.%20%E0%A6%AA%E0%A7%87%E0%A6%9C%20%E0%A6%B8%E0%A7%8D%E0%A6%95%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%A8%20%E0%A6%95%E0%A6%B0%E0%A7%87%20%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%8D%E0%A6%AF%20%E0%A6%A1%E0%A7%87%E0%A6%9F%E0%A6%BE%20%E0%A6%96%E0%A7%8B%E0%A6%81%E0%A6%9C%E0%A6%BE%20----------%0A%20%20function%20scanPage()%20%7B%0A%20%20%20%20const%20bodyText%20%3D%20document.body.innerText%20%7C%7C%20%22%22%3B%0A%0A%20%20%20%20%2F%2F%20DSEX-%E0%A6%8F%E0%A6%B0%20%E0%A6%95%E0%A6%BE%E0%A6%9B%E0%A6%BE%E0%A6%95%E0%A6%BE%E0%A6%9B%E0%A6%BF%20%E0%A6%B8%E0%A6%82%E0%A6%96%E0%A7%8D%E0%A6%AF%E0%A6%BE%20%E0%A6%96%E0%A7%8B%E0%A6%81%E0%A6%9C%E0%A6%BE%20(%E0%A6%AF%E0%A7%87%E0%A6%AE%E0%A6%A8%20%22DSEX%206%2C234.12%20%2B42.35%20(0.68%25)%22)%0A%20%20%20%20let%20dsexGuess%20%3D%20null%3B%0A%20%20%20%20const%20dsexMatch%20%3D%20bodyText.match(%2FDSEX%5B%5E%5Cd%5C-%5D%7B0%2C20%7D(%5B%5Cd%2C%5D%2B%5C.%3F%5Cd*)%2Fi)%3B%0A%20%20%20%20if%20(dsexMatch)%20%7B%0A%20%20%20%20%20%20const%20changeMatch%20%3D%20bodyText%0A%20%20%20%20%20%20%20%20.slice(bodyText.indexOf(dsexMatch%5B0%5D)%2C%20bodyText.indexOf(dsexMatch%5B0%5D)%20%2B%20200)%0A%20%20%20%20%20%20%20%20.match(%2F(%5B%2B%5C-%5D%3F%5Cd%2B%5C.%3F%5Cd*)%5Cs*%5C(%3F(%5B%2B%5C-%5D%3F%5Cd%2B%5C.%3F%5Cd*)%25%3F%5C)%3F%2F)%3B%0A%20%20%20%20%20%20dsexGuess%20%3D%20%7B%0A%20%20%20%20%20%20%20%20close_value%3A%20parseFloat(dsexMatch%5B1%5D.replace(%2F%2C%2Fg%2C%20%22%22))%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20change_value%3A%20changeMatch%20%3F%20parseFloat(changeMatch%5B1%5D)%20%7C%7C%200%20%3A%200%2C%0A%20%20%20%20%20%20%20%20change_percent%3A%20changeMatch%20%3F%20parseFloat(changeMatch%5B2%5D)%20%7C%7C%200%20%3A%200%2C%0A%20%20%20%20%20%20%20%20total_volume%3A%200%2C%0A%20%20%20%20%20%20%20%20total_turnover%3A%200%2C%0A%20%20%20%20%20%20%7D%3B%0A%20%20%20%20%7D%0A%0A%20%20%20%20%2F%2F%20%E0%A6%9F%E0%A7%87%E0%A6%AC%E0%A6%BF%E0%A6%B2%E0%A7%87%E0%A6%B0%20%E0%A6%B8%E0%A6%BE%E0%A6%B0%E0%A6%BF%20%E0%A6%B8%E0%A7%8D%E0%A6%95%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%A8%20%E0%A6%95%E0%A6%B0%E0%A7%87%20%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%8D%E0%A6%AF%20%E0%A6%95%E0%A7%8B%E0%A6%AE%E0%A7%8D%E0%A6%AA%E0%A6%BE%E0%A6%A8%E0%A6%BF-%E0%A6%95%E0%A7%8B%E0%A6%A1%20%2B%20%E0%A6%A6%E0%A6%BE%E0%A6%AE%20%2B%20%E0%A6%AA%E0%A6%B0%E0%A6%BF%E0%A6%AC%E0%A6%B0%E0%A7%8D%E0%A6%A4%E0%A6%A8%20%25%20%E0%A6%96%E0%A7%8B%E0%A6%81%E0%A6%9C%E0%A6%BE%0A%20%20%20%20const%20candidates%20%3D%20%5B%5D%3B%0A%20%20%20%20document.querySelectorAll(%22table%20tr%22).forEach((row)%20%3D%3E%20%7B%0A%20%20%20%20%20%20const%20cells%20%3D%20Array.from(row.querySelectorAll(%22td%2Cth%22)).map((td)%20%3D%3E%20td.innerText.trim())%3B%0A%20%20%20%20%20%20if%20(cells.length%20%3C%203)%20return%3B%0A%20%20%20%20%20%20const%20codeCell%20%3D%20cells%5B0%5D%3B%0A%20%20%20%20%20%20%2F%2F%20%E0%A6%95%E0%A7%8B%E0%A6%AE%E0%A7%8D%E0%A6%AA%E0%A6%BE%E0%A6%A8%E0%A6%BF%20%E0%A6%95%E0%A7%8B%E0%A6%A1%E0%A7%87%E0%A6%B0%20%E0%A6%AE%E0%A6%A4%E0%A7%8B%3A%20%E0%A6%AC%E0%A6%A1%E0%A6%BC%20%E0%A6%B9%E0%A6%BE%E0%A6%A4%E0%A7%87%E0%A6%B0%20%E0%A6%85%E0%A6%95%E0%A7%8D%E0%A6%B7%E0%A6%B0%2F%E0%A6%B8%E0%A6%82%E0%A6%96%E0%A7%8D%E0%A6%AF%E0%A6%BE%2C%20%E0%A6%B8%E0%A7%8D%E0%A6%AA%E0%A7%87%E0%A6%B8%20%E0%A6%A8%E0%A7%87%E0%A6%87%2C%20%E0%A7%A8-%E0%A7%A7%E0%A7%AB%20%E0%A6%95%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%B0%E0%A7%87%E0%A6%95%E0%A7%8D%E0%A6%9F%E0%A6%BE%E0%A6%B0%0A%20%20%20%20%20%20if%20(!%2F%5E%5BA-Z0-9%5D%7B2%2C15%7D%24%2F.test(codeCell))%20return%3B%0A%20%20%20%20%20%20%2F%2F%20%E0%A6%B8%E0%A6%82%E0%A6%96%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%AF%E0%A7%81%E0%A6%95%E0%A7%8D%E0%A6%A4%20%E0%A6%B8%E0%A7%87%E0%A6%B2%20%E0%A6%96%E0%A7%81%E0%A6%81%E0%A6%9C%E0%A6%BF%20(%E0%A6%A6%E0%A6%BE%E0%A6%AE%2C%20%E0%A6%AA%E0%A6%B0%E0%A6%BF%E0%A6%AC%E0%A6%B0%E0%A7%8D%E0%A6%A4%E0%A6%A8%20%25)%0A%20%20%20%20%20%20const%20numericCells%20%3D%20cells.slice(1).map((c)%20%3D%3E%20parseFloat(c.replace(%2F%2C%2Fg%2C%20%22%22).replace(%2F%25%2Fg%2C%20%22%22)))%3B%0A%20%20%20%20%20%20const%20validNums%20%3D%20numericCells.filter((n)%20%3D%3E%20!isNaN(n))%3B%0A%20%20%20%20%20%20if%20(validNums.length%20%3C%202)%20return%3B%0A%20%20%20%20%20%20candidates.push(%7B%0A%20%20%20%20%20%20%20%20trading_code%3A%20codeCell%2C%0A%20%20%20%20%20%20%20%20company_name%3A%20codeCell%2C%0A%20%20%20%20%20%20%20%20ltp%3A%20validNums%5B0%5D%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20change_percent%3A%20validNums.find((n)%20%3D%3E%20Math.abs(n)%20%3C%2020%20%26%26%20n%20!%3D%3D%20validNums%5B0%5D)%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20volume%3A%20Math.round(validNums.find((n)%20%3D%3E%20n%20%3E%201000)%20%7C%7C%200)%2C%0A%20%20%20%20%20%20%7D)%3B%0A%20%20%20%20%7D)%3B%0A%0A%20%20%20%20return%20%7B%20dsexGuess%2C%20candidates%3A%20candidates.slice(0%2C%2040)%20%7D%3B%0A%20%20%7D%0A%0A%20%20%2F%2F%20----------%20%E0%A7%A8.%20%E0%A6%B0%E0%A6%BF%E0%A6%AD%E0%A6%BF%E0%A6%89%20%E0%A6%AA%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%A8%E0%A7%87%E0%A6%B2%20UI%20%E0%A6%A4%E0%A7%88%E0%A6%B0%E0%A6%BF%20%E0%A6%95%E0%A6%B0%E0%A6%BE%20----------%0A%20%20function%20buildPanel(scanResult)%20%7B%0A%20%20%20%20const%20old%20%3D%20document.getElementById(%22sp-bd-panel%22)%3B%0A%20%20%20%20if%20(old)%20old.remove()%3B%0A%0A%20%20%20%20const%20panel%20%3D%20document.createElement(%22div%22)%3B%0A%20%20%20%20panel.id%20%3D%20%22sp-bd-panel%22%3B%0A%20%20%20%20panel.style.cssText%20%3D%0A%20%20%20%20%20%20%22position%3Afixed%3Btop%3A10px%3Bright%3A10px%3Bwidth%3A380px%3Bmax-height%3A90vh%3Boverflow%3Aauto%3B%22%20%2B%0A%20%20%20%20%20%20%22background%3A%2312151A%3Bcolor%3A%23EDEFF2%3Bborder%3A1px%20solid%20%233EC98B%3Bborder-radius%3A12px%3B%22%20%2B%0A%20%20%20%20%20%20%22padding%3A16px%3Bz-index%3A999999%3Bfont-family%3Asans-serif%3Bfont-size%3A13px%3Bbox-shadow%3A0%208px%2030px%20rgba(0%2C0%2C0%2C.5)%3B%22%3B%0A%0A%20%20%20%20const%20gainers%20%3D%20%5B...scanResult.candidates%5D.sort((a%2C%20b)%20%3D%3E%20b.change_percent%20-%20a.change_percent).slice(0%2C%205)%3B%0A%20%20%20%20const%20losers%20%3D%20%5B...scanResult.candidates%5D.sort((a%2C%20b)%20%3D%3E%20a.change_percent%20-%20b.change_percent).slice(0%2C%205)%3B%0A%0A%20%20%20%20panel.innerHTML%20%3D%20%60%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22font-weight%3A700%3Bfont-size%3A15px%3Bmargin-bottom%3A8px%3B%22%3E%F0%9F%93%8A%20StockPilot%20BD%20AI%20%E2%80%94%20%E0%A6%A1%E0%A7%87%E0%A6%9F%E0%A6%BE%20%E0%A6%AF%E0%A6%BE%E0%A6%9A%E0%A6%BE%E0%A6%87%20%E0%A6%95%E0%A6%B0%E0%A7%81%E0%A6%A8%3C%2Fdiv%3E%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22color%3A%23F0A18F%3Bfont-size%3A11.5px%3Bmargin-bottom%3A12px%3B%22%3E%E2%9A%A0%EF%B8%8F%20%E0%A6%B8%E0%A7%8D%E0%A6%AC%E0%A6%AF%E0%A6%BC%E0%A6%82%E0%A6%95%E0%A7%8D%E0%A6%B0%E0%A6%BF%E0%A6%AF%E0%A6%BC%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%87%20%E0%A6%96%E0%A7%81%E0%A6%81%E0%A6%9C%E0%A7%87%20%E0%A6%AA%E0%A6%BE%E0%A6%93%E0%A6%AF%E0%A6%BC%E0%A6%BE%20%E0%A6%B8%E0%A6%82%E0%A6%96%E0%A7%8D%E0%A6%AF%E0%A6%BE%20%E2%80%94%20%E0%A6%AA%E0%A6%BE%E0%A6%A0%E0%A6%BE%E0%A6%A8%E0%A7%8B%E0%A6%B0%20%E0%A6%86%E0%A6%97%E0%A7%87%20%E0%A6%85%E0%A6%AC%E0%A6%B6%E0%A7%8D%E0%A6%AF%E0%A6%87%20dsebd.org-%E0%A6%8F%E0%A6%B0%20%E0%A6%B8%E0%A6%BE%E0%A6%A5%E0%A7%87%20%E0%A6%AE%E0%A6%BF%E0%A6%B2%E0%A6%BF%E0%A6%AF%E0%A6%BC%E0%A7%87%20%E0%A6%A6%E0%A7%87%E0%A6%96%E0%A7%81%E0%A6%A8%20%E0%A6%93%20%E0%A6%AA%E0%A7%8D%E0%A6%B0%E0%A6%AF%E0%A6%BC%E0%A7%8B%E0%A6%9C%E0%A6%A8%E0%A7%87%20%E0%A6%A0%E0%A6%BF%E0%A6%95%20%E0%A6%95%E0%A6%B0%E0%A7%81%E0%A6%A8%E0%A5%A4%3C%2Fdiv%3E%0A%0A%20%20%20%20%20%20%3Clabel%20style%3D%22display%3Ablock%3Bmargin-bottom%3A4px%3Bcolor%3A%237D8590%3B%22%3EAdmin%20Key%3C%2Flabel%3E%0A%20%20%20%20%20%20%3Cinput%20id%3D%22sp-key%22%20type%3D%22password%22%20style%3D%22width%3A100%25%3Bpadding%3A6px%3Bmargin-bottom%3A10px%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3B%22%20placeholder%3D%22Admin%20Key%20%E0%A6%A6%E0%A6%BF%E0%A6%A8%22%3E%0A%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22font-weight%3A600%3Bmargin-bottom%3A4px%3B%22%3EDSEX%20%E0%A6%87%E0%A6%A8%E0%A6%A1%E0%A7%87%E0%A6%95%E0%A7%8D%E0%A6%B8%3C%2Fdiv%3E%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22display%3Agrid%3Bgrid-template-columns%3A1fr%201fr%3Bgap%3A6px%3Bmargin-bottom%3A10px%3B%22%3E%0A%20%20%20%20%20%20%20%20%3Cinput%20id%3D%22sp-close%22%20placeholder%3D%22%E0%A6%95%E0%A7%8D%E0%A6%B2%E0%A7%8B%E0%A6%9C%E0%A6%BF%E0%A6%82%22%20value%3D%22%24%7BscanResult.dsexGuess%20%3F%20scanResult.dsexGuess.close_value%20%3A%20%22%22%7D%22%20style%3D%22padding%3A6px%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3B%22%3E%0A%20%20%20%20%20%20%20%20%3Cinput%20id%3D%22sp-change%22%20placeholder%3D%22%E0%A6%AA%E0%A6%B0%E0%A6%BF%E0%A6%AC%E0%A6%B0%E0%A7%8D%E0%A6%A4%E0%A6%A8%22%20value%3D%22%24%7BscanResult.dsexGuess%20%3F%20scanResult.dsexGuess.change_value%20%3A%20%22%22%7D%22%20style%3D%22padding%3A6px%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3B%22%3E%0A%20%20%20%20%20%20%20%20%3Cinput%20id%3D%22sp-changepct%22%20placeholder%3D%22%E0%A6%AA%E0%A6%B0%E0%A6%BF%E0%A6%AC%E0%A6%B0%E0%A7%8D%E0%A6%A4%E0%A6%A8%20%25%22%20value%3D%22%24%7BscanResult.dsexGuess%20%3F%20scanResult.dsexGuess.change_percent%20%3A%20%22%22%7D%22%20style%3D%22padding%3A6px%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3B%22%3E%0A%20%20%20%20%20%20%20%20%3Cinput%20id%3D%22sp-volume%22%20placeholder%3D%22%E0%A6%AD%E0%A6%B2%E0%A6%BF%E0%A6%89%E0%A6%AE%22%20style%3D%22padding%3A6px%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3B%22%3E%0A%20%20%20%20%20%20%3C%2Fdiv%3E%0A%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22font-weight%3A600%3Bmargin-bottom%3A4px%3B%22%3E%E0%A6%97%E0%A7%87%E0%A6%87%E0%A6%A8%E0%A6%BE%E0%A6%B0%E0%A7%8D%E0%A6%B8%20(%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%8D%E0%A6%AF%2C%20%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AA%E0%A6%BE%E0%A6%A6%E0%A6%A8%E0%A6%BE%E0%A6%AF%E0%A7%8B%E0%A6%97%E0%A7%8D%E0%A6%AF)%3C%2Fdiv%3E%0A%20%20%20%20%20%20%3Ctextarea%20id%3D%22sp-gainers%22%20rows%3D%224%22%20style%3D%22width%3A100%25%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3Bpadding%3A6px%3Bfont-family%3Amonospace%3Bfont-size%3A11px%3Bmargin-bottom%3A10px%3B%22%3E%24%7Bgainers.map((g)%20%3D%3E%20%60%24%7Bg.trading_code%7D%2C%24%7Bg.company_name%7D%2C%24%7Bg.ltp%7D%2C%24%7Bg.change_percent%7D%2C%24%7Bg.volume%7D%60).join(%22%5Cn%22)%7D%3C%2Ftextarea%3E%0A%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22font-weight%3A600%3Bmargin-bottom%3A4px%3B%22%3E%E0%A6%B2%E0%A7%81%E0%A6%9C%E0%A6%BE%E0%A6%B0%E0%A7%8D%E0%A6%B8%20(%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%8D%E0%A6%AF%2C%20%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AA%E0%A6%BE%E0%A6%A6%E0%A6%A8%E0%A6%BE%E0%A6%AF%E0%A7%8B%E0%A6%97%E0%A7%8D%E0%A6%AF)%3C%2Fdiv%3E%0A%20%20%20%20%20%20%3Ctextarea%20id%3D%22sp-losers%22%20rows%3D%224%22%20style%3D%22width%3A100%25%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3Bpadding%3A6px%3Bfont-family%3Amonospace%3Bfont-size%3A11px%3Bmargin-bottom%3A10px%3B%22%3E%24%7Blosers.map((g)%20%3D%3E%20%60%24%7Bg.trading_code%7D%2C%24%7Bg.company_name%7D%2C%24%7Bg.ltp%7D%2C%24%7Bg.change_percent%7D%2C%24%7Bg.volume%7D%60).join(%22%5Cn%22)%7D%3C%2Ftextarea%3E%0A%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22display%3Aflex%3Bgap%3A8px%3B%22%3E%0A%20%20%20%20%20%20%20%20%3Cbutton%20id%3D%22sp-send%22%20style%3D%22flex%3A1%3Bbackground%3A%233EC98B%3Bcolor%3A%230C1210%3Bborder%3Anone%3Bborder-radius%3A8px%3Bpadding%3A10px%3Bfont-weight%3A700%3Bcursor%3Apointer%3B%22%3E%E2%9C%85%20%E0%A6%AF%E0%A6%BE%E0%A6%9A%E0%A6%BE%E0%A6%87%20%E0%A6%95%E0%A6%B0%E0%A7%87%20%E0%A6%AA%E0%A6%BE%E0%A6%A0%E0%A6%BE%E0%A6%A8%3C%2Fbutton%3E%0A%20%20%20%20%20%20%20%20%3Cbutton%20id%3D%22sp-close-panel%22%20style%3D%22background%3A%233A1E1E%3Bcolor%3A%23F0A18F%3Bborder%3Anone%3Bborder-radius%3A8px%3Bpadding%3A10px%3Bcursor%3Apointer%3B%22%3E%E2%9C%95%3C%2Fbutton%3E%0A%20%20%20%20%20%20%3C%2Fdiv%3E%0A%20%20%20%20%20%20%3Cdiv%20id%3D%22sp-msg%22%20style%3D%22margin-top%3A8px%3Bfont-size%3A12px%3B%22%3E%3C%2Fdiv%3E%0A%20%20%20%20%60%3B%0A%0A%20%20%20%20document.body.appendChild(panel)%3B%0A%0A%20%20%20%20document.getElementById(%22sp-close-panel%22).onclick%20%3D%20()%20%3D%3E%20panel.remove()%3B%0A%20%20%20%20document.getElementById(%22sp-send%22).onclick%20%3D%20()%20%3D%3E%20sendData(panel)%3B%0A%0A%20%20%20%20%2F%2F%20%E0%A6%86%E0%A6%97%E0%A7%87%20%E0%A6%B8%E0%A7%87%E0%A6%AD%20%E0%A6%95%E0%A6%B0%E0%A6%BE%20Admin%20Key%20%E0%A6%A5%E0%A6%BE%E0%A6%95%E0%A6%B2%E0%A7%87%20%E0%A6%AD%E0%A6%B0%E0%A7%87%20%E0%A6%A6%E0%A6%BE%E0%A6%93%20(localStorage%20%E2%80%94%20%E0%A6%8F%E0%A6%87%20%E0%A6%AC%E0%A7%8D%E0%A6%B0%E0%A6%BE%E0%A6%89%E0%A6%9C%E0%A6%BE%E0%A6%B0%E0%A7%87%E0%A6%87%20%E0%A6%A5%E0%A6%BE%E0%A6%95%E0%A7%87)%0A%20%20%20%20const%20savedKey%20%3D%20localStorage.getItem(%22sp_admin_key%22)%3B%0A%20%20%20%20if%20(savedKey)%20document.getElementById(%22sp-key%22).value%20%3D%20savedKey%3B%0A%20%20%7D%0A%0A%20%20function%20parseLines(text)%20%7B%0A%20%20%20%20return%20text%0A%20%20%20%20%20%20.split(%22%5Cn%22)%0A%20%20%20%20%20%20.map((l)%20%3D%3E%20l.trim())%0A%20%20%20%20%20%20.filter(Boolean)%0A%20%20%20%20%20%20.map((line)%20%3D%3E%20%7B%0A%20%20%20%20%20%20%20%20const%20p%20%3D%20line.split(%22%2C%22).map((x)%20%3D%3E%20x.trim())%3B%0A%20%20%20%20%20%20%20%20return%20%7B%0A%20%20%20%20%20%20%20%20%20%20trading_code%3A%20p%5B0%5D%20%7C%7C%20%22%22%2C%0A%20%20%20%20%20%20%20%20%20%20company_name%3A%20p%5B1%5D%20%7C%7C%20p%5B0%5D%20%7C%7C%20%22%22%2C%0A%20%20%20%20%20%20%20%20%20%20ltp%3A%20parseFloat(p%5B2%5D)%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20%20%20change_percent%3A%20parseFloat(p%5B3%5D)%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20%20%20volume%3A%20parseInt(p%5B4%5D%2C%2010)%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20%7D%3B%0A%20%20%20%20%20%20%7D)%0A%20%20%20%20%20%20.filter((r)%20%3D%3E%20r.trading_code)%3B%0A%20%20%7D%0A%0A%20%20async%20function%20sendData(panel)%20%7B%0A%20%20%20%20const%20key%20%3D%20document.getElementById(%22sp-key%22).value.trim()%3B%0A%20%20%20%20const%20msg%20%3D%20document.getElementById(%22sp-msg%22)%3B%0A%20%20%20%20if%20(!key)%20%7B%0A%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9A%A0%EF%B8%8F%20Admin%20Key%20%E0%A6%A6%E0%A6%BF%E0%A6%A8%22%3B%0A%20%20%20%20%20%20msg.style.color%20%3D%20%22%23F0A18F%22%3B%0A%20%20%20%20%20%20return%3B%0A%20%20%20%20%7D%0A%20%20%20%20localStorage.setItem(%22sp_admin_key%22%2C%20key)%3B%0A%0A%20%20%20%20const%20close_value%20%3D%20parseFloat(document.getElementById(%22sp-close%22).value)%3B%0A%20%20%20%20const%20change_value%20%3D%20parseFloat(document.getElementById(%22sp-change%22).value)%3B%0A%20%20%20%20const%20change_percent%20%3D%20parseFloat(document.getElementById(%22sp-changepct%22).value)%3B%0A%20%20%20%20const%20total_volume%20%3D%20parseInt(document.getElementById(%22sp-volume%22).value%2C%2010)%20%7C%7C%200%3B%0A%0A%20%20%20%20const%20body%20%3D%20%7B%7D%3B%0A%20%20%20%20if%20(!isNaN(close_value)%20%26%26%20!isNaN(change_value)%20%26%26%20!isNaN(change_percent))%20%7B%0A%20%20%20%20%20%20body.index%20%3D%20%7B%20close_value%2C%20change_value%2C%20change_percent%2C%20total_volume%2C%20total_turnover%3A%200%20%7D%3B%0A%20%20%20%20%7D%0A%20%20%20%20const%20gainers%20%3D%20parseLines(document.getElementById(%22sp-gainers%22).value)%3B%0A%20%20%20%20const%20losers%20%3D%20parseLines(document.getElementById(%22sp-losers%22).value)%3B%0A%20%20%20%20if%20(gainers.length)%20body.gainers%20%3D%20gainers%3B%0A%20%20%20%20if%20(losers.length)%20body.losers%20%3D%20losers%3B%0A%0A%20%20%20%20if%20(!body.index%20%26%26%20!gainers.length%20%26%26%20!losers.length)%20%7B%0A%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9A%A0%EF%B8%8F%20%E0%A6%85%E0%A6%A8%E0%A7%8D%E0%A6%A4%E0%A6%A4%20%E0%A6%8F%E0%A6%95%E0%A6%9F%E0%A6%BE%20%E0%A6%A4%E0%A6%A5%E0%A7%8D%E0%A6%AF%20(%E0%A6%87%E0%A6%A8%E0%A6%A1%E0%A7%87%E0%A6%95%E0%A7%8D%E0%A6%B8%20%E0%A6%AC%E0%A6%BE%20%E0%A6%97%E0%A7%87%E0%A6%87%E0%A6%A8%E0%A6%BE%E0%A6%B0%E0%A7%8D%E0%A6%B8%2F%E0%A6%B2%E0%A7%81%E0%A6%9C%E0%A6%BE%E0%A6%B0%E0%A7%8D%E0%A6%B8)%20%E0%A6%A6%E0%A6%BF%E0%A6%A8%22%3B%0A%20%20%20%20%20%20msg.style.color%20%3D%20%22%23F0A18F%22%3B%0A%20%20%20%20%20%20return%3B%0A%20%20%20%20%7D%0A%0A%20%20%20%20msg.textContent%20%3D%20%22%E0%A6%AA%E0%A6%BE%E0%A6%A0%E0%A6%BE%E0%A6%A8%E0%A7%8B%20%E0%A6%B9%E0%A6%9A%E0%A7%8D%E0%A6%9B%E0%A7%87...%22%3B%0A%20%20%20%20msg.style.color%20%3D%20%22%239199A3%22%3B%0A%20%20%20%20try%20%7B%0A%20%20%20%20%20%20const%20res%20%3D%20await%20fetch(API_BASE%20%2B%20%22%2Fv1%2Fadmin%2Fmanual-entry%22%2C%20%7B%0A%20%20%20%20%20%20%20%20method%3A%20%22POST%22%2C%0A%20%20%20%20%20%20%20%20headers%3A%20%7B%20%22Content-Type%22%3A%20%22application%2Fjson%22%2C%20%22X-Admin-Key%22%3A%20key%20%7D%2C%0A%20%20%20%20%20%20%20%20body%3A%20JSON.stringify(body)%2C%0A%20%20%20%20%20%20%7D)%3B%0A%20%20%20%20%20%20if%20(res.status%20%3D%3D%3D%20401)%20%7B%0A%20%20%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9D%8C%20%E0%A6%AD%E0%A7%81%E0%A6%B2%20Admin%20Key%22%3B%0A%20%20%20%20%20%20%20%20msg.style.color%20%3D%20%22%23F0A18F%22%3B%0A%20%20%20%20%20%20%20%20return%3B%0A%20%20%20%20%20%20%7D%0A%20%20%20%20%20%20if%20(!res.ok)%20%7B%0A%20%20%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9D%8C%20%E0%A6%AA%E0%A6%BE%E0%A6%A0%E0%A6%BE%E0%A6%A8%E0%A7%8B%20%E0%A6%AF%E0%A6%BE%E0%A6%AF%E0%A6%BC%E0%A6%A8%E0%A6%BF%20(%E0%A6%B8%E0%A7%8D%E0%A6%9F%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%9F%E0%A6%BE%E0%A6%B8%20%22%20%2B%20res.status%20%2B%20%22)%22%3B%0A%20%20%20%20%20%20%20%20msg.style.color%20%3D%20%22%23F0A18F%22%3B%0A%20%20%20%20%20%20%20%20return%3B%0A%20%20%20%20%20%20%7D%0A%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9C%85%20%E0%A6%B8%E0%A6%AB%E0%A6%B2%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%87%20%E0%A6%AA%E0%A6%BE%E0%A6%A0%E0%A6%BE%E0%A6%A8%E0%A7%8B%20%E0%A6%B9%E0%A6%AF%E0%A6%BC%E0%A7%87%E0%A6%9B%E0%A7%87!%20%E0%A6%85%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%AA%E0%A7%87%20%E0%A6%97%E0%A6%BF%E0%A6%AF%E0%A6%BC%E0%A7%87%20%E0%A6%B0%E0%A6%BF%E0%A6%AB%E0%A7%8D%E0%A6%B0%E0%A7%87%E0%A6%B6%20%E0%A6%95%E0%A6%B0%E0%A7%81%E0%A6%A8%E0%A5%A4%22%3B%0A%20%20%20%20%20%20msg.style.color%20%3D%20%22%233EC98B%22%3B%0A%20%20%20%20%7D%20catch%20(e)%20%7B%0A%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9D%8C%20%E0%A6%B8%E0%A6%BE%E0%A6%B0%E0%A7%8D%E0%A6%AD%E0%A6%BE%E0%A6%B0%20%E0%A6%B8%E0%A6%82%E0%A6%AF%E0%A7%8B%E0%A6%97%E0%A7%87%20%E0%A6%B8%E0%A6%AE%E0%A6%B8%E0%A7%8D%E0%A6%AF%E0%A6%BE%3A%20%22%20%2B%20e.message%3B%0A%20%20%20%20%20%20msg.style.color%20%3D%20%22%23F0A18F%22%3B%0A%20%20%20%20%7D%0A%20%20%7D%0A%0A%20%20%2F%2F%20----------%20%E0%A6%9A%E0%A6%BE%E0%A6%B2%E0%A7%81%20%E0%A6%95%E0%A6%B0%E0%A6%BE%20----------%0A%20%20const%20result%20%3D%20scanPage()%3B%0A%20%20buildPanel(result)%3B%0A%7D)()%3B%0A"""

BOOKMARKLET_PAGE = """<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>StockPilot BD AI — Bookmarklet ইনস্টল</title>
<style>
  body { margin:0; background:#12151A; color:#EDEFF2; font-family:'Hind Siliguri','Inter',system-ui,sans-serif; padding:24px 20px 60px; }
  .wrap { max-width:640px; margin:0 auto; }
  h1 { font-size:22px; margin-bottom:6px; }
  .step { background:#171B22; border:1px solid #262B33; border-radius:12px; padding:16px 18px; margin-bottom:14px; }
  .step-num { color:#3EC98B; font-weight:700; font-size:12px; letter-spacing:.08em; }
  .bookmarklet-btn { display:inline-block; margin:12px 0; background:#3EC98B; color:#0C1210 !important; padding:14px 22px; border-radius:10px; font-weight:700; text-decoration:none; font-size:15px; }
  code { background:#1C2028; padding:2px 6px; border-radius:4px; font-size:12.5px; }
  .warn { background:#3A1E1E; color:#F0A18F; border-radius:8px; padding:10px 14px; font-size:13px; margin-bottom:16px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>📌 StockPilot BD AI — dsebd.org ডেটা এক্সট্র্যাক্টর</h1>
  <div class="warn">⚠️ এটা পেজ স্ক্যান করে সংখ্যা <b>অনুমান</b> করে — পাঠানোর আগে সবসময় নিজে চোখে যাচাই করুন। ভুল সংখ্যা সরাসরি অ্যাপে চলে যাবে না, আগে একটা প্রিভিউ প্যানেলে দেখাবে।</div>

  <div class="step">
    <div class="step-num">ধাপ ১</div>
    নিচের সবুজ বাটনটা <b>বুকমার্ক বার-এ টেনে আনুন</b> (ডেক্সটপে), অথবা মোবাইলে বাটনে দীর্ঘক্ষণ চেপে ধরে "Add to bookmarks" / লিংক কপি করে ম্যানুয়ালি একটা বুকমার্ক বানান (নিচে মোবাইল নির্দেশনা দেওয়া আছে)।
    <div><a class="bookmarklet-btn" href="javascript:%0A(function%20()%20%7B%0A%20%20%22use%20strict%22%3B%0A%0A%20%20const%20API_BASE%20%3D%20%22https%3A%2F%2Fmarket-backend-v4-production.up.railway.app%22%3B%0A%0A%20%20%2F%2F%20----------%20%E0%A7%A7.%20%E0%A6%AA%E0%A7%87%E0%A6%9C%20%E0%A6%B8%E0%A7%8D%E0%A6%95%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%A8%20%E0%A6%95%E0%A6%B0%E0%A7%87%20%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%8D%E0%A6%AF%20%E0%A6%A1%E0%A7%87%E0%A6%9F%E0%A6%BE%20%E0%A6%96%E0%A7%8B%E0%A6%81%E0%A6%9C%E0%A6%BE%20----------%0A%20%20function%20scanPage()%20%7B%0A%20%20%20%20const%20bodyText%20%3D%20document.body.innerText%20%7C%7C%20%22%22%3B%0A%0A%20%20%20%20%2F%2F%20DSEX-%E0%A6%8F%E0%A6%B0%20%E0%A6%95%E0%A6%BE%E0%A6%9B%E0%A6%BE%E0%A6%95%E0%A6%BE%E0%A6%9B%E0%A6%BF%20%E0%A6%B8%E0%A6%82%E0%A6%96%E0%A7%8D%E0%A6%AF%E0%A6%BE%20%E0%A6%96%E0%A7%8B%E0%A6%81%E0%A6%9C%E0%A6%BE%20(%E0%A6%AF%E0%A7%87%E0%A6%AE%E0%A6%A8%20%22DSEX%206%2C234.12%20%2B42.35%20(0.68%25)%22)%0A%20%20%20%20let%20dsexGuess%20%3D%20null%3B%0A%20%20%20%20const%20dsexMatch%20%3D%20bodyText.match(%2FDSEX%5B%5E%5Cd%5C-%5D%7B0%2C20%7D(%5B%5Cd%2C%5D%2B%5C.%3F%5Cd*)%2Fi)%3B%0A%20%20%20%20if%20(dsexMatch)%20%7B%0A%20%20%20%20%20%20const%20changeMatch%20%3D%20bodyText%0A%20%20%20%20%20%20%20%20.slice(bodyText.indexOf(dsexMatch%5B0%5D)%2C%20bodyText.indexOf(dsexMatch%5B0%5D)%20%2B%20200)%0A%20%20%20%20%20%20%20%20.match(%2F(%5B%2B%5C-%5D%3F%5Cd%2B%5C.%3F%5Cd*)%5Cs*%5C(%3F(%5B%2B%5C-%5D%3F%5Cd%2B%5C.%3F%5Cd*)%25%3F%5C)%3F%2F)%3B%0A%20%20%20%20%20%20dsexGuess%20%3D%20%7B%0A%20%20%20%20%20%20%20%20close_value%3A%20parseFloat(dsexMatch%5B1%5D.replace(%2F%2C%2Fg%2C%20%22%22))%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20change_value%3A%20changeMatch%20%3F%20parseFloat(changeMatch%5B1%5D)%20%7C%7C%200%20%3A%200%2C%0A%20%20%20%20%20%20%20%20change_percent%3A%20changeMatch%20%3F%20parseFloat(changeMatch%5B2%5D)%20%7C%7C%200%20%3A%200%2C%0A%20%20%20%20%20%20%20%20total_volume%3A%200%2C%0A%20%20%20%20%20%20%20%20total_turnover%3A%200%2C%0A%20%20%20%20%20%20%7D%3B%0A%20%20%20%20%7D%0A%0A%20%20%20%20%2F%2F%20%E0%A6%9F%E0%A7%87%E0%A6%AC%E0%A6%BF%E0%A6%B2%E0%A7%87%E0%A6%B0%20%E0%A6%B8%E0%A6%BE%E0%A6%B0%E0%A6%BF%20%E0%A6%B8%E0%A7%8D%E0%A6%95%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%A8%20%E0%A6%95%E0%A6%B0%E0%A7%87%20%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%8D%E0%A6%AF%20%E0%A6%95%E0%A7%8B%E0%A6%AE%E0%A7%8D%E0%A6%AA%E0%A6%BE%E0%A6%A8%E0%A6%BF-%E0%A6%95%E0%A7%8B%E0%A6%A1%20%2B%20%E0%A6%A6%E0%A6%BE%E0%A6%AE%20%2B%20%E0%A6%AA%E0%A6%B0%E0%A6%BF%E0%A6%AC%E0%A6%B0%E0%A7%8D%E0%A6%A4%E0%A6%A8%20%25%20%E0%A6%96%E0%A7%8B%E0%A6%81%E0%A6%9C%E0%A6%BE%0A%20%20%20%20const%20candidates%20%3D%20%5B%5D%3B%0A%20%20%20%20document.querySelectorAll(%22table%20tr%22).forEach((row)%20%3D%3E%20%7B%0A%20%20%20%20%20%20const%20cells%20%3D%20Array.from(row.querySelectorAll(%22td%2Cth%22)).map((td)%20%3D%3E%20td.innerText.trim())%3B%0A%20%20%20%20%20%20if%20(cells.length%20%3C%203)%20return%3B%0A%20%20%20%20%20%20const%20codeCell%20%3D%20cells%5B0%5D%3B%0A%20%20%20%20%20%20%2F%2F%20%E0%A6%95%E0%A7%8B%E0%A6%AE%E0%A7%8D%E0%A6%AA%E0%A6%BE%E0%A6%A8%E0%A6%BF%20%E0%A6%95%E0%A7%8B%E0%A6%A1%E0%A7%87%E0%A6%B0%20%E0%A6%AE%E0%A6%A4%E0%A7%8B%3A%20%E0%A6%AC%E0%A6%A1%E0%A6%BC%20%E0%A6%B9%E0%A6%BE%E0%A6%A4%E0%A7%87%E0%A6%B0%20%E0%A6%85%E0%A6%95%E0%A7%8D%E0%A6%B7%E0%A6%B0%2F%E0%A6%B8%E0%A6%82%E0%A6%96%E0%A7%8D%E0%A6%AF%E0%A6%BE%2C%20%E0%A6%B8%E0%A7%8D%E0%A6%AA%E0%A7%87%E0%A6%B8%20%E0%A6%A8%E0%A7%87%E0%A6%87%2C%20%E0%A7%A8-%E0%A7%A7%E0%A7%AB%20%E0%A6%95%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%B0%E0%A7%87%E0%A6%95%E0%A7%8D%E0%A6%9F%E0%A6%BE%E0%A6%B0%0A%20%20%20%20%20%20if%20(!%2F%5E%5BA-Z0-9%5D%7B2%2C15%7D%24%2F.test(codeCell))%20return%3B%0A%20%20%20%20%20%20%2F%2F%20%E0%A6%B8%E0%A6%82%E0%A6%96%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%AF%E0%A7%81%E0%A6%95%E0%A7%8D%E0%A6%A4%20%E0%A6%B8%E0%A7%87%E0%A6%B2%20%E0%A6%96%E0%A7%81%E0%A6%81%E0%A6%9C%E0%A6%BF%20(%E0%A6%A6%E0%A6%BE%E0%A6%AE%2C%20%E0%A6%AA%E0%A6%B0%E0%A6%BF%E0%A6%AC%E0%A6%B0%E0%A7%8D%E0%A6%A4%E0%A6%A8%20%25)%0A%20%20%20%20%20%20const%20numericCells%20%3D%20cells.slice(1).map((c)%20%3D%3E%20parseFloat(c.replace(%2F%2C%2Fg%2C%20%22%22).replace(%2F%25%2Fg%2C%20%22%22)))%3B%0A%20%20%20%20%20%20const%20validNums%20%3D%20numericCells.filter((n)%20%3D%3E%20!isNaN(n))%3B%0A%20%20%20%20%20%20if%20(validNums.length%20%3C%202)%20return%3B%0A%20%20%20%20%20%20candidates.push(%7B%0A%20%20%20%20%20%20%20%20trading_code%3A%20codeCell%2C%0A%20%20%20%20%20%20%20%20company_name%3A%20codeCell%2C%0A%20%20%20%20%20%20%20%20ltp%3A%20validNums%5B0%5D%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20change_percent%3A%20validNums.find((n)%20%3D%3E%20Math.abs(n)%20%3C%2020%20%26%26%20n%20!%3D%3D%20validNums%5B0%5D)%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20volume%3A%20Math.round(validNums.find((n)%20%3D%3E%20n%20%3E%201000)%20%7C%7C%200)%2C%0A%20%20%20%20%20%20%7D)%3B%0A%20%20%20%20%7D)%3B%0A%0A%20%20%20%20return%20%7B%20dsexGuess%2C%20candidates%3A%20candidates.slice(0%2C%2040)%20%7D%3B%0A%20%20%7D%0A%0A%20%20%2F%2F%20----------%20%E0%A7%A8.%20%E0%A6%B0%E0%A6%BF%E0%A6%AD%E0%A6%BF%E0%A6%89%20%E0%A6%AA%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%A8%E0%A7%87%E0%A6%B2%20UI%20%E0%A6%A4%E0%A7%88%E0%A6%B0%E0%A6%BF%20%E0%A6%95%E0%A6%B0%E0%A6%BE%20----------%0A%20%20function%20buildPanel(scanResult)%20%7B%0A%20%20%20%20const%20old%20%3D%20document.getElementById(%22sp-bd-panel%22)%3B%0A%20%20%20%20if%20(old)%20old.remove()%3B%0A%0A%20%20%20%20const%20panel%20%3D%20document.createElement(%22div%22)%3B%0A%20%20%20%20panel.id%20%3D%20%22sp-bd-panel%22%3B%0A%20%20%20%20panel.style.cssText%20%3D%0A%20%20%20%20%20%20%22position%3Afixed%3Btop%3A10px%3Bright%3A10px%3Bwidth%3A380px%3Bmax-height%3A90vh%3Boverflow%3Aauto%3B%22%20%2B%0A%20%20%20%20%20%20%22background%3A%2312151A%3Bcolor%3A%23EDEFF2%3Bborder%3A1px%20solid%20%233EC98B%3Bborder-radius%3A12px%3B%22%20%2B%0A%20%20%20%20%20%20%22padding%3A16px%3Bz-index%3A999999%3Bfont-family%3Asans-serif%3Bfont-size%3A13px%3Bbox-shadow%3A0%208px%2030px%20rgba(0%2C0%2C0%2C.5)%3B%22%3B%0A%0A%20%20%20%20const%20gainers%20%3D%20%5B...scanResult.candidates%5D.sort((a%2C%20b)%20%3D%3E%20b.change_percent%20-%20a.change_percent).slice(0%2C%205)%3B%0A%20%20%20%20const%20losers%20%3D%20%5B...scanResult.candidates%5D.sort((a%2C%20b)%20%3D%3E%20a.change_percent%20-%20b.change_percent).slice(0%2C%205)%3B%0A%0A%20%20%20%20panel.innerHTML%20%3D%20%60%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22font-weight%3A700%3Bfont-size%3A15px%3Bmargin-bottom%3A8px%3B%22%3E%F0%9F%93%8A%20StockPilot%20BD%20AI%20%E2%80%94%20%E0%A6%A1%E0%A7%87%E0%A6%9F%E0%A6%BE%20%E0%A6%AF%E0%A6%BE%E0%A6%9A%E0%A6%BE%E0%A6%87%20%E0%A6%95%E0%A6%B0%E0%A7%81%E0%A6%A8%3C%2Fdiv%3E%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22color%3A%23F0A18F%3Bfont-size%3A11.5px%3Bmargin-bottom%3A12px%3B%22%3E%E2%9A%A0%EF%B8%8F%20%E0%A6%B8%E0%A7%8D%E0%A6%AC%E0%A6%AF%E0%A6%BC%E0%A6%82%E0%A6%95%E0%A7%8D%E0%A6%B0%E0%A6%BF%E0%A6%AF%E0%A6%BC%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%87%20%E0%A6%96%E0%A7%81%E0%A6%81%E0%A6%9C%E0%A7%87%20%E0%A6%AA%E0%A6%BE%E0%A6%93%E0%A6%AF%E0%A6%BC%E0%A6%BE%20%E0%A6%B8%E0%A6%82%E0%A6%96%E0%A7%8D%E0%A6%AF%E0%A6%BE%20%E2%80%94%20%E0%A6%AA%E0%A6%BE%E0%A6%A0%E0%A6%BE%E0%A6%A8%E0%A7%8B%E0%A6%B0%20%E0%A6%86%E0%A6%97%E0%A7%87%20%E0%A6%85%E0%A6%AC%E0%A6%B6%E0%A7%8D%E0%A6%AF%E0%A6%87%20dsebd.org-%E0%A6%8F%E0%A6%B0%20%E0%A6%B8%E0%A6%BE%E0%A6%A5%E0%A7%87%20%E0%A6%AE%E0%A6%BF%E0%A6%B2%E0%A6%BF%E0%A6%AF%E0%A6%BC%E0%A7%87%20%E0%A6%A6%E0%A7%87%E0%A6%96%E0%A7%81%E0%A6%A8%20%E0%A6%93%20%E0%A6%AA%E0%A7%8D%E0%A6%B0%E0%A6%AF%E0%A6%BC%E0%A7%8B%E0%A6%9C%E0%A6%A8%E0%A7%87%20%E0%A6%A0%E0%A6%BF%E0%A6%95%20%E0%A6%95%E0%A6%B0%E0%A7%81%E0%A6%A8%E0%A5%A4%3C%2Fdiv%3E%0A%0A%20%20%20%20%20%20%3Clabel%20style%3D%22display%3Ablock%3Bmargin-bottom%3A4px%3Bcolor%3A%237D8590%3B%22%3EAdmin%20Key%3C%2Flabel%3E%0A%20%20%20%20%20%20%3Cinput%20id%3D%22sp-key%22%20type%3D%22password%22%20style%3D%22width%3A100%25%3Bpadding%3A6px%3Bmargin-bottom%3A10px%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3B%22%20placeholder%3D%22Admin%20Key%20%E0%A6%A6%E0%A6%BF%E0%A6%A8%22%3E%0A%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22font-weight%3A600%3Bmargin-bottom%3A4px%3B%22%3EDSEX%20%E0%A6%87%E0%A6%A8%E0%A6%A1%E0%A7%87%E0%A6%95%E0%A7%8D%E0%A6%B8%3C%2Fdiv%3E%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22display%3Agrid%3Bgrid-template-columns%3A1fr%201fr%3Bgap%3A6px%3Bmargin-bottom%3A10px%3B%22%3E%0A%20%20%20%20%20%20%20%20%3Cinput%20id%3D%22sp-close%22%20placeholder%3D%22%E0%A6%95%E0%A7%8D%E0%A6%B2%E0%A7%8B%E0%A6%9C%E0%A6%BF%E0%A6%82%22%20value%3D%22%24%7BscanResult.dsexGuess%20%3F%20scanResult.dsexGuess.close_value%20%3A%20%22%22%7D%22%20style%3D%22padding%3A6px%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3B%22%3E%0A%20%20%20%20%20%20%20%20%3Cinput%20id%3D%22sp-change%22%20placeholder%3D%22%E0%A6%AA%E0%A6%B0%E0%A6%BF%E0%A6%AC%E0%A6%B0%E0%A7%8D%E0%A6%A4%E0%A6%A8%22%20value%3D%22%24%7BscanResult.dsexGuess%20%3F%20scanResult.dsexGuess.change_value%20%3A%20%22%22%7D%22%20style%3D%22padding%3A6px%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3B%22%3E%0A%20%20%20%20%20%20%20%20%3Cinput%20id%3D%22sp-changepct%22%20placeholder%3D%22%E0%A6%AA%E0%A6%B0%E0%A6%BF%E0%A6%AC%E0%A6%B0%E0%A7%8D%E0%A6%A4%E0%A6%A8%20%25%22%20value%3D%22%24%7BscanResult.dsexGuess%20%3F%20scanResult.dsexGuess.change_percent%20%3A%20%22%22%7D%22%20style%3D%22padding%3A6px%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3B%22%3E%0A%20%20%20%20%20%20%20%20%3Cinput%20id%3D%22sp-volume%22%20placeholder%3D%22%E0%A6%AD%E0%A6%B2%E0%A6%BF%E0%A6%89%E0%A6%AE%22%20style%3D%22padding%3A6px%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3B%22%3E%0A%20%20%20%20%20%20%3C%2Fdiv%3E%0A%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22font-weight%3A600%3Bmargin-bottom%3A4px%3B%22%3E%E0%A6%97%E0%A7%87%E0%A6%87%E0%A6%A8%E0%A6%BE%E0%A6%B0%E0%A7%8D%E0%A6%B8%20(%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%8D%E0%A6%AF%2C%20%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AA%E0%A6%BE%E0%A6%A6%E0%A6%A8%E0%A6%BE%E0%A6%AF%E0%A7%8B%E0%A6%97%E0%A7%8D%E0%A6%AF)%3C%2Fdiv%3E%0A%20%20%20%20%20%20%3Ctextarea%20id%3D%22sp-gainers%22%20rows%3D%224%22%20style%3D%22width%3A100%25%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3Bpadding%3A6px%3Bfont-family%3Amonospace%3Bfont-size%3A11px%3Bmargin-bottom%3A10px%3B%22%3E%24%7Bgainers.map((g)%20%3D%3E%20%60%24%7Bg.trading_code%7D%2C%24%7Bg.company_name%7D%2C%24%7Bg.ltp%7D%2C%24%7Bg.change_percent%7D%2C%24%7Bg.volume%7D%60).join(%22%5Cn%22)%7D%3C%2Ftextarea%3E%0A%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22font-weight%3A600%3Bmargin-bottom%3A4px%3B%22%3E%E0%A6%B2%E0%A7%81%E0%A6%9C%E0%A6%BE%E0%A6%B0%E0%A7%8D%E0%A6%B8%20(%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%8D%E0%A6%AF%2C%20%E0%A6%B8%E0%A6%AE%E0%A7%8D%E0%A6%AA%E0%A6%BE%E0%A6%A6%E0%A6%A8%E0%A6%BE%E0%A6%AF%E0%A7%8B%E0%A6%97%E0%A7%8D%E0%A6%AF)%3C%2Fdiv%3E%0A%20%20%20%20%20%20%3Ctextarea%20id%3D%22sp-losers%22%20rows%3D%224%22%20style%3D%22width%3A100%25%3Bbackground%3A%231C2028%3Bcolor%3A%23fff%3Bborder%3A1px%20solid%20%23333%3Bborder-radius%3A6px%3Bpadding%3A6px%3Bfont-family%3Amonospace%3Bfont-size%3A11px%3Bmargin-bottom%3A10px%3B%22%3E%24%7Blosers.map((g)%20%3D%3E%20%60%24%7Bg.trading_code%7D%2C%24%7Bg.company_name%7D%2C%24%7Bg.ltp%7D%2C%24%7Bg.change_percent%7D%2C%24%7Bg.volume%7D%60).join(%22%5Cn%22)%7D%3C%2Ftextarea%3E%0A%0A%20%20%20%20%20%20%3Cdiv%20style%3D%22display%3Aflex%3Bgap%3A8px%3B%22%3E%0A%20%20%20%20%20%20%20%20%3Cbutton%20id%3D%22sp-send%22%20style%3D%22flex%3A1%3Bbackground%3A%233EC98B%3Bcolor%3A%230C1210%3Bborder%3Anone%3Bborder-radius%3A8px%3Bpadding%3A10px%3Bfont-weight%3A700%3Bcursor%3Apointer%3B%22%3E%E2%9C%85%20%E0%A6%AF%E0%A6%BE%E0%A6%9A%E0%A6%BE%E0%A6%87%20%E0%A6%95%E0%A6%B0%E0%A7%87%20%E0%A6%AA%E0%A6%BE%E0%A6%A0%E0%A6%BE%E0%A6%A8%3C%2Fbutton%3E%0A%20%20%20%20%20%20%20%20%3Cbutton%20id%3D%22sp-close-panel%22%20style%3D%22background%3A%233A1E1E%3Bcolor%3A%23F0A18F%3Bborder%3Anone%3Bborder-radius%3A8px%3Bpadding%3A10px%3Bcursor%3Apointer%3B%22%3E%E2%9C%95%3C%2Fbutton%3E%0A%20%20%20%20%20%20%3C%2Fdiv%3E%0A%20%20%20%20%20%20%3Cdiv%20id%3D%22sp-msg%22%20style%3D%22margin-top%3A8px%3Bfont-size%3A12px%3B%22%3E%3C%2Fdiv%3E%0A%20%20%20%20%60%3B%0A%0A%20%20%20%20document.body.appendChild(panel)%3B%0A%0A%20%20%20%20document.getElementById(%22sp-close-panel%22).onclick%20%3D%20()%20%3D%3E%20panel.remove()%3B%0A%20%20%20%20document.getElementById(%22sp-send%22).onclick%20%3D%20()%20%3D%3E%20sendData(panel)%3B%0A%0A%20%20%20%20%2F%2F%20%E0%A6%86%E0%A6%97%E0%A7%87%20%E0%A6%B8%E0%A7%87%E0%A6%AD%20%E0%A6%95%E0%A6%B0%E0%A6%BE%20Admin%20Key%20%E0%A6%A5%E0%A6%BE%E0%A6%95%E0%A6%B2%E0%A7%87%20%E0%A6%AD%E0%A6%B0%E0%A7%87%20%E0%A6%A6%E0%A6%BE%E0%A6%93%20(localStorage%20%E2%80%94%20%E0%A6%8F%E0%A6%87%20%E0%A6%AC%E0%A7%8D%E0%A6%B0%E0%A6%BE%E0%A6%89%E0%A6%9C%E0%A6%BE%E0%A6%B0%E0%A7%87%E0%A6%87%20%E0%A6%A5%E0%A6%BE%E0%A6%95%E0%A7%87)%0A%20%20%20%20const%20savedKey%20%3D%20localStorage.getItem(%22sp_admin_key%22)%3B%0A%20%20%20%20if%20(savedKey)%20document.getElementById(%22sp-key%22).value%20%3D%20savedKey%3B%0A%20%20%7D%0A%0A%20%20function%20parseLines(text)%20%7B%0A%20%20%20%20return%20text%0A%20%20%20%20%20%20.split(%22%5Cn%22)%0A%20%20%20%20%20%20.map((l)%20%3D%3E%20l.trim())%0A%20%20%20%20%20%20.filter(Boolean)%0A%20%20%20%20%20%20.map((line)%20%3D%3E%20%7B%0A%20%20%20%20%20%20%20%20const%20p%20%3D%20line.split(%22%2C%22).map((x)%20%3D%3E%20x.trim())%3B%0A%20%20%20%20%20%20%20%20return%20%7B%0A%20%20%20%20%20%20%20%20%20%20trading_code%3A%20p%5B0%5D%20%7C%7C%20%22%22%2C%0A%20%20%20%20%20%20%20%20%20%20company_name%3A%20p%5B1%5D%20%7C%7C%20p%5B0%5D%20%7C%7C%20%22%22%2C%0A%20%20%20%20%20%20%20%20%20%20ltp%3A%20parseFloat(p%5B2%5D)%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20%20%20change_percent%3A%20parseFloat(p%5B3%5D)%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20%20%20volume%3A%20parseInt(p%5B4%5D%2C%2010)%20%7C%7C%200%2C%0A%20%20%20%20%20%20%20%20%7D%3B%0A%20%20%20%20%20%20%7D)%0A%20%20%20%20%20%20.filter((r)%20%3D%3E%20r.trading_code)%3B%0A%20%20%7D%0A%0A%20%20async%20function%20sendData(panel)%20%7B%0A%20%20%20%20const%20key%20%3D%20document.getElementById(%22sp-key%22).value.trim()%3B%0A%20%20%20%20const%20msg%20%3D%20document.getElementById(%22sp-msg%22)%3B%0A%20%20%20%20if%20(!key)%20%7B%0A%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9A%A0%EF%B8%8F%20Admin%20Key%20%E0%A6%A6%E0%A6%BF%E0%A6%A8%22%3B%0A%20%20%20%20%20%20msg.style.color%20%3D%20%22%23F0A18F%22%3B%0A%20%20%20%20%20%20return%3B%0A%20%20%20%20%7D%0A%20%20%20%20localStorage.setItem(%22sp_admin_key%22%2C%20key)%3B%0A%0A%20%20%20%20const%20close_value%20%3D%20parseFloat(document.getElementById(%22sp-close%22).value)%3B%0A%20%20%20%20const%20change_value%20%3D%20parseFloat(document.getElementById(%22sp-change%22).value)%3B%0A%20%20%20%20const%20change_percent%20%3D%20parseFloat(document.getElementById(%22sp-changepct%22).value)%3B%0A%20%20%20%20const%20total_volume%20%3D%20parseInt(document.getElementById(%22sp-volume%22).value%2C%2010)%20%7C%7C%200%3B%0A%0A%20%20%20%20const%20body%20%3D%20%7B%7D%3B%0A%20%20%20%20if%20(!isNaN(close_value)%20%26%26%20!isNaN(change_value)%20%26%26%20!isNaN(change_percent))%20%7B%0A%20%20%20%20%20%20body.index%20%3D%20%7B%20close_value%2C%20change_value%2C%20change_percent%2C%20total_volume%2C%20total_turnover%3A%200%20%7D%3B%0A%20%20%20%20%7D%0A%20%20%20%20const%20gainers%20%3D%20parseLines(document.getElementById(%22sp-gainers%22).value)%3B%0A%20%20%20%20const%20losers%20%3D%20parseLines(document.getElementById(%22sp-losers%22).value)%3B%0A%20%20%20%20if%20(gainers.length)%20body.gainers%20%3D%20gainers%3B%0A%20%20%20%20if%20(losers.length)%20body.losers%20%3D%20losers%3B%0A%0A%20%20%20%20if%20(!body.index%20%26%26%20!gainers.length%20%26%26%20!losers.length)%20%7B%0A%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9A%A0%EF%B8%8F%20%E0%A6%85%E0%A6%A8%E0%A7%8D%E0%A6%A4%E0%A6%A4%20%E0%A6%8F%E0%A6%95%E0%A6%9F%E0%A6%BE%20%E0%A6%A4%E0%A6%A5%E0%A7%8D%E0%A6%AF%20(%E0%A6%87%E0%A6%A8%E0%A6%A1%E0%A7%87%E0%A6%95%E0%A7%8D%E0%A6%B8%20%E0%A6%AC%E0%A6%BE%20%E0%A6%97%E0%A7%87%E0%A6%87%E0%A6%A8%E0%A6%BE%E0%A6%B0%E0%A7%8D%E0%A6%B8%2F%E0%A6%B2%E0%A7%81%E0%A6%9C%E0%A6%BE%E0%A6%B0%E0%A7%8D%E0%A6%B8)%20%E0%A6%A6%E0%A6%BF%E0%A6%A8%22%3B%0A%20%20%20%20%20%20msg.style.color%20%3D%20%22%23F0A18F%22%3B%0A%20%20%20%20%20%20return%3B%0A%20%20%20%20%7D%0A%0A%20%20%20%20msg.textContent%20%3D%20%22%E0%A6%AA%E0%A6%BE%E0%A6%A0%E0%A6%BE%E0%A6%A8%E0%A7%8B%20%E0%A6%B9%E0%A6%9A%E0%A7%8D%E0%A6%9B%E0%A7%87...%22%3B%0A%20%20%20%20msg.style.color%20%3D%20%22%239199A3%22%3B%0A%20%20%20%20try%20%7B%0A%20%20%20%20%20%20const%20res%20%3D%20await%20fetch(API_BASE%20%2B%20%22%2Fv1%2Fadmin%2Fmanual-entry%22%2C%20%7B%0A%20%20%20%20%20%20%20%20method%3A%20%22POST%22%2C%0A%20%20%20%20%20%20%20%20headers%3A%20%7B%20%22Content-Type%22%3A%20%22application%2Fjson%22%2C%20%22X-Admin-Key%22%3A%20key%20%7D%2C%0A%20%20%20%20%20%20%20%20body%3A%20JSON.stringify(body)%2C%0A%20%20%20%20%20%20%7D)%3B%0A%20%20%20%20%20%20if%20(res.status%20%3D%3D%3D%20401)%20%7B%0A%20%20%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9D%8C%20%E0%A6%AD%E0%A7%81%E0%A6%B2%20Admin%20Key%22%3B%0A%20%20%20%20%20%20%20%20msg.style.color%20%3D%20%22%23F0A18F%22%3B%0A%20%20%20%20%20%20%20%20return%3B%0A%20%20%20%20%20%20%7D%0A%20%20%20%20%20%20if%20(!res.ok)%20%7B%0A%20%20%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9D%8C%20%E0%A6%AA%E0%A6%BE%E0%A6%A0%E0%A6%BE%E0%A6%A8%E0%A7%8B%20%E0%A6%AF%E0%A6%BE%E0%A6%AF%E0%A6%BC%E0%A6%A8%E0%A6%BF%20(%E0%A6%B8%E0%A7%8D%E0%A6%9F%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%9F%E0%A6%BE%E0%A6%B8%20%22%20%2B%20res.status%20%2B%20%22)%22%3B%0A%20%20%20%20%20%20%20%20msg.style.color%20%3D%20%22%23F0A18F%22%3B%0A%20%20%20%20%20%20%20%20return%3B%0A%20%20%20%20%20%20%7D%0A%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9C%85%20%E0%A6%B8%E0%A6%AB%E0%A6%B2%E0%A6%AD%E0%A6%BE%E0%A6%AC%E0%A7%87%20%E0%A6%AA%E0%A6%BE%E0%A6%A0%E0%A6%BE%E0%A6%A8%E0%A7%8B%20%E0%A6%B9%E0%A6%AF%E0%A6%BC%E0%A7%87%E0%A6%9B%E0%A7%87!%20%E0%A6%85%E0%A7%8D%E0%A6%AF%E0%A6%BE%E0%A6%AA%E0%A7%87%20%E0%A6%97%E0%A6%BF%E0%A6%AF%E0%A6%BC%E0%A7%87%20%E0%A6%B0%E0%A6%BF%E0%A6%AB%E0%A7%8D%E0%A6%B0%E0%A7%87%E0%A6%B6%20%E0%A6%95%E0%A6%B0%E0%A7%81%E0%A6%A8%E0%A5%A4%22%3B%0A%20%20%20%20%20%20msg.style.color%20%3D%20%22%233EC98B%22%3B%0A%20%20%20%20%7D%20catch%20(e)%20%7B%0A%20%20%20%20%20%20msg.textContent%20%3D%20%22%E2%9D%8C%20%E0%A6%B8%E0%A6%BE%E0%A6%B0%E0%A7%8D%E0%A6%AD%E0%A6%BE%E0%A6%B0%20%E0%A6%B8%E0%A6%82%E0%A6%AF%E0%A7%8B%E0%A6%97%E0%A7%87%20%E0%A6%B8%E0%A6%AE%E0%A6%B8%E0%A7%8D%E0%A6%AF%E0%A6%BE%3A%20%22%20%2B%20e.message%3B%0A%20%20%20%20%20%20msg.style.color%20%3D%20%22%23F0A18F%22%3B%0A%20%20%20%20%7D%0A%20%20%7D%0A%0A%20%20%2F%2F%20----------%20%E0%A6%9A%E0%A6%BE%E0%A6%B2%E0%A7%81%20%E0%A6%95%E0%A6%B0%E0%A6%BE%20----------%0A%20%20const%20result%20%3D%20scanPage()%3B%0A%20%20buildPanel(result)%3B%0A%7D)()%3B%0A">📊 StockPilot ডেটা তুলুন</a></div>
  </div>

  <div class="step">
    <div class="step-num">ধাপ ২ (মোবাইল Chrome-এ ইনস্টল করার নিয়ম)</div>
    ১. Chrome-এর ⋮ মেনু → <b>Bookmarks</b> → <b>Add a new bookmark</b><br>
    ২. নাম দিন: <code>StockPilot ডেটা তুলুন</code><br>
    ৩. URL ঘরে উপরের সবুজ বাটনের লিংকটা কপি করে বসান (বাটনে চেপে ধরে "Copy link")<br>
    ৪. সেভ করুন
  </div>

  <div class="step">
    <div class="step-num">ধাপ ৩ — প্রতিদিন ব্যবহার</div>
    ১. Chrome-এ dsebd.org খুলুন (স্বাভাবিকভাবে, মানুষ হিসেবে)<br>
    ২. ঠিকানা বারে গিয়ে বুকমার্কের নাম টাইপ করুন (<code>StockPilot ডেটা তুলুন</code>) এবং সিলেক্ট করুন — এতেই এটা চালু হয়ে যাবে<br>
    ৩. একটা প্যানেল খুলবে, সংখ্যা যাচাই/সম্পাদনা করুন<br>
    ৪. Admin Key দিয়ে "✅ যাচাই করে পাঠান" চাপুন
  </div>

  <div style="text-align:center; margin-top:24px;">
    <a href="/app" style="color:#8FB8FF;">← অ্যাপে ফিরে যান</a>
  </div>
</div>
</body>
</html>"""


@app.get("/bookmarklet", response_class=HTMLResponse)
def bookmarklet_page():
    return BOOKMARKLET_PAGE


# ---------------------------------------------------------------
# লাইভ ড্যাশবোর্ড (same-origin — কোনো CORS/sandbox সমস্যা ছাড়াই ব্রাউজারে সরাসরি খোলা যায়)
# ---------------------------------------------------------------
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>StockPilot BD AI — Market</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+Bengali:wght@600;700&family=Hind+Siliguri:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
  * { box-sizing: border-box; font-family: 'Hind Siliguri','Inter',system-ui,sans-serif; }
  body { margin:0; min-height:100vh; background:#12151A; color:#EDEFF2; padding:0 0 60px; }
  .wrap { max-width:880px; margin:0 auto; padding:20px 20px 0; }
  .demo-banner { background:#4A1414; color:#FFB4A8; text-align:center; padding:10px 16px; font-size:13px; font-weight:600; border-bottom:2px solid #F0654A; }
  .demo-banner.verified { background:#153D2B; color:#8CE6B8; border-bottom-color:#3EC98B; }
  .top { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:16px; }
  .eyebrow { font-size:12px; color:#7D8590; letter-spacing:.1em; font-family:'IBM Plex Mono',monospace; }
  h1 { font-size:26px; font-weight:700; font-family:'Noto Serif Bengali',serif; margin:2px 0 0; }
  .refresh { background:#1C2028; border:1px solid #262B33; border-radius:8px; padding:8px 14px; color:#C4C9D1; font-size:12.5px; cursor:pointer; }
  .banner { padding:10px 14px; border-radius:8px; font-size:12.5px; margin-bottom:18px; }
  .banner.live { background:#153D2B; color:#3EC98B; }
  .banner.loading { background:#1C2028; color:#9199A3; }
  .banner.error { background:#3A1E1E; color:#F0A18F; display:flex; justify-content:space-between; align-items:center; gap:10px; }
  .retry { background:#F0654A; color:#fff; border:none; border-radius:6px; padding:6px 12px; font-size:12px; font-weight:600; cursor:pointer; }
  .hero { background:linear-gradient(135deg,#171B22 0%,#1B2129 100%); border:1px solid #262B33; border-radius:16px; padding:24px 26px; margin-bottom:28px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:16px; min-height:120px; }
  .hero-name { font-size:13px; color:#7D8590; margin-bottom:4px; }
  .hero-value { font-size:40px; font-weight:700; font-family:'IBM Plex Mono',monospace; letter-spacing:-.02em; }
  .up { color:#3EC98B; font-weight:600; }
  .down { color:#F0654A; font-weight:600; }
  .stat { color:#7D8590; font-size:13px; }
  .stat b { display:block; color:#EDEFF2; font-family:'IBM Plex Mono',monospace; font-size:16px; margin-top:2px; font-weight:500; }
  .section-eyebrow { font-size:11px; letter-spacing:.14em; color:#7D8590; text-transform:uppercase; font-family:'IBM Plex Mono',monospace; }
  .section-title { font-size:20px; font-weight:700; color:#EDEFF2; font-family:'Noto Serif Bengali',serif; margin-bottom:14px; }
  .heatgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:10px; margin-bottom:34px; min-height:90px; }
  .heatcell { border:1px solid rgba(255,255,255,.06); border-radius:10px; padding:14px 12px; }
  .heatcell .name { font-size:12.5px; font-weight:600; margin-bottom:8px; color:#F2F4F7; }
  .heatcell .pct { font-family:'IBM Plex Mono',monospace; font-size:15px; font-weight:600; }
  .heatcell .meta { font-size:10.5px; color:#D8DBE0; margin-top:4px; }
  .tabs { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
  .tab-btn { background:#1C2028; color:#C4C9D1; border:1px solid #262B33; border-radius:8px; padding:8px 14px; font-size:13px; font-weight:600; cursor:pointer; }
  .tab-btn.active { background:#3EC98B; color:#0C1210; border-color:#3EC98B; }
  .search { margin-left:auto; background:#1C2028; border:1px solid #262B33; border-radius:8px; padding:8px 12px; color:#EDEFF2; font-size:13px; outline:none; width:160px; }
  table { width:100%; border-collapse:collapse; border:1px solid #262B33; border-radius:12px; overflow:hidden; }
  thead td { padding:10px 16px; font-size:11px; color:#7D8590; letter-spacing:.05em; border-bottom:1px solid #262B33; background:#171B22; }
  tbody td { padding:12px 16px; font-size:13.5px; border-bottom:1px solid #1D222A; }
  tbody tr:hover { background:#1C2028; }
  .code { font-family:'IBM Plex Mono',monospace; font-weight:600; color:#8FB8FF; }
  .num { font-family:'IBM Plex Mono',monospace; text-align:right; }
  .muted { color:#9199A3; }
  .right { text-align:right; }
  .footer { margin-top:30px; font-size:11.5px; color:#5B6270; text-align:center; }
  .empty { padding:24px; text-align:center; color:#7D8590; font-size:13px; }

  /* Nav */
  .nav { display:flex; gap:6px; margin-bottom:24px; border-bottom:1px solid #262B33; }
  .nav-btn { background:none; border:none; color:#7D8590; font-size:14px; font-weight:600; padding:10px 16px; cursor:pointer; border-bottom:2px solid transparent; font-family:'Hind Siliguri',sans-serif; }
  .nav-btn.active { color:#3EC98B; border-bottom-color:#3EC98B; }
  .view { display:none; }
  .view.active { display:block; }

  /* Company / Portfolio forms */
  .card { background:#171B22; border:1px solid #262B33; border-radius:14px; padding:20px 22px; margin-bottom:22px; }
  .search-row { display:flex; gap:8px; margin-bottom:20px; }
  .search-row input { flex:1; background:#1C2028; border:1px solid #262B33; border-radius:8px; padding:10px 14px; color:#EDEFF2; font-size:14px; outline:none; }
  .btn-primary { background:#3EC98B; color:#0C1210; border:none; border-radius:8px; padding:10px 18px; font-size:13.5px; font-weight:700; cursor:pointer; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .grid3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }
  .metric-box { background:#1C2028; border:1px solid #262B33; border-radius:10px; padding:12px 14px; }
  .metric-box .label { font-size:11px; color:#7D8590; margin-bottom:4px; }
  .metric-box .value { font-family:'IBM Plex Mono',monospace; font-size:16px; font-weight:600; }
  .form-row { display:grid; grid-template-columns:1fr 1fr 1fr auto; gap:8px; margin-bottom:16px; align-items:end; }
  .form-row label { font-size:11px; color:#7D8590; display:block; margin-bottom:4px; }
  .form-row input { width:100%; background:#1C2028; border:1px solid #262B33; border-radius:8px; padding:9px 10px; color:#EDEFF2; font-size:13px; outline:none; }
  .del-btn { background:#3A1E1E; color:#F0A18F; border:1px solid #5A2A2A; border-radius:6px; padding:6px 10px; font-size:11.5px; cursor:pointer; }
  .total-box { display:flex; gap:24px; margin-top:16px; flex-wrap:wrap; }
</style>
</head>
<body>
<div class="demo-banner" id="topDemoBanner">⚠️ এটি DEMO/MOCK ডেটা — প্রকৃত DSE ডেটা নয়। বিনিয়োগ সিদ্ধান্তে ব্যবহার করবেন না।</div>
<div class="wrap">
  <div class="top">
    <div>
      <div class="eyebrow">STOCKPILOT BD AI</div>
      <h1 id="pageTitle">বাজার সংক্ষিপ্ত বিবরণ</h1>
    </div>
    <button class="refresh" onclick="refreshCurrent()">↻ রিফ্রেশ</button>
  </div>

  <div class="nav">
    <button class="nav-btn active" data-view="market" onclick="setView('market')">মার্কেট</button>
    <button class="nav-btn" data-view="company" onclick="setView('company')">কোম্পানি ডিটেইল</button>
    <button class="nav-btn" data-view="portfolio" onclick="setView('portfolio')">পোর্টফোলিও</button>
    <button class="nav-btn" data-view="admin" onclick="setView('admin')">⚙️ Admin</button>
  </div>

  <!-- ============ MARKET VIEW ============ -->
  <div class="view active" id="view-market">
    <div id="banner" class="banner loading">⏳ সার্ভার থেকে লাইভ ডেটা লোড হচ্ছে...</div>

    <div class="hero" id="hero">
      <div style="color:#5B6270; font-size:13px;">ইনডেক্স ডেটা লোড হচ্ছে...</div>
    </div>

    <div class="section-eyebrow">Sector Heatmap</div>
    <div class="section-title">সেক্টর ভিত্তিক পারফরম্যান্স</div>
    <div class="heatgrid" id="heatgrid"></div>

    <div class="section-eyebrow">Market Movers</div>
    <div class="section-title">শীর্ষ পরিবর্তনসমূহ</div>
    <div class="tabs">
      <button class="tab-btn active" data-tab="gainers" onclick="setTab('gainers')">টপ গেইনার্স</button>
      <button class="tab-btn" data-tab="losers" onclick="setTab('losers')">টপ লুজার্স</button>
      <button class="tab-btn" data-tab="volume" onclick="setTab('volume')">ভলিউম লিডার্স</button>
      <input class="search" id="search" placeholder="কোম্পানি খুঁজুন..." oninput="renderTable()">
    </div>
    <table>
      <thead><tr><td>কোড</td><td>কোম্পানি</td><td class="right">এলটিপি</td><td class="right">পরিবর্তন</td><td class="right">ভলিউম</td></tr></thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>

  <!-- ============ COMPANY DETAIL VIEW ============ -->
  <div class="view" id="view-company">
    <div class="search-row">
      <input id="companyCode" placeholder="ট্রেডিং কোড লিখুন (যেমন GP, BEXIMCO, SQUARPHARMA)" onkeydown="if(event.key==='Enter')loadCompany()">
      <button class="btn-primary" onclick="loadCompany()">খুঁজুন</button>
    </div>
    <div id="companyResult"><div class="empty">একটা ট্রেডিং কোড লিখে খুঁজুন।</div></div>
  </div>

  <!-- ============ PORTFOLIO VIEW ============ -->
  <div class="view" id="view-portfolio">
    <div class="card">
      <div class="section-title" style="margin-bottom:12px;">নতুন হোল্ডিং যোগ করুন (অনুশীলন)</div>
      <div class="form-row">
        <div><label>ট্রেডিং কোড</label><input id="pfCode" placeholder="GP"></div>
        <div><label>পরিমাণ</label><input id="pfQty" type="number" placeholder="100"></div>
        <div><label>গড় ক্রয়মূল্য (৳)</label><input id="pfPrice" type="number" placeholder="280"></div>
        <button class="btn-primary" onclick="addHolding()">যোগ করুন</button>
      </div>
    </div>
    <div id="portfolioResult"></div>
  </div>

  <!-- ============ ADMIN VIEW (Manual Data Entry) ============ -->
  <div class="view" id="view-admin">
    <div class="card">
      <div class="section-title" style="font-size:16px; margin-bottom:6px;">আজকের যাচাইকৃত ডেটা যোগ করুন</div>
      <div class="muted" style="font-size:12.5px; margin-bottom:16px;">dsebd.org-এ ব্রাউজার দিয়ে গিয়ে (মানুষ হিসেবে) দেখা সংখ্যা এখানে লিখুন। সেভ করলে এটাই আজকের "যাচাইকৃত বাস্তব ডেটা" হিসেবে দেখানো হবে।</div>
      <div class="form-row" style="grid-template-columns:1fr;">
        <div><label>Admin Key</label><input id="adminKey" type="password" placeholder="Railway-তে সেট করা ADMIN_KEY দিন"></div>
      </div>

      <div class="section-title" style="font-size:15px; margin:18px 0 10px;">DSEX ইনডেক্স</div>
      <div class="grid3" style="margin-bottom:16px;">
        <div><label style="font-size:11px; color:#7D8590;">ক্লোজিং ভ্যালু</label><input id="admClose" type="number" step="0.01" placeholder="5300.50"></div>
        <div><label style="font-size:11px; color:#7D8590;">পরিবর্তন (পয়েন্ট)</label><input id="admChange" type="number" step="0.01" placeholder="20.10"></div>
        <div><label style="font-size:11px; color:#7D8590;">পরিবর্তন (%)</label><input id="admChangePct" type="number" step="0.01" placeholder="0.38"></div>
        <div><label style="font-size:11px; color:#7D8590;">মোট ভলিউম</label><input id="admVolume" type="number" placeholder="123456789"></div>
        <div><label style="font-size:11px; color:#7D8590;">মোট টার্নওভার (৳)</label><input id="admTurnover" type="number" placeholder="9999999999"></div>
      </div>

      <div class="section-title" style="font-size:15px; margin:18px 0 6px;">টপ গেইনার্স / লুজার্স (ঐচ্ছিক)</div>
      <div class="muted" style="font-size:11.5px; margin-bottom:8px;">প্রতি লাইনে একটা করে, কমা দিয়ে ভাগ করা: কোড,কোম্পানির নাম,LTP,পরিবর্তন%,ভলিউম</div>
      <div class="grid2">
        <div>
          <label style="font-size:11px; color:#7D8590;">গেইনার্স</label>
          <textarea id="admGainers" rows="4" style="width:100%; background:#1C2028; border:1px solid #262B33; border-radius:8px; padding:8px; color:#EDEFF2; font-size:12px; font-family:'IBM Plex Mono',monospace;" placeholder="GP,Grameenphone,310.5,5.2,10000"></textarea>
        </div>
        <div>
          <label style="font-size:11px; color:#7D8590;">লুজার্স</label>
          <textarea id="admLosers" rows="4" style="width:100%; background:#1C2028; border:1px solid #262B33; border-radius:8px; padding:8px; color:#EDEFF2; font-size:12px; font-family:'IBM Plex Mono',monospace;" placeholder="BEXIMCO,Beximco Limited,80.1,-3.1,5000"></textarea>
        </div>
      </div>

      <div style="margin-top:16px; display:flex; gap:10px;">
        <button class="btn-primary" onclick="submitManualEntry()">সেভ করুন</button>
        <button class="del-btn" onclick="clearManualEntry()">আজকের এন্ট্রি মুছুন (ডেমোতে ফিরুন)</button>
      </div>
      <div id="adminMsg" style="margin-top:14px; font-size:13px;"></div>
    </div>
  </div>

  <div class="footer">সংযুক্ত: এই সার্ভার (same-origin) — লাইভ ডেটা (ডেমো)। <a href="/bookmarklet" style="color:#8FB8FF;">📌 dsebd.org ডেটা এক্সট্র্যাক্টর বুকমার্কলেট ইনস্টল করুন</a></div>
</div>

<script>
let DATA = { gainers: [], losers: [], volume: [] };
let TAB = 'gainers';
let VIEW = 'market';

function fmt(n) { return new Intl.NumberFormat('en-US').format(n); }
function fmtBDT(n) {
  if (n >= 1e9) return (n/1e9).toFixed(2) + ' বিলিয়ন';
  if (n >= 1e7) return (n/1e7).toFixed(2) + ' কোটি';
  return fmt(n);
}
function pillHTML(v) {
  const cls = v >= 0 ? 'up' : 'down';
  const arrow = v >= 0 ? '▲' : '▼';
  return `<span class="${cls}">${arrow} ${Math.abs(v).toFixed(2)}%</span>`;
}

const TITLES = { market: 'বাজার সংক্ষিপ্ত বিবরণ', company: 'কোম্পানি ডিটেইল', portfolio: 'পোর্টফোলিও (ডেমো)', admin: 'Admin — ম্যানুয়াল ডেটা এন্ট্রি' };

function setView(v) {
  VIEW = v;
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === 'view-' + v));
  document.getElementById('pageTitle').textContent = TITLES[v];
  if (v === 'portfolio') loadPortfolio();
}

function refreshCurrent() {
  if (VIEW === 'market') loadAll();
  else if (VIEW === 'company') { if (document.getElementById('companyCode').value.trim()) loadCompany(); }
  else if (VIEW === 'portfolio') loadPortfolio();
}

function updateTopBanner(isDemo) {
  const bar = document.getElementById('topDemoBanner');
  if (!bar) return;
  if (isDemo) {
    bar.className = 'demo-banner';
    bar.textContent = '⚠️ এটি DEMO/MOCK ডেটা — প্রকৃত DSE ডেটা নয়। বিনিয়োগ সিদ্ধান্তে ব্যবহার করবেন না।';
  } else {
    bar.className = 'demo-banner verified';
    bar.textContent = '✅ আজকের ডেটা dsebd.org থেকে ম্যানুয়ালি যাচাই করা — তবুও বড় সিদ্ধান্তের আগে সরাসরি ক্রস-চেক করুন।';
  }
}

/* ---------------- MARKET ---------------- */
async function loadAll() {
  const banner = document.getElementById('banner');
  banner.className = 'banner loading';
  banner.innerHTML = '⏳ সার্ভার থেকে লাইভ ডেটা লোড হচ্ছে...';
  try {
    const [idxRes, gainRes, loseRes, volRes, secRes] = await Promise.all([
      fetch('/v1/market/index/DSEX'),
      fetch('/v1/market/gainers?limit=6'),
      fetch('/v1/market/losers?limit=6'),
      fetch('/v1/market/volume-leaders?limit=6'),
      fetch('/v1/market/sector-heatmap'),
    ]);
    if (!idxRes.ok || !gainRes.ok || !loseRes.ok || !volRes.ok || !secRes.ok) throw new Error('bad response');
    const [idx, gain, lose, vol, sec] = await Promise.all([idxRes.json(), gainRes.json(), loseRes.json(), volRes.json(), secRes.json()]);

    renderHero(idx);
    renderHeat(sec.sectors || []);
    DATA.gainers = gain.data || [];
    DATA.losers = lose.data || [];
    DATA.volume = vol.data || [];
    renderTable();

    updateTopBanner(idx.is_demo_data);
    if (idx.is_demo_data) {
      banner.className = 'banner live';
      banner.innerHTML = '● লাইভ (ডেমো ডেটা) — সার্ভার থেকে সরাসরি আসছে';
    } else {
      banner.className = 'banner live';
      banner.innerHTML = '✅ লাইভ (যাচাইকৃত বাস্তব ডেটা) — আজকের জন্য ম্যানুয়ালি যাচাই করা';
    }
  } catch (e) {
    banner.className = 'banner error';
    banner.innerHTML = '⚠️ সার্ভারের সাথে সংযোগ করা যায়নি। একটু পর আবার চেষ্টা করুন। <button class="retry" onclick="loadAll()">আবার চেষ্টা করুন</button>';
  }
}

function renderHero(idx) {
  document.getElementById('hero').innerHTML = `
    <div>
      <div class="hero-name">${idx.index_name}</div>
      <div class="hero-value">${fmt(idx.close_value)}</div>
      <div style="margin-top:6px; font-size:15px;">${pillHTML(idx.change_percent)} <span style="color:#7D8590; margin-left:6px;">(${idx.change_value > 0 ? '+' : ''}${idx.change_value})</span></div>
    </div>
    <div style="display:flex; gap:28px;">
      <div class="stat">মোট ভলিউম<b>${fmt(idx.total_volume)}</b></div>
      <div class="stat">মোট টার্নওভার<b>৳ ${fmtBDT(idx.total_turnover)}</b></div>
    </div>
  `;
}

function renderHeat(sectors) {
  const grid = document.getElementById('heatgrid');
  grid.innerHTML = sectors.map(s => {
    const clamp = Math.max(-8, Math.min(8, s.avg_change_pct));
    const color = clamp >= 0
      ? `rgba(62,201,139,${0.15 + (clamp/8)*0.65})`
      : `rgba(240,101,74,${0.15 + (Math.abs(clamp)/8)*0.65})`;
    return `<div class="heatcell" style="background:${color}">
      <div class="name">${s.sector_name}</div>
      <div class="pct">${s.avg_change_pct >= 0 ? '+' : ''}${s.avg_change_pct}%</div>
      <div class="meta">${s.advancers} বৃদ্ধি · ${s.decliners} পতন</div>
    </div>`;
  }).join('');
}

function setTab(t) {
  TAB = t;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
  renderTable();
}

function renderTable() {
  const q = document.getElementById('search').value.trim().toLowerCase();
  let rows = DATA[TAB] || [];
  if (q) rows = rows.filter(r => r.trading_code.toLowerCase().includes(q) || r.company_name.toLowerCase().includes(q));
  const tbody = document.getElementById('tbody');
  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty">কোনো ফলাফল পাওয়া যায়নি।</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td class="code">${r.trading_code}</td>
      <td>${r.company_name}</td>
      <td class="num">৳${r.ltp}</td>
      <td class="right">${pillHTML(r.change_percent)}</td>
      <td class="num muted">${fmt(r.volume)}</td>
    </tr>
  `).join('');
}

/* ---------------- COMPANY DETAIL ---------------- */
async function loadCompany() {
  const code = document.getElementById('companyCode').value.trim().toUpperCase();
  const result = document.getElementById('companyResult');
  if (!code) return;
  result.innerHTML = '<div class="empty">লোড হচ্ছে...</div>';
  try {
    const [ovRes, techRes, finRes] = await Promise.all([
      fetch(`/v1/company/${code}/overview`),
      fetch(`/v1/company/${code}/technical`),
      fetch(`/v1/company/${code}/financials`),
    ]);
    if (!ovRes.ok) { result.innerHTML = '<div class="empty">কোম্পানির তথ্য পাওয়া যায়নি। কোড সঠিক কিনা দেখুন।</div>'; return; }
    const ov = await ovRes.json();
    const tech = await techRes.json();
    const fin = await finRes.json();

    result.innerHTML = `
      <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:16px;">
          <div>
            <div class="code" style="font-size:18px;">${ov.trading_code}</div>
            <div class="muted" style="font-size:13px;">${ov.company_name} · ${ov.sector}</div>
          </div>
          <div style="text-align:right;">
            <div class="hero-value" style="font-size:26px;">৳${ov.ltp}</div>
            ${pillHTML(ov.change_percent)}
          </div>
        </div>
        <div class="grid3" style="margin-bottom:10px;">
          <div class="metric-box"><div class="label">EPS (TTM)</div><div class="value">৳${ov.financials.eps_ttm}</div></div>
          <div class="metric-box"><div class="label">P/E Ratio</div><div class="value">${ov.financials.pe_ratio}</div></div>
          <div class="metric-box"><div class="label">NAV/Share</div><div class="value">৳${ov.financials.nav_per_share}</div></div>
          <div class="metric-box"><div class="label">ডিভিডেন্ড ইল্ড</div><div class="value">${ov.financials.dividend_yield_pct}%</div></div>
          <div class="metric-box"><div class="label">৫২ সপ্তাহ উচ্চ</div><div class="value">৳${ov.financials.year_high}</div></div>
          <div class="metric-box"><div class="label">৫২ সপ্তাহ নিম্ন</div><div class="value">৳${ov.financials.year_low}</div></div>
        </div>
      </div>

      <div class="card">
        <div class="section-title" style="font-size:16px; margin-bottom:12px;">শেয়ারহোল্ডিং প্যাটার্ন</div>
        <div class="grid3">
          <div class="metric-box"><div class="label">স্পন্সর/ডিরেক্টর</div><div class="value">${ov.shareholding.sponsor_director_pct}%</div></div>
          <div class="metric-box"><div class="label">প্রাতিষ্ঠানিক</div><div class="value">${ov.shareholding.institute_pct}%</div></div>
          <div class="metric-box"><div class="label">সাধারণ জনগণ</div><div class="value">${ov.shareholding.public_pct}%</div></div>
          <div class="metric-box"><div class="label">বৈদেশিক</div><div class="value">${ov.shareholding.foreign_pct}%</div></div>
          <div class="metric-box"><div class="label">সরকার</div><div class="value">${ov.shareholding.govt_pct}%</div></div>
        </div>
      </div>

      <div class="card">
        <div class="section-title" style="font-size:16px; margin-bottom:12px;">টেকনিক্যাল সংকেত</div>
        <div class="grid3">
          <div class="metric-box"><div class="label">SMA 20</div><div class="value">৳${tech.sma_20}</div></div>
          <div class="metric-box"><div class="label">SMA 50</div><div class="value">৳${tech.sma_50}</div></div>
          <div class="metric-box"><div class="label">RSI (14)</div><div class="value">${tech.rsi_14}</div></div>
          <div class="metric-box"><div class="label">সাপোর্ট</div><div class="value">৳${tech.support}</div></div>
          <div class="metric-box"><div class="label">রেজিস্ট্যান্স</div><div class="value">৳${tech.resistance}</div></div>
        </div>
      </div>

      <div class="card">
        <div class="section-title" style="font-size:16px; margin-bottom:12px;">ত্রৈমাসিক আর্থিক ফলাফল</div>
        <table>
          <thead><tr><td>প্রান্তিক</td><td class="right">রেভিনিউ (৳)</td><td class="right">নিট মুনাফা (৳)</td><td class="right">EPS</td></tr></thead>
          <tbody>
            ${fin.quarterly.map(q => `<tr><td>${q.period}</td><td class="num">${fmt(q.revenue)}</td><td class="num">${fmt(q.net_profit)}</td><td class="num">৳${q.eps}</td></tr>`).join('')}
          </tbody>
        </table>
      </div>
    `;
  } catch (e) {
    result.innerHTML = '<div class="empty">ডেটা লোড করা যায়নি।</div>';
  }
}

/* ---------------- PORTFOLIO ---------------- */
async function addHolding() {
  const trading_code = document.getElementById('pfCode').value.trim().toUpperCase();
  const quantity = parseInt(document.getElementById('pfQty').value, 10);
  const avg_buy_price = parseFloat(document.getElementById('pfPrice').value);
  if (!trading_code || !quantity || !avg_buy_price) { alert('সব ঘর পূরণ করুন'); return; }
  try {
    const res = await fetch('/v1/portfolio/holdings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trading_code, quantity, avg_buy_price }),
    });
    if (!res.ok) { alert('কোম্পানির কোড সঠিক নয়'); return; }
    document.getElementById('pfCode').value = '';
    document.getElementById('pfQty').value = '';
    document.getElementById('pfPrice').value = '';
    loadPortfolio();
  } catch (e) { alert('সার্ভার সংযোগে সমস্যা হয়েছে'); }
}

async function removeHolding(code) {
  await fetch(`/v1/portfolio/holdings/${code}`, { method: 'DELETE' });
  loadPortfolio();
}

async function loadPortfolio() {
  const result = document.getElementById('portfolioResult');
  result.innerHTML = '<div class="empty">লোড হচ্ছে...</div>';
  try {
    const res = await fetch('/v1/portfolio/summary');
    const data = await res.json();
    if (!data.holdings || data.holdings.length === 0) {
      result.innerHTML = '<div class="empty">এখনো কোনো হোল্ডিং যোগ করা হয়নি।</div>';
      return;
    }
    result.innerHTML = `
      <table>
        <thead><tr><td>কোড</td><td class="right">পরিমাণ</td><td class="right">গড় ক্রয়মূল্য</td><td class="right">এলটিপি</td><td class="right">বর্তমান মূল্য</td><td class="right">লাভ/ক্ষতি</td><td></td></tr></thead>
        <tbody>
          ${data.holdings.map(h => `
            <tr>
              <td class="code">${h.trading_code}</td>
              <td class="num">${fmt(h.quantity)}</td>
              <td class="num">৳${h.avg_buy_price}</td>
              <td class="num">৳${h.ltp}</td>
              <td class="num">৳${fmt(h.current_value)}</td>
              <td class="num">${h.pnl >= 0 ? '<span class="up">' : '<span class="down">'}৳${fmt(h.pnl)} (${h.pnl_pct}%)</span></td>
              <td><button class="del-btn" onclick="removeHolding('${h.trading_code}')">মুছুন</button></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
      <div class="total-box">
        <div class="metric-box"><div class="label">মোট বিনিয়োগ</div><div class="value">৳${fmt(data.total_invested)}</div></div>
        <div class="metric-box"><div class="label">বর্তমান মূল্য</div><div class="value">৳${fmt(data.total_current_value)}</div></div>
        <div class="metric-box"><div class="label">সর্বমোট লাভ/ক্ষতি</div><div class="value ${data.total_pnl >= 0 ? 'up' : 'down'}">৳${fmt(data.total_pnl)} (${data.total_pnl_pct}%)</div></div>
      </div>
    `;
  } catch (e) {
    result.innerHTML = '<div class="empty">ডেটা লোড করা যায়নি।</div>';
  }
}

/* ---------------- ADMIN (Manual Data Entry) ---------------- */
function parseMoverLines(text) {
  return text.split('\\n').map(l => l.trim()).filter(Boolean).map(line => {
    const parts = line.split(',').map(p => p.trim());
    return {
      trading_code: parts[0] || '',
      company_name: parts[1] || parts[0] || '',
      ltp: parseFloat(parts[2]) || 0,
      change_percent: parseFloat(parts[3]) || 0,
      volume: parseInt(parts[4], 10) || 0,
    };
  }).filter(r => r.trading_code);
}

async function submitManualEntry() {
  const key = document.getElementById('adminKey').value;
  const msg = document.getElementById('adminMsg');
  if (!key) { msg.innerHTML = '<span class="down">Admin Key দিন</span>'; return; }

  const close = parseFloat(document.getElementById('admClose').value);
  const change = parseFloat(document.getElementById('admChange').value);
  const changePct = parseFloat(document.getElementById('admChangePct').value);
  const volume = parseInt(document.getElementById('admVolume').value, 10) || 0;
  const turnover = parseFloat(document.getElementById('admTurnover').value) || 0;

  const body = {};
  if (!isNaN(close) && !isNaN(change) && !isNaN(changePct)) {
    body.index = { close_value: close, change_value: change, change_percent: changePct, total_volume: volume, total_turnover: turnover };
  }
  const gainers = parseMoverLines(document.getElementById('admGainers').value);
  const losers = parseMoverLines(document.getElementById('admLosers').value);
  if (gainers.length) body.gainers = gainers;
  if (losers.length) body.losers = losers;

  if (!body.index && !body.gainers && !body.losers) {
    msg.innerHTML = '<span class="down">অন্তত ইনডেক্স অথবা গেইনার্স/লুজার্স পূরণ করুন</span>';
    return;
  }

  msg.innerHTML = 'সেভ হচ্ছে...';
  try {
    const res = await fetch('/v1/admin/manual-entry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': key },
      body: JSON.stringify(body),
    });
    if (res.status === 401) { msg.innerHTML = '<span class="down">ভুল Admin Key</span>'; return; }
    if (!res.ok) { msg.innerHTML = '<span class="down">সেভ করা যায়নি</span>'; return; }
    msg.innerHTML = '<span class="up">✅ সেভ হয়ে গেছে — মার্কেট ট্যাবে গিয়ে রিফ্রেশ করলে যাচাইকৃত ডেটা দেখাবে</span>';
  } catch (e) {
    msg.innerHTML = '<span class="down">সার্ভার সংযোগে সমস্যা হয়েছে</span>';
  }
}

async function clearManualEntry() {
  const key = document.getElementById('adminKey').value;
  const msg = document.getElementById('adminMsg');
  if (!key) { msg.innerHTML = '<span class="down">Admin Key দিন</span>'; return; }
  try {
    const res = await fetch('/v1/admin/manual-entry/today', { method: 'DELETE', headers: { 'X-Admin-Key': key } });
    if (res.status === 401) { msg.innerHTML = '<span class="down">ভুল Admin Key</span>'; return; }
    msg.innerHTML = '✅ মুছে ফেলা হয়েছে, ডেমো ডেটায় ফিরে যাওয়া হয়েছে';
  } catch (e) {
    msg.innerHTML = '<span class="down">সার্ভার সংযোগে সমস্যা হয়েছে</span>';
  }
}

loadAll();
</script>
</body>
</html>"""


@app.get("/app", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML
