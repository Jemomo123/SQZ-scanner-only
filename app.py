from flask import Flask, render_template_string, jsonify
import os, requests, math

app = Flask(__name__)

BINANCE = "https://fapi.binance.com"
BYBIT   = "https://api.bybit.com"
OKX     = "https://www.okx.com"

PAIRS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","AVAXUSDT","LINKUSDT","ARBUSDT","OPUSDT",
    "SUIUSDT","INJUSDT","WIFUSDT","PEPEUSDT","TONUSDT",
    "NEARUSDT","APTUSDT","TIAUSDT","ORDIUSDT","SEIUSDT",
    "1000BONKUSDT","WUSDT","PYTHUSDT","JTOUSDT",
]

TF_MAP    = {"3m":"3m","5m":"5m","15m":"15m","1h":"1h","4h":"4h","1d":"1d"}
BYBIT_TF  = {"3m":"3","5m":"5","15m":"15","1h":"60","4h":"240","1d":"D"}
OKX_TF    = {"3m":"3m","5m":"5m","15m":"15m","1h":"1H","4h":"4H","1d":"1Dutc"}

# ── Exchange fetchers ─────────────────────────────────────────────────────────

def _norm(raw_list):
    """Normalise raw kline rows into dicts {o,h,l,c}"""
    return [{"o":float(k[0]),"h":float(k[1]),"l":float(k[2]),"c":float(k[3])} for k in raw_list]

def klines_binance(symbol, interval, limit=200):
    try:
        r = requests.get(f"{BINANCE}/fapi/v1/klines",
            params={"symbol":symbol,"interval":interval,"limit":limit}, timeout=7)
        if r.status_code != 200: return []
        rows = r.json()
        # Drop the last candle — it is the current OPEN (unfinished) candle.
        # Its close field is just the last tick price and shifts every second,
        # causing SMA values to differ from what your chart shows.
        return _norm([[k[1],k[2],k[3],k[4]] for k in rows[:-1]])
    except Exception:
        return []

def klines_bybit(symbol, interval, limit=200):
    try:
        tf = BYBIT_TF.get(interval,"15")
        r = requests.get(f"{BYBIT}/v5/market/kline",
            params={"category":"linear","symbol":symbol,"interval":tf,"limit":limit}, timeout=7)
        if r.status_code != 200: return []
        rows = r.json().get("result",{}).get("list",[])
        if not rows: return []
        rows = list(reversed(rows))
        # Drop last (current open) candle
        return _norm([[k[1],k[2],k[3],k[4]] for k in rows[:-1]])
    except Exception:
        return []

def klines_okx(symbol, interval, limit=200):
    try:
        sym = symbol.replace("1000BONKUSDT","BONK-USDT-SWAP") \
                    .replace("USDT","-USDT-SWAP")
        tf  = OKX_TF.get(interval,"15m")
        r = requests.get(f"{OKX}/api/v5/market/candles",
            params={"instId":sym,"bar":tf,"limit":limit}, timeout=7)
        if r.status_code != 200: return []
        rows = r.json().get("data",[])
        if not rows: return []
        rows = list(reversed(rows))
        # Drop last (current open) candle
        return _norm([[k[1],k[2],k[3],k[4]] for k in rows[:-1]])
    except Exception:
        return []

def get_klines(symbol, interval, live_price=None, limit=200):
    """
    Fetch closed candles only (open candle dropped).
    Then append the live ticker price as a synthetic current candle so
    the SMA reflects what the chart shows right now.
    """
    for fn, name in [(klines_binance,"binance"),(klines_bybit,"bybit"),(klines_okx,"okx")]:
        k = fn(symbol, interval, limit)
        if len(k) >= 110:
            # Append live price as a synthetic closed candle for the current bar.
            # This makes SMA20/SMA100 match exactly what your chart displays.
            if live_price:
                k.append({"o":live_price,"h":live_price,"l":live_price,"c":live_price})
            return k, name
    return [], "none"

def tickers_binance():
    try:
        r = requests.get(f"{BINANCE}/fapi/v1/ticker/24hr", timeout=7)
        if r.status_code != 200: return {}
        return {d["symbol"]:{"price":float(d["lastPrice"]),"chg":float(d["priceChangePercent"]),"volume":float(d["quoteVolume"])} for d in r.json()}
    except Exception:
        return {}

def tickers_bybit():
    try:
        r = requests.get(f"{BYBIT}/v5/market/tickers",
            params={"category":"linear"}, timeout=7)
        if r.status_code != 200: return {}
        items = r.json().get("result",{}).get("list",[])
        return {d["symbol"]:{"price":float(d["lastPrice"]),"chg":float(d["price24hPcnt"])*100,"volume":float(d["turnover24h"])} for d in items}
    except Exception:
        return {}

def get_tickers():
    t = tickers_binance()
    if t: return t, "binance"
    t = tickers_bybit()
    if t: return t, "bybit"
    return {}, "none"

def funding_binance():
    try:
        r = requests.get(f"{BINANCE}/fapi/v1/premiumIndex", timeout=7)
        if r.status_code != 200: return {}
        return {d["symbol"]: float(d["lastFundingRate"]) for d in r.json()}
    except Exception:
        return {}

def get_funding():
    f = funding_binance()
    return f if f else {}

# ── Indicator helpers ─────────────────────────────────────────────────────────

def sma(vals, n):
    if len(vals) < n: return None
    return sum(vals[-n:]) / n

def stdev(vals, n):
    s = sma(vals, n)
    if s is None: return None
    return math.sqrt(sum((x - s)**2 for x in vals[-n:]) / n)

def ema(vals, n):
    """Standard EMA used as KC basis (matches TradingView)"""
    if len(vals) < n: return None
    k = 2.0 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e

def rma(vals, n):
    """Wilder RMA used for ATR in KC (matches TradingView)"""
    if len(vals) < n: return None
    k = 1.0 / n
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e

def tr_series(highs, lows, closes):
    out = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        out.append(max(
            highs[i] - lows[i],
            abs(highs[i]  - closes[i-1]),
            abs(lows[i]   - closes[i-1])
        ))
    return out

# ── Core squeeze calculation ──────────────────────────────────────────────────

