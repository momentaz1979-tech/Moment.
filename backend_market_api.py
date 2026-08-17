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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from datetime import date, timedelta
import random

app = FastAPI(title="StockPilot BD AI — Market API", version="1.0.0")

# UI (Claude আর্টিফ্যাক্ট / ব্রাউজার) থেকে ভিন্ন origin থেকে API কল করার অনুমতি
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    return {"message": "StockPilot BD AI Market API চলছে। /app এ যান লাইভ ড্যাশবোর্ড দেখতে, অথবা /docs এ API বিস্তারিত দেখতে।"}


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
  body { margin:0; min-height:100vh; background:#12151A; color:#EDEFF2; padding:28px 20px 60px; }
  .wrap { max-width:880px; margin:0 auto; }
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
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <div class="eyebrow">STOCKPILOT BD AI · MARKET (LIVE)</div>
      <h1>বাজার সংক্ষিপ্ত বিবরণ</h1>
    </div>
    <button class="refresh" onclick="loadAll()">↻ রিফ্রেশ</button>
  </div>
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

  <div class="footer">সংযুক্ত: এই সার্ভার (same-origin) — লাইভ ডেটা।</div>
</div>

<script>
let DATA = { gainers: [], losers: [], volume: [] };
let TAB = 'gainers';

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

    banner.className = 'banner live';
    banner.innerHTML = '● লাইভ — সার্ভার থেকে সরাসরি ডেটা আসছে';
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

loadAll();
</script>
</body>
</html>"""


@app.get("/app", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML
