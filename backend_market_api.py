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

from fastapi import FastAPI, HTTPException, Query
from datetime import date, timedelta
import random

app = FastAPI(title="StockPilot BD AI — Market API", version="1.0.0")

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
# ১. Market Index
# ---------------------------------------------------------------
@app.get("/v1/market/index/{index_name}")
def get_index(index_name: str, from_date: date | None = None, to_date: date | None = None):
    index_name = index_name.upper()
    if index_name not in ("DSEX", "DS30", "DSES"):
        raise HTTPException(400, "অবৈধ ইনডেক্স নাম। DSEX, DS30, অথবা DSES ব্যবহার করুন।")
    today = to_date or date.today()
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
    }


# ---------------------------------------------------------------
# ২-৪. Gainers / Losers / Volume Leaders
# ---------------------------------------------------------------
def _all_prices(d: date):
    return [_seeded_price(c["trading_code"], d) | {"company_name": _company_by_code(c["trading_code"])["company_name"]}
            for c in COMPANIES]


@app.get("/v1/market/gainers")
def get_gainers(target_date: date = Query(default_factory=date.today), limit: int = 20):
    rows = sorted(_all_prices(target_date), key=lambda r: r["change_percent"], reverse=True)
    return {"trade_date": target_date.isoformat(), "data": rows[:limit]}


@app.get("/v1/market/losers")
def get_losers(target_date: date = Query(default_factory=date.today), limit: int = 20):
    rows = sorted(_all_prices(target_date), key=lambda r: r["change_percent"])
    return {"trade_date": target_date.isoformat(), "data": rows[:limit]}


@app.get("/v1/market/volume-leaders")
def get_volume_leaders(target_date: date = Query(default_factory=date.today), limit: int = 20):
    rows = sorted(_all_prices(target_date), key=lambda r: r["volume"], reverse=True)
    return {"trade_date": target_date.isoformat(), "data": rows[:limit]}


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
    return {"trade_date": target_date.isoformat(), "data": data}


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
    return {"trade_date": target_date.isoformat(), "sectors": result}


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
    return {"trading_code": trading_code, "series": series}


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


@app.get("/")
def root():
    return {"message": "StockPilot BD AI Market API চলছে। /docs এ যান বিস্তারিত দেখতে।"}