def calc_squeeze(klines, bb_len=20, bb_mult=2.0, kc_len=20, kc_mult=1.5, clust_pct=0.001):
    """
    Volatility SQZ  : BB (SMA basis, stdev bands) INSIDE KC (EMA basis, RMA-ATR bands)
                      — identical to TradingView's Squeeze Momentum Indicator.
    SMA SQZ         : price, SMA20, SMA100 all within clust_pct of each other.
    Fire detection  : squeeze was ON last bar and is OFF this bar.
    """
    if len(klines) < 110:
        return {"volSqz":"none","smaSqz":"none","sqzBarsCount":0,
                "lastBarMult":0,"lastWickRatio":0,"sma20":0,"sma100":0,"smaRange":0}

    closes = [k["c"] for k in klines]
    highs  = [k["h"] for k in klines]
    lows   = [k["l"] for k in klines]
    opens  = [k["o"] for k in klines]
    trs    = tr_series(highs, lows, closes)

    # ── helper: vol sqz — returns (is_squeezed, bb_width, kc_width) ──
    def vol_state(c, h, l):
        t = tr_series(h, l, c)
        bb_b = sma(c, bb_len)
        bb_d = stdev(c, bb_len)
        kc_b = ema(c, kc_len)
        kc_a = rma(t, kc_len)
        if not all([bb_b, bb_d, kc_b, kc_a]):
            return False, 0.0, 0.0
        bb_u = bb_b + bb_mult * bb_d
        bb_l = bb_b - bb_mult * bb_d
        kc_u = kc_b + kc_mult * kc_a
        kc_l = kc_b - kc_mult * kc_a
        bb_w = bb_u - bb_l
        kc_w = kc_u - kc_l
        return (bb_u < kc_u and bb_l > kc_l), bb_w, kc_w

    # ── helper: SMA sqz — returns (is_clustered, range_pct) ──
    def sma_state(c):
        if len(c) < 100: return False, 0.0
        p    = c[-1]
        s20  = sma(c, 20)
        s100 = sma(c, 100)
        if not s20 or not s100: return False, 0.0
        rng = max(abs(p - s20), abs(p - s100), abs(s20 - s100)) / p
        return rng < clust_pct, rng

    # ── Step 1: scan back to find the most recent SQZ block ──
    # Walk backwards. A SQZ block = consecutive bars where vol OR sma was ON.
    # sqz_end_idx   = last bar INSIDE the squeeze
    # sqz_start_idx = first bar of that squeeze block
    n = len(closes)
    sqz_end_idx   = None
    sqz_start_idx = None
    in_sqz_block  = False

    lookback = min(60, n - 2)
    for i in range(n - 2, n - 2 - lookback, -1):
        if i < 20: break
        c_sl = closes[:i+1]
        h_sl = highs[:i+1]
        l_sl = lows[:i+1]
        v, _, _ = vol_state(c_sl, h_sl, l_sl)
        s, _ = sma_state(c_sl)
        was_sqz = v or s
        if was_sqz:
            if sqz_end_idx is None:
                sqz_end_idx = i       # rightmost bar still in squeeze
            sqz_start_idx = i         # keep pushing left
            in_sqz_block = True
        else:
            if in_sqz_block:
                break                 # found the bar before squeeze started

    sqz_bars = 0
    if sqz_end_idx is not None and sqz_start_idx is not None:
        sqz_bars = sqz_end_idx - sqz_start_idx + 1

    # ── Step 2: current SQZ state — ON / FORMING / FIRE / NONE ──
    cur_vol,  cur_bb_w,  cur_kc_w  = vol_state(closes, highs, lows)
    prev_vol, prev_bb_w, prev_kc_w = vol_state(closes[:-1], highs[:-1], lows[:-1])
    p2_vol,   p2_bb_w,  p2_kc_w   = vol_state(closes[:-2], highs[:-2], lows[:-2])

    cur_sma,  cur_rng  = sma_state(closes)
    prev_sma, prev_rng = sma_state(closes[:-1])
    p2_sma,   p2_rng   = sma_state(closes[:-2])

    # VOL SQZ state
    if cur_vol:
        vol_sqz = "on"
    elif prev_vol:
        vol_sqz = "fire"
    else:
        # FORMING: BB is narrowing toward KC over last 3 bars (ratio shrinking)
        cur_ratio  = cur_bb_w  / cur_kc_w  if cur_kc_w  > 0 else 1.0
        prev_ratio = prev_bb_w / prev_kc_w if prev_kc_w > 0 else 1.0
        p2_ratio   = p2_bb_w  / p2_kc_w   if p2_kc_w   > 0 else 1.0
        narrowing  = cur_ratio < prev_ratio < p2_ratio and cur_ratio < 1.2
        vol_sqz = "forming" if narrowing else "none"

    # SMA SQZ state
    if cur_sma:
        sma_sqz = "on"
    elif prev_sma:
        sma_sqz = "fire"
    else:
        # FORMING: gap between price/SMA20/SMA100 narrowing over last 3 bars
        narrowing = (cur_rng < prev_rng < p2_rng) and cur_rng < clust_pct * 4
        sma_sqz = "forming" if narrowing else "none"

    # ── Step 3: avg body of SQZ candles (the reference size) ──
    sqz_avg = 0.0
    if sqz_bars > 0 and sqz_start_idx is not None:
        sizes = [abs(closes[j] - opens[j])
                 for j in range(sqz_start_idx, sqz_end_idx + 1)]
        sqz_avg = sum(sizes) / len(sizes) if sizes else 0.0

    # ── Step 4: HOW LONG AGO DID THE SQZ END? ──
    # Keep FIRE visible for up to 10 bars after the SQZ ended, not just 1.
    # fire_bars_ago = 0 means it fired THIS candle (freshest)
    #               = 5 means it fired 5 candles ago (still relevant)
    #               = -1 means no recent fire found
    fire_bars_ago = -1
    if sqz_end_idx is not None:
        # sqz_end_idx+1 is the first bar after the squeeze
        first_post = sqz_end_idx + 1
        if first_post <= n - 1:
            fire_bars_ago = (n - 1) - first_post   # 0 = just fired this bar

    # Override vol/sma sqz to show FIRE for up to 10 bars after ending
    if fire_bars_ago >= 0 and fire_bars_ago <= 10:
        if vol_sqz == "none" and not cur_vol:
            # Check if we were recently in a vol squeeze
            if sqz_end_idx is not None:
                v_at_end, _, _ = vol_state(closes[:sqz_end_idx+1], highs[:sqz_end_idx+1], lows[:sqz_end_idx+1])
                if v_at_end:
                    vol_sqz = "fire"
        if sma_sqz == "none" and not cur_sma:
            if sqz_end_idx is not None:
                s_at_end, _ = sma_state(closes[:sqz_end_idx+1])
                if s_at_end:
                    sma_sqz = "fire"

    # ── Step 5: SQZ PRICE RANGE ──
    # The high and low of ALL candles inside the squeeze.
    # A confirming bar must close OUTSIDE this range — not just be big.
    sqz_high = None
    sqz_low  = None
    if sqz_start_idx is not None and sqz_end_idx is not None:
        sqz_high = max(highs[sqz_start_idx : sqz_end_idx + 1])
        sqz_low  = min(lows[sqz_start_idx  : sqz_end_idx + 1])

    # ── Step 6: BREAKOUT DIRECTION ──
    # Direction based on which side of the SQZ range price broke out of.
    # Use sqz_high/sqz_low rather than midpoint — more precise.
    breakout_dir = "none"
    if sqz_end_idx is not None and sqz_end_idx + 1 < n and sqz_high and sqz_low:
        first_post = sqz_end_idx + 1
        if closes[first_post] > sqz_high:
            breakout_dir = "up"
        elif closes[first_post] < sqz_low:
            breakout_dir = "down"
        else:
            # Still inside range — ambiguous drift, not a real breakout
            breakout_dir = "inside"

    # ── Step 7: FORWARD-LOOKING elephant/tail bar scan ──
    # Rules for a VALID confirming bar:
    #   1. Body >= elephMult * avg SQZ candle body (size check)
    #   2. Close is OUTSIDE the SQZ high/low range (location check)
    #      - Elephant: close > sqz_high (bullish) or close < sqz_low (bearish)
    #      - Tail: wick pierces outside range, body may close back inside
    # Both checks must pass. A big bar that stays inside the SQZ range
    # is NOT a confirmation — the energy went nowhere.
    best_bar_mult   = 0.0
    best_wick       = 0.0
    confirm_bar_ago = -1
    confirm_outside = False   # did the confirm bar actually break the SQZ range?

    if sqz_end_idx is not None and sqz_avg > 0 and sqz_high and sqz_low:
        post_start = sqz_end_idx + 1
        search_end = min(n, post_start + 5)
        for j in range(post_start, search_end):
            body  = abs(closes[j] - opens[j])
            rng   = highs[j] - lows[j]
            wick  = (rng - body) / rng if rng > 0 else 0.0
            mult  = body / sqz_avg

            # Location check: close outside SQZ range (elephant)
            # OR wick pierces outside SQZ range (tail)
            close_outside = closes[j] > sqz_high or closes[j] < sqz_low
            wick_outside  = highs[j] > sqz_high or lows[j] < sqz_low

            if mult > best_bar_mult and (close_outside or wick_outside):
                best_bar_mult   = mult
                best_wick       = wick
                confirm_bar_ago = (n - 1) - j
                confirm_outside = close_outside

    s20  = sma(closes, 20)
    s100 = sma(closes, 100)

    return {
        "volSqz":        vol_sqz,
        "smaSqz":        sma_sqz,
        "sqzBarsCount":  sqz_bars,
        "fireBarAgo":    fire_bars_ago,
        "breakoutDir":   breakout_dir,
        "lastBarMult":   round(best_bar_mult, 2),
        "lastWickRatio": round(best_wick, 2),
        "confirmBarAgo": confirm_bar_ago,
        "confirmOutside":confirm_outside,
        "sqzHigh":       round(sqz_high, 8) if sqz_high else 0,
        "sqzLow":        round(sqz_low,  8) if sqz_low  else 0,
        "sma20":         round(s20,  8) if s20  else 0,
        "sma100":        round(s100, 8) if s100 else 0,
        "smaRange":      round(cur_rng * 100, 3),
    }

# ── API route ─────────────────────────────────────────────────────────────────

@app.route('/api/scan/<tf>')
def scan(tf):
    from flask import request as freq
    interval   = TF_MAP.get(tf, "15m")
    # clust_pct comes from the frontend slider (e.g. ?clust=0.1 means 0.1%)
    try:
        clust_pct = float(freq.args.get("clust", "0.1")) / 100.0
    except Exception:
        clust_pct = 0.001
    # bb/kc params also passable for power users
    try:    bb_mult = float(freq.args.get("bbm", "2.0"))
    except: bb_mult = 2.0
    try:    kc_mult = float(freq.args.get("kcm", "1.5"))
    except: kc_mult = 1.5

    tickers, tick_src = get_tickers()
    fundings          = get_funding()
    results           = []

    for sym in PAIRS:
        t = tickers.get(sym)
        if not t: continue

        live_price = t["price"] if t else None
        klines, ksrc = get_klines(sym, interval, live_price=live_price)
        sqz = calc_squeeze(klines, bb_mult=bb_mult, kc_mult=kc_mult, clust_pct=clust_pct) if klines else {
            "volSqz":"none","smaSqz":"none","sqzBarsCount":0,
            "lastBarMult":0,"lastWickRatio":0,"sma20":0,"sma100":0,"smaRange":0,
            "fireBarAgo":-1,"breakoutDir":"none","confirmBarAgo":-1,
            "confirmOutside":False,"sqzHigh":0,"sqzLow":0
        }

        funding = round(fundings.get(sym, 0.0) * 100, 4)
        display = sym.replace("1000BONKUSDT","BONK/USDT").replace("USDT","/USDT")

        results.append({
            "sym":sym, "display":display,
            "price":t["price"], "chg":round(t["chg"],2), "volume":t["volume"],
            "volSqz":sqz["volSqz"], "smaSqz":sqz["smaSqz"],
            "both":    sqz["volSqz"]!="none" and sqz["smaSqz"]!="none",
            "eitherActive": sqz["volSqz"]!="none" or sqz["smaSqz"]!="none",
            "bothFire":sqz["volSqz"]=="fire"  and sqz["smaSqz"]=="fire",
            "sqzBarsCount": sqz["sqzBarsCount"],
            "lastBarMult":  sqz["lastBarMult"],
            "lastWickRatio":sqz["lastWickRatio"],
            "sma20":sqz["sma20"], "sma100":sqz["sma100"],
            "smaRange":sqz["smaRange"], "funding":funding,
            "src": ksrc,
        })

    return jsonify(results)

# ── Frontend ──────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="theme-color" content="#0a0c11"/>
<title>SQZ Scanner</title>
<style>
  :root{--bg:#0a0c11;--s1:#111318;--s2:#181c24;--s3:#1f2430;--border:#252b3a;--text:#e2e8f0;--muted:#56637a;--dim:#2e3647;--bull:#22c55e;--bear:#ef4444;--purple:#a855f7;--orange:#f97316;--blue:#3b82f6;--bull-bg:rgba(34,197,94,0.08);--bear-bg:rgba(239,68,68,0.08);--purple-bg:rgba(168,85,247,0.10);--orange-bg:rgba(249,115,22,0.12);}
  *{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
  html,body{height:100%;background:var(--bg);color:var(--text);font-family:'SF Mono','Fira Code',monospace}
  body{display:flex;flex-direction:column;overflow:hidden}
  .topbar{background:var(--s1);border-bottom:0.5px solid var(--border);padding:10px 14px 8px;flex-shrink:0}
  .topbar-row1{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
  .brand{font-size:14px;font-weight:700;letter-spacing:2px}.brand span{color:var(--orange)}
  .right-top{display:flex;align-items:center;gap:8px}
  .live-dot{width:7px;height:7px;border-radius:50%;background:var(--bull);animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
  .live-lbl{font-size:10px;color:var(--bull);letter-spacing:1px}
  .scan-btn{background:var(--orange);color:#fff;border:none;padding:6px 16px;border-radius:5px;font-size:12px;font-family:inherit;font-weight:700;letter-spacing:1px;cursor:pointer}
  .scan-btn:active{opacity:.75}.scan-btn.loading{background:var(--s3);color:var(--muted)}
  .tf-row{display:flex;gap:4px}
  .tf-btn{flex:1;padding:5px 0;font-size:11px;font-weight:700;letter-spacing:1px;border-radius:4px;border:0.5px solid var(--border);background:transparent;color:var(--muted);font-family:inherit;cursor:pointer}
  .tf-btn.on{background:var(--s3);color:var(--text);border-color:var(--dim)}
  .settings-toggle{display:flex;align-items:center;justify-content:space-between;padding:7px 14px;background:var(--s2);border-bottom:0.5px solid var(--border);cursor:pointer;font-size:11px;letter-spacing:1px;color:var(--muted);flex-shrink:0}
  .settings-drawer{background:var(--s1);border-bottom:0.5px solid var(--border);padding:10px 14px 12px;display:none;flex-shrink:0}
  .settings-drawer.open{display:block}
  .settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .set-card{background:var(--s2);border-radius:6px;padding:8px 10px}
  .set-lbl{font-size:10px;color:var(--muted);letter-spacing:.8px;margin-bottom:4px}
  .set-row{display:flex;align-items:center;gap:6px}
  .set-row input[type=range]{flex:1;accent-color:var(--orange);height:2px}
  .set-val{font-size:11px;font-weight:700;color:var(--orange);min-width:32px;text-align:right}
  .set-section{font-size:10px;color:var(--purple);letter-spacing:1.5px;margin:8px 0 5px;font-weight:700}
  .stats-bar{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:0.5px solid var(--border);flex-shrink:0}
  .stat{padding:8px 4px;text-align:center;border-right:0.5px solid var(--border)}.stat:last-child{border-right:none}
  .stat-n{font-size:18px;font-weight:700}.stat-l{font-size:9px;color:var(--muted);letter-spacing:.5px;margin-top:1px}
  .filter-row{display:flex;gap:5px;padding:7px 14px;overflow-x:auto;border-bottom:0.5px solid var(--border);flex-shrink:0;scrollbar-width:none}
  .filter-row::-webkit-scrollbar{display:none}
  .fpill{flex-shrink:0;padding:4px 11px;font-size:10px;font-weight:700;letter-spacing:.7px;border-radius:20px;border:0.5px solid var(--border);background:transparent;color:var(--muted);font-family:inherit;cursor:pointer;white-space:nowrap}
  .fpill.on{border-color:var(--purple);color:var(--purple);background:var(--purple-bg)}
  .legend{display:flex;gap:12px;padding:5px 14px 6px;border-bottom:0.5px solid var(--border);flex-shrink:0;overflow-x:auto;scrollbar-width:none}
  .legend::-webkit-scrollbar{display:none}
  .li{display:flex;align-items:center;gap:4px;font-size:10px;color:var(--muted);white-space:nowrap}
  .dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
  .d-on{background:var(--purple)}.d-fire{background:var(--bull)}.d-none{background:var(--border)}.d-forming{background:var(--warn);animation:pulse 1.5s infinite}
  .cards{flex:1;overflow-y:auto;padding:8px 10px 80px;-webkit-overflow-scrolling:touch}
  .card{background:var(--s1);border:0.5px solid var(--border);border-radius:8px;margin-bottom:7px;padding:10px 12px;cursor:pointer}
  .card:active{background:var(--s2)}.card.both-fire{border-color:var(--orange)}.card.vol-fire{border-left:2px solid var(--bull)}.card.sma-fire{border-left:2px solid var(--purple)}.card.forming{border-left:2px solid var(--warn)}.card.fire-no-confirm{border-left:2px solid var(--bear)}
  .card-top{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:8px}
  .pair-name{font-size:14px;font-weight:700;letter-spacing:.3px}.pair-cat{font-size:10px;color:var(--muted);margin-top:1px}
  .price-col{text-align:right}.price{font-size:13px;font-weight:700}.chg{font-size:11px;margin-top:1px}
  .up{color:var(--bull)}.dn{color:var(--bear)}.neu{color:var(--muted)}
  .card-mid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px}
  .sqz-box{background:var(--s2);border-radius:5px;padding:6px 8px;border:0.5px solid var(--border)}
  .sqz-box.active-forming{border-color:var(--warn);background:rgba(245,158,11,0.08)}
  .sqz-box.active-on{border-color:var(--purple);background:var(--purple-bg)}
  .sqz-box.active-fire{border-color:var(--bull);background:var(--bull-bg)}
  .sqz-type{font-size:9px;color:var(--muted);letter-spacing:1px;margin-bottom:3px}
  .sqz-status{display:flex;align-items:center;gap:5px;font-size:11px;font-weight:700}
  .sqz-status .dot{width:8px;height:8px}
  .card-bot{display:flex;align-items:center;justify-content:space-between}
  .meta{display:flex;gap:10px;align-items:center}
  .fund-tag{font-size:10px;font-weight:700;padding:2px 7px;border-radius:3px;letter-spacing:.5px}
  .fund-pos{background:var(--bull-bg);color:var(--bull)}.fund-neg{background:var(--bear-bg);color:var(--bear)}
  .vol-tag{font-size:10px;color:var(--muted)}
  .src-tag{font-size:9px;color:var(--dim);letter-spacing:.5px;margin-left:2px}
  .bias-badge{font-size:10px;font-weight:700;padding:3px 9px;border-radius:3px;letter-spacing:.8px}
  .b-long{background:var(--bull-bg);color:var(--bull)}.b-short{background:var(--bear-bg);color:var(--bear)}.b-flat{background:var(--s3);color:var(--muted)}
  .breakout-banner{font-size:10px;font-weight:700;letter-spacing:.8px;padding:4px 8px;border-radius:3px;margin-top:6px;display:flex;align-items:center;gap:5px}
  .bb-elephant{background:var(--orange-bg);color:var(--orange);border:0.5px solid var(--orange)}
  .bb-tail{background:rgba(59,130,246,0.1);color:var(--blue);border:0.5px solid var(--blue)}
  .star{color:var(--orange);font-size:13px;margin-left:3px}
  .loading-screen{position:fixed;inset:0;background:var(--bg);display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:200;gap:14px}
  .loading-title{font-size:16px;font-weight:700;letter-spacing:2px}.loading-title span{color:var(--orange)}
  .loading-sub{font-size:11px;color:var(--muted);letter-spacing:1px}
  .spinner{width:28px;height:28px;border:2px solid var(--border);border-top-color:var(--orange);border-radius:50%;animation:spin .8s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  .error-banner{background:var(--bear-bg);border:0.5px solid var(--bear);border-radius:6px;padding:10px 14px;margin:8px 10px;font-size:11px;color:var(--bear);display:none}
  .modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:100;align-items:flex-end}
  .modal-bg.open{display:flex}
  .modal{background:var(--s1);border-radius:14px 14px 0 0;border:0.5px solid var(--border);width:100%;padding:0 0 30px;max-height:85vh;overflow-y:auto}
  .modal-handle{width:36px;height:4px;background:var(--dim);border-radius:2px;margin:10px auto 14px}
  .modal-close{position:absolute;top:12px;right:16px;background:var(--s3);border:none;color:var(--muted);font-size:18px;width:28px;height:28px;border-radius:50%;cursor:pointer;display:flex;align-items:center;justify-content:center;font-family:inherit}
  .modal-header{padding:0 16px 12px;border-bottom:0.5px solid var(--border);position:relative}
  .modal-pair{font-size:20px;font-weight:700;letter-spacing:.5px}.modal-price{font-size:16px;margin-top:2px}
  .modal-body{padding:14px 16px}.modal-section{margin-bottom:16px}
  .modal-section-title{font-size:10px;color:var(--muted);letter-spacing:1.5px;margin-bottom:8px;font-weight:700}
  .detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .detail-card{background:var(--s2);border-radius:6px;padding:8px 10px;border:0.5px solid var(--border)}
  .detail-lbl{font-size:10px;color:var(--muted);letter-spacing:.5px}.detail-val{font-size:13px;font-weight:700;margin-top:2px}
  .confirm-box{background:var(--s2);border-radius:8px;padding:12px;border:0.5px solid var(--border);margin-top:6px}
  .confirm-title{font-size:11px;font-weight:700;letter-spacing:1px;margin-bottom:8px}
  .confirm-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px}
  .confirm-lbl{font-size:11px;color:var(--muted)}.confirm-val{font-size:11px;font-weight:700}
  .rule-box{background:var(--purple-bg);border:0.5px solid var(--purple);border-radius:6px;padding:10px 12px;margin-top:6px;font-size:11px;line-height:1.7;color:var(--text)}
  .rule-box strong{color:var(--orange)}
  .empty{text-align:center;padding:40px 20px;color:var(--muted);font-size:12px}
</style>
</head>
<body>
<div class="loading-screen" id="loadingScreen">
  <div class="loading-title">CRYPTO <span>SQZ</span></div>
  <div class="spinner"></div>
  <div class="loading-sub">FETCHING LIVE DATA...</div>
</div>
<div class="topbar">
  <div class="topbar-row1">
    <div class="brand">CRYPTO <span>SQZ</span></div>
    <div class="right-top">
      <span class="live-dot"></span><span class="live-lbl">LIVE</span>
      <button class="scan-btn" id="scanBtn" onclick="doScan()">&#8635; SCAN</button>
    </div>
  </div>
  <div class="tf-row">
    <button class="tf-btn on" onclick="setTF(this,'3m')">3M</button>
    <button class="tf-btn" onclick="setTF(this,'5m')">5M</button>
    <button class="tf-btn" onclick="setTF(this,'15m')">15M</button>
    <button class="tf-btn" onclick="setTF(this,'1h')">1H</button>
    <button class="tf-btn" onclick="setTF(this,'4h')">4H</button>
    <button class="tf-btn" onclick="setTF(this,'1d')">1D</button>
  </div>
</div>
<div class="settings-toggle" onclick="toggleSettings()">
  <span>INDICATOR SETTINGS</span><span id="settingsArrow">&#9660;</span>
</div>
<div class="settings-drawer" id="settingsDrawer">
  <div class="set-section">VOLATILITY SQZ &mdash; BB vs KC</div>
  <div class="settings-grid">
    <div class="set-card"><div class="set-lbl">BB LENGTH</div><div class="set-row"><input type="range" min="10" max="30" value="20" step="1" oninput="sv('bbLv',this.value)"><span class="set-val" id="bbLv">20</span></div></div>
    <div class="set-card"><div class="set-lbl">BB MULT</div><div class="set-row"><input type="range" min="10" max="30" value="20" step="1" oninput="sv('bbMv',(this.value/10).toFixed(1))"><span class="set-val" id="bbMv">2.0</span></div></div>
    <div class="set-card"><div class="set-lbl">KC LENGTH</div><div class="set-row"><input type="range" min="10" max="30" value="20" step="1" oninput="sv('kcLv',this.value)"><span class="set-val" id="kcLv">20</span></div></div>
    <div class="set-card"><div class="set-lbl">KC MULT</div><div class="set-row"><input type="range" min="10" max="20" value="15" step="1" oninput="sv('kcMv',(this.value/10).toFixed(1))"><span class="set-val" id="kcMv">1.5</span></div></div>
  </div>
  <div class="set-section">SMA SQZ &mdash; PRICE + SMA20 + SMA100 CLUSTER</div>
  <div class="settings-grid">
    <div class="set-card"><div class="set-lbl">CLUSTER THRESHOLD %</div><div class="set-row"><input type="range" min="1" max="20" value="1" step="1" oninput="sv('clustV',(this.value/10).toFixed(1)+'%')"><span class="set-val" id="clustV">0.1%</span></div></div>
    <div class="set-card"><div class="set-lbl">ELEPHANT BAR MULT</div><div class="set-row"><input type="range" min="10" max="30" value="10" step="1" oninput="sv('elephV',(this.value/10).toFixed(1)+'x')"><span class="set-val" id="elephV">1.0x</span></div></div>
    <div class="set-card"><div class="set-lbl">TAIL WICK RATIO %</div><div class="set-row"><input type="range" min="40" max="90" value="60" step="5" oninput="sv('tailV',this.value+'%')"><span class="set-val" id="tailV">60%</span></div></div>
  </div>
</div>
<div class="stats-bar" id="statsBar">
  <div class="stat"><div class="stat-n" style="color:#f97316">-</div><div class="stat-l">BOTH FIRE</div></div>
  <div class="stat"><div class="stat-n" style="color:#22c55e">-</div><div class="stat-l">CONFIRMED</div></div>
  <div class="stat"><div class="stat-n" style="color:#a855f7">-</div><div class="stat-l">VOL SQZ</div></div>
  <div class="stat"><div class="stat-n" style="color:#ef4444">-</div><div class="stat-l">NEG FUND</div></div>
</div>
<div class="filter-row">
  <button class="fpill on" onclick="setFilter(this,'all')">ALL</button>
  <button class="fpill" onclick="setFilter(this,'forming')">FORMING</button>
  <button class="fpill" onclick="setFilter(this,'both')">BOTH SQZ</button>
  <button class="fpill" onclick="setFilter(this,'vol')">VOL SQZ</button>
  <button class="fpill" onclick="setFilter(this,'sma')">SMA SQZ</button>
  <button class="fpill" onclick="setFilter(this,'confirmed')">CONFIRMED</button>
  <button class="fpill" onclick="setFilter(this,'no_confirm')">NO CONFIRM</button>
  <button class="fpill" onclick="setFilter(this,'elephant')">ELEPHANT BAR</button>
  <button class="fpill" onclick="setFilter(this,'tail')">TAIL BAR</button>
  <button class="fpill" onclick="setFilter(this,'long')">LONG BIAS</button>
  <button class="fpill" onclick="setFilter(this,'short')">SHORT BIAS</button>
  <button class="fpill" onclick="setFilter(this,'neg_fund')">NEG FUNDING</button>
</div>
<div class="legend">
  <div class="li"><span class="dot d-forming"></span>FORMING</div>
  <div class="li"><span class="dot d-on"></span>SQZ ON</div>
  <div class="li"><span class="dot d-fire"></span>SQZ FIRE</div>
  <div class="li"><span class="dot d-none"></span>NONE</div>
  <div class="li"><span style="color:var(--orange);font-size:11px">&#9733;</span>&nbsp;BOTH FIRING</div>
</div>
<div id="errorBanner" class="error-banner">Could not reach exchanges. Check connection.</div>
<div class="cards" id="cards"></div>
<div class="modal-bg" id="modalBg" onclick="closeModal(event)">
  <div class="modal">
    <div class="modal-handle"></div>
    <button class="modal-close" onclick="closeModalBtn()">&#10005;</button>
    <div class="modal-header" id="modalHeader"></div>
    <div class="modal-body" id="modalBody"></div>
  </div>
</div>
<script>
let st={tf:"3m",filter:"all",data:[]};
function sv(id,val){document.getElementById(id).textContent=val;}
function getSetts(){return{elephMult:parseFloat(document.getElementById("elephV").textContent),tailRatio:+document.getElementById("tailV").textContent/100};}
function fmtPrice(v){if(v>=1000)return"$"+v.toLocaleString("en",{maximumFractionDigits:1});if(v>=1)return"$"+v.toFixed(3);if(v>=0.0001)return"$"+v.toFixed(5);return"$"+v.toFixed(8);}
function fmtVol(v){if(v>=1e9)return(v/1e9).toFixed(1)+"B";if(v>=1e6)return(v/1e6).toFixed(1)+"M";return(v/1e3).toFixed(0)+"K";}
function setTF(el,tf){document.querySelectorAll(".tf-btn").forEach(b=>b.classList.remove("on"));el.classList.add("on");st.tf=tf;doScan();}
function setFilter(el,f){document.querySelectorAll(".fpill").forEach(b=>b.classList.remove("on"));el.classList.add("on");st.filter=f;render();}
function toggleSettings(){const d=document.getElementById("settingsDrawer");const a=document.getElementById("settingsArrow");d.classList.toggle("open");a.innerHTML=d.classList.contains("open")?"&#9650;":"&#9660;";}
async function doScan(){
  const btn=document.getElementById("scanBtn");
  btn.textContent="...";btn.classList.add("loading");
  document.getElementById("errorBanner").style.display="none";
  try{
    const clust=parseFloat(document.getElementById("clustV").textContent);
    const bbm=document.getElementById("bbMv").textContent;
    const kcm=document.getElementById("kcMv").textContent;
    const res=await fetch(`/api/scan/${st.tf}?clust=${clust}&bbm=${bbm}&kcm=${kcm}`);
    if(!res.ok) throw new Error("HTTP "+res.status);
    const data=await res.json();
    const s=getSetts();
    st.data=data.map(r=>{
      const isFired=r.volSqz==="fire"||r.smaSqz==="fire";
      // Elephant: size >= mult AND close broke outside SQZ range
      const isElephant=isFired&&r.lastBarMult>=s.elephMult&&r.confirmOutside;
      // Tail: same size check + wick ratio + wick pierced range (confirmOutside includes wick pierce)
      const isTail=isFired&&r.lastBarMult>=s.elephMult&&r.lastWickRatio>=s.tailRatio&&r.confirmOutside;
      const bias=r.chg>1.8?"long":r.chg<-1.8?"short":"flat";
      const fireAge=r.fireBarAgo>=0?r.fireBarAgo:-1;
      return{...r,isElephant,isTail,isFired,confirmType:isTail?"tail":isElephant?"elephant":null,bias,fireAge};
    });
    render();
    document.getElementById("loadingScreen").style.display="none";
  }catch(e){
    document.getElementById("errorBanner").style.display="block";
    document.getElementById("loadingScreen").style.display="none";
  }
  btn.innerHTML="&#8635; SCAN";btn.classList.remove("loading");
}
function dotEl(v){const cls=v==="fire"?"d-fire":v==="on"?"d-on":v==="forming"?"d-forming":"d-none";const lbl=v==="fire"?"FIRE":v==="on"?"ON":v==="forming"?"FORMING":"&mdash;";return`<span class="dot ${cls}"></span> ${lbl}`;}
function render(){
  const s=getSetts();
  st.data=st.data.map(r=>{
    const isFiredR=r.volSqz==="fire"||r.smaSqz==="fire";
    const isElephant=isFiredR&&r.lastBarMult>=s.elephMult&&r.confirmOutside;
    const isTail=isFiredR&&r.lastBarMult>=s.elephMult&&r.lastWickRatio>=s.tailRatio&&r.confirmOutside;
    return{...r,isElephant,isTail,isFired:isFiredR,confirmType:isTail?"tail":isElephant?"elephant":null};
  });
  let rows=[...st.data];
  if(st.filter==="forming")  rows=rows.filter(r=>r.volSqz==="forming"||r.smaSqz==="forming");
  if(st.filter==="vol")      rows=rows.filter(r=>r.volSqz!=="none");
  if(st.filter==="sma")      rows=rows.filter(r=>r.smaSqz!=="none");
  if(st.filter==="both")     rows=rows.filter(r=>r.both);
  if(st.filter==="confirmed")  rows=rows.filter(r=>r.confirmType);
  if(st.filter==="no_confirm") rows=rows.filter(r=>(r.volSqz==="fire"||r.smaSqz==="fire")&&!r.confirmType);
  if(st.filter==="elephant") rows=rows.filter(r=>r.isElephant);
  if(st.filter==="tail")     rows=rows.filter(r=>r.isTail);
  if(st.filter==="long")     rows=rows.filter(r=>r.bias==="long");
  if(st.filter==="short")    rows=rows.filter(r=>r.bias==="short");
  if(st.filter==="neg_fund") rows=rows.filter(r=>r.funding<0);
  const bothFire =st.data.filter(r=>r.bothFire).length;
  const confirmed=st.data.filter(r=>r.confirmType).length;
  const volAct   =st.data.filter(r=>r.volSqz!=="none").length;
  const negF     =st.data.filter(r=>r.funding<0).length;
  document.getElementById("statsBar").innerHTML=`<div class="stat"><div class="stat-n" style="color:#f97316">${bothFire}</div><div class="stat-l">BOTH FIRE</div></div><div class="stat"><div class="stat-n" style="color:#22c55e">${confirmed}</div><div class="stat-l">CONFIRMED</div></div><div class="stat"><div class="stat-n" style="color:#a855f7">${volAct}</div><div class="stat-l">VOL SQZ</div></div><div class="stat"><div class="stat-n" style="color:#ef4444">${negF}</div><div class="stat-l">NEG FUND</div></div>`;
  if(!rows.length){document.getElementById("cards").innerHTML=`<div class="empty">No pairs match this filter</div>`;return;}
  document.getElementById("cards").innerHTML=rows.map(r=>{
    const chgCls=r.chg>0?"up":r.chg<0?"dn":"neu";
    const chgStr=(r.chg>0?"+":"")+r.chg.toFixed(2)+"%";
    const bCls=r.bias==="long"?"b-long":r.bias==="short"?"b-short":"b-flat";
    const bTxt=r.bias==="long"?"LONG":r.bias==="short"?"SHORT":"FLAT";
    const fundCls=r.funding<0?"fund-neg":"fund-pos";
    const fundStr=(r.funding>0?"+":"")+r.funding.toFixed(4)+"%";
    const star=r.bothFire?`<span class="star">&#9733;</span>`:"";
    const vBox=r.volSqz==="fire"?"active-fire":r.volSqz==="on"?"active-on":r.volSqz==="forming"?"active-forming":"";
    const sBox=r.smaSqz==="fire"?"active-fire":r.smaSqz==="on"?"active-on":r.smaSqz==="forming"?"active-forming":"";
    let cardCls="card";
    if(r.bothFire)cardCls+=" both-fire";
    else if(isFired&&r.confirmType)cardCls+=" vol-fire";
    else if(isFired&&!r.confirmType)cardCls+=" fire-no-confirm";
    else if(r.volSqz==="on"||r.smaSqz==="on")cardCls+=" vol-fire";
    else if(r.volSqz==="forming"||r.smaSqz==="forming")cardCls+=" forming";
    let banner="";
    const isForming=r.volSqz==="forming"||r.smaSqz==="forming";
    const isFired=r.volSqz==="fire"||r.smaSqz==="fire";
    const dirArrow=r.breakoutDir==="up"?"▲":r.breakoutDir==="down"?"▼":"";
    const dirCol=r.breakoutDir==="up"?"var(--bull)":r.breakoutDir==="down"?"var(--bear)":"var(--muted)";
    const fireAgeStr=r.fireAge===0?"just now":r.fireAge===1?"1 bar ago":r.fireAge>1?`${r.fireAge} bars ago`:"";
    if(r.isTail){
      // CONFIRMED — tail wick pierced outside SQZ range, size checks pass
      banner=`<div class="breakout-banner bb-tail"><span style="color:${dirCol}">${dirArrow}</span> TAIL BAR &mdash; OUTSIDE SQZ RANGE &mdash; ENTER &bull; ${r.lastBarMult.toFixed(1)}x &bull; ${fireAgeStr}</div>`;
    } else if(r.isElephant){
      // CONFIRMED — closed outside SQZ range with big body
      banner=`<div class="breakout-banner bb-elephant"><span style="color:${dirCol}">${dirArrow}</span> ELEPHANT BAR &mdash; OUTSIDE SQZ RANGE &mdash; ENTER &bull; ${r.lastBarMult.toFixed(1)}x &bull; ${fireAgeStr}</div>`;
    } else if(r.isFired&&r.lastBarMult>=1&&!r.confirmOutside){
      // Big bar but STAYED INSIDE the SQZ range — not valid, do not enter
      banner=`<div class="breakout-banner" style="background:rgba(239,68,68,0.08);color:var(--bear);border:0.5px solid var(--bear)"><span style="color:${dirCol}">${dirArrow}</span> BIG BAR BUT INSIDE SQZ RANGE &mdash; NO ENTRY &bull; ${fireAgeStr}</div>`;
    } else if(r.isFired){
      // SQZ ended — waiting for a bar that breaks outside the range
      banner=`<div class="breakout-banner" style="background:rgba(239,68,68,0.08);color:var(--bear);border:0.5px solid var(--bear)"><span style="color:${dirCol}">${dirArrow}</span> SQZ ENDED &mdash; WAIT FOR RANGE BREAK &bull; ${fireAgeStr}</div>`;
    } else if(isForming){
      banner=`<div class="breakout-banner" style="background:rgba(245,158,11,0.1);color:var(--warn);border:0.5px solid var(--warn)">SQZ FORMING &mdash; WATCH THIS PAIR</div>`;
    }
    const srcLbl=r.src&&r.src!=="binance"?`<span class="src-tag">${r.src.toUpperCase()}</span>`:"";
    return`<div class="${cardCls}" onclick="openModal('${r.sym}')"><div class="card-top"><div><div class="pair-name">${r.display}${star}</div><div class="pair-cat">PERP &bull; ${st.tf.toUpperCase()}</div></div><div class="price-col"><div class="price">${fmtPrice(r.price)}</div><div class="chg ${chgCls}">${chgStr}</div></div></div><div class="card-mid"><div class="sqz-box ${vBox}"><div class="sqz-type">VOL SQZ (BB/KC)</div><div class="sqz-status">${dotEl(r.volSqz)}</div></div><div class="sqz-box ${sBox}"><div class="sqz-type">SMA SQZ (20/100)</div><div class="sqz-status">${dotEl(r.smaSqz)}</div></div></div><div class="card-bot"><div class="meta"><span class="fund-tag ${fundCls}">${fundStr}</span><span class="vol-tag">${fmtVol(r.volume)}${srcLbl}</span></div><span class="bias-badge ${bCls}">${bTxt}</span></div>${banner}</div>`;
  }).join("");
}
function openModal(sym){
  const r=st.data.find(d=>d.sym===sym);if(!r)return;
  const s=getSetts();
  const chgCls=r.chg>0?"up":r.chg<0?"dn":"neu";
  const chgStr=(r.chg>0?"+":"")+r.chg.toFixed(2)+"%";
  const star=r.bothFire?`<span class="star">&#9733;</span>`:"";
  const fundCls=r.funding<0?"fund-neg":"fund-pos";
  document.getElementById("modalHeader").innerHTML=`<div class="modal-pair">${r.display}${star}</div><div class="modal-price"><span class="${chgCls}">${fmtPrice(r.price)}</span> <span style="font-size:12px" class="${chgCls}">${chgStr}</span></div>`;
  let confirmSection="";
  if(r.confirmType){
    const type=r.isTail?"TAIL BAR":"ELEPHANT BAR";
    const color=r.isTail?"var(--blue)":"var(--orange)";
    const barAgoLbl=r.confirmBarAgo===0?"Current bar":r.confirmBarAgo===1?"1 bar ago":`${r.confirmBarAgo} bars ago`;
    confirmSection=`<div class="modal-section"><div class="modal-section-title">BREAKOUT CONFIRMATION</div><div class="confirm-box" style="border-color:${color}"><div class="confirm-title" style="color:${color}">${type} DETECTED &mdash; ENTER NOW</div><div class="confirm-row"><span class="confirm-lbl">Bar size vs SQZ candles</span><span class="confirm-val" style="color:${color}">${r.lastBarMult.toFixed(2)}x</span></div><div class="confirm-row"><span class="confirm-lbl">Your required mult</span><span class="confirm-val">${s.elephMult.toFixed(1)}x</span></div>${r.isTail?`<div class="confirm-row"><span class="confirm-lbl">Wick ratio</span><span class="confirm-val">${(r.lastWickRatio*100).toFixed(0)}% (min ${(s.tailRatio*100).toFixed(0)}%)</span></div>`:""}<div class="confirm-row"><span class="confirm-lbl">Confirm bar appeared</span><span class="confirm-val" style="color:${color}">${barAgoLbl}</span></div><div class="confirm-row"><span class="confirm-lbl">SQZ lasted</span><span class="confirm-val">${r.sqzBarsCount} candles</span></div></div></div>`;
  } else if(r.volSqz!=="none"||r.smaSqz!=="none"){
    const fireStr=r.fireAge>=0?(r.fireAge===0?"This candle":r.fireAge+" candles ago"):"Unknown";
  confirmSection=`<div class="modal-section"><div class="modal-section-title">BREAKOUT CONFIRMATION</div><div class="confirm-box" style="border-color:var(--bear)"><div class="confirm-title" style="color:var(--bear)">SQZ ENDED &mdash; NO CONFIRMING BAR YET</div><div class="confirm-row"><span class="confirm-lbl">Do NOT enter</span><span class="confirm-val" style="color:var(--bear)">Wait for elephant/tail</span></div><div class="confirm-row"><span class="confirm-lbl">SQZ ended</span><span class="confirm-val">${fireStr}</span></div><div class="confirm-row"><span class="confirm-lbl">Breakout direction</span><span class="confirm-val" style="color:${r.breakoutDir==="up"?"var(--bull)":r.breakoutDir==="down"?"var(--bear)":"var(--muted)"}">${r.breakoutDir==="up"?"▲ UP":r.breakoutDir==="down"?"▼ DOWN":"&mdash;"}</span></div><div class="confirm-row"><span class="confirm-lbl">SQZ lasted</span><span class="confirm-val">${r.sqzBarsCount} candles</span></div><div class="confirm-row"><span class="confirm-lbl">Need bar size &ge;</span><span class="confirm-val">${s.elephMult.toFixed(1)}x avg SQZ candle</span></div><div class="confirm-row"><span class="confirm-lbl">Tail wick min</span><span class="confirm-val">${(s.tailRatio*100).toFixed(0)}% of bar</span></div></div></div>`;
  }
  document.getElementById("modalBody").innerHTML=`<div class="modal-section"><div class="modal-section-title">SQUEEZE STATUS</div><div class="detail-grid"><div class="detail-card" style="${r.volSqz!=="none"?"border-color:var(--purple)":""}"><div class="detail-lbl">VOL SQZ (BB/KC)</div><div class="detail-val" style="color:${r.volSqz==="fire"?"var(--bull)":r.volSqz==="on"?"var(--purple)":"var(--muted)"}">${r.volSqz==="fire"?"FIRE":r.volSqz==="on"?"ON":"&mdash;"}</div></div><div class="detail-card" style="${r.smaSqz!=="none"?"border-color:var(--purple)":""}"><div class="detail-lbl">SMA SQZ (20/100)</div><div class="detail-val" style="color:${r.smaSqz==="fire"?"var(--bull)":r.smaSqz==="on"?"var(--purple)":"var(--muted)"}">${r.smaSqz==="fire"?"FIRE":r.smaSqz==="on"?"ON":"&mdash;"}</div></div><div class="detail-card"><div class="detail-lbl">SMA cluster range</div><div class="detail-val" style="color:${r.smaRange<1?"var(--bull)":"var(--text)"}">${r.smaRange}%</div></div><div class="detail-card"><div class="detail-lbl">SQZ range HIGH</div><div class="detail-val" style="color:var(--bear)">${r.sqzHigh?fmtPrice(r.sqzHigh):"&mdash;"}</div></div><div class="detail-card"><div class="detail-lbl">SQZ range LOW</div><div class="detail-val" style="color:var(--bull)">${r.sqzLow?fmtPrice(r.sqzLow):"&mdash;"}</div></div><div class="detail-card"><div class="detail-lbl">SQZ lasted</div><div class="detail-val">${r.sqzBarsCount} candles</div></div><div class="detail-card"><div class="detail-lbl">SQZ ended</div><div class="detail-val">${r.fireAge>=0?(r.fireAge===0?"This bar":r.fireAge+" bars ago"):"Still active"}</div></div><div class="detail-card"><div class="detail-lbl">Breakout dir</div><div class="detail-val" style="color:${r.breakoutDir==='up'?'var(--bull)':r.breakoutDir==='down'?'var(--bear)':'var(--muted)'}">${r.breakoutDir==='up'?'&#9650; UP':r.breakoutDir==='down'?'&#9660; DOWN':'&mdash;'}</div></div></div></div><div class="modal-section"><div class="modal-section-title">LIVE MARKET DATA</div><div class="detail-grid"><div class="detail-card"><div class="detail-lbl">SMA20</div><div class="detail-val">${fmtPrice(r.sma20)}</div></div><div class="detail-card"><div class="detail-lbl">SMA100</div><div class="detail-val">${fmtPrice(r.sma100)}</div></div><div class="detail-card"><div class="detail-lbl">Funding rate</div><div class="detail-val ${fundCls}">${(r.funding>0?"+":"")+r.funding.toFixed(4)+"%"}</div></div><div class="detail-card"><div class="detail-lbl">Data source</div><div class="detail-val" style="text-transform:uppercase">${r.src||"binance"}</div></div></div></div>${confirmSection}<div class="modal-section"><div class="rule-box"><strong>SMA SQZ:</strong> Price, SMA20 &amp; SMA100 all clustered together.<br><strong>CONFIRM:</strong> Elephant bar (big body) or Tail bar (long wick) &mdash; must be <strong>${s.elephMult.toFixed(1)}x</strong> the SQZ candles.<br><strong>ACTION:</strong> Enter immediately on confirmation.</div></div>`;
  document.getElementById("modalBg").classList.add("open");
}
function closeModal(e){if(e.target===document.getElementById("modalBg"))closeModalBtn();}
function closeModalBtn(){document.getElementById("modalBg").classList.remove("open");}
doScan();
setInterval(doScan,60000);
</script>
</body>
</html>"""

@app.route('/')
def index():
    return render_template_string(HTML)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
