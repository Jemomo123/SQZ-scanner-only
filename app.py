from flask import Flask, render_template_string, jsonify, request
import os, requests, math

app = Flask(__name__)

# ── Pairs ─────────────────────────────────────────────────────────────────────
PAIRS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","AVAXUSDT","LINKUSDT","ARBUSDT","OPUSDT",
    "SUIUSDT","INJUSDT","WIFUSDT","PEPEUSDT","TONUSDT",
    "NEARUSDT","APTUSDT","TIAUSDT","ORDIUSDT","SEIUSDT",
    "1000BONKUSDT","WUSDT","PYTHUSDT","JTOUSDT",
]

TF_MAP   = {"3m":"3m","5m":"5m","15m":"15m","1h":"1h","4h":"4h","1d":"1d"}
BYBIT_TF = {"3m":"3","5m":"5","15m":"15","1h":"60","4h":"240","1d":"D"}
OKX_TF   = {"3m":"3m","5m":"5m","15m":"15m","1h":"1H","4h":"4H","1d":"1Dutc"}

# ── Fetch helpers ─────────────────────────────────────────────────────────────

def fetch(url, params=None, timeout=8):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None

def get_closed_klines(symbol, interval, limit=220):
    """
    Returns list of CLOSED candles as dicts {o,h,l,c}.
    Last candle is always closed — open (live) candle is dropped.
    Tries Binance → Bybit → OKX.
    """
    # ── Binance ──
    data = fetch("https://fapi.binance.com/fapi/v1/klines",
                 {"symbol": symbol, "interval": interval, "limit": limit})
    if data and len(data) >= 3:
        # Binance: last row is the live open candle — drop it
        rows = data[:-1]
        return [{"o":float(k[1]),"h":float(k[2]),"l":float(k[3]),"c":float(k[4])} for k in rows]

    # ── Bybit ──
    tf   = BYBIT_TF.get(interval, "15")
    data = fetch("https://api.bybit.com/v5/market/kline",
                 {"category":"linear","symbol":symbol,"interval":tf,"limit":limit})
    if data:
        rows = data.get("result",{}).get("list",[])
        if rows and len(rows) >= 3:
            rows = list(reversed(rows))[:-1]   # oldest→newest, drop last (open)
            return [{"o":float(k[1]),"h":float(k[2]),"l":float(k[3]),"c":float(k[4])} for k in rows]

    # ── OKX ──
    sym  = symbol.replace("1000BONKUSDT","BONK-USDT-SWAP").replace("USDT","-USDT-SWAP")
    tf   = OKX_TF.get(interval,"15m")
    data = fetch("https://www.okx.com/api/v5/market/candles",
                 {"instId":sym,"bar":tf,"limit":limit})
    if data:
        rows = data.get("data",[])
        if rows and len(rows) >= 3:
            rows = list(reversed(rows))[:-1]
            return [{"o":float(k[1]),"h":float(k[2]),"l":float(k[3]),"c":float(k[4])} for k in rows]

    return []

def get_live_prices():
    """Returns {symbol: {price, chg, volume}} from Binance futures ticker."""
    data = fetch("https://fapi.binance.com/fapi/v1/ticker/24hr")
    if not data:
        data = fetch("https://api.bybit.com/v5/market/tickers",{"category":"linear"})
        if data:
            items = data.get("result",{}).get("list",[])
            return {d["symbol"]:{"price":float(d["lastPrice"]),
                                 "chg":float(d["price24hPcnt"])*100,
                                 "volume":float(d["turnover24h"])} for d in items}
        return {}
    return {d["symbol"]:{"price":float(d["lastPrice"]),
                         "chg":float(d["priceChangePercent"]),
                         "volume":float(d["quoteVolume"])} for d in data}

def get_funding():
    data = fetch("https://fapi.binance.com/fapi/v1/premiumIndex")
    if not data: return {}
    return {d["symbol"]: float(d["lastFundingRate"]) for d in data}

# ── Math ──────────────────────────────────────────────────────────────────────

def calc_sma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n

def calc_ema(closes, n):
    if len(closes) < n:
        return None
    k = 2.0 / (n + 1)
    val = sum(closes[:n]) / n
    for c in closes[n:]:
        val = c * k + val * (1 - k)
    return val

def calc_rma(values, n):
    """Wilder's RMA — used for ATR in Keltner Channel."""
    if len(values) < n:
        return None
    k = 1.0 / n
    val = sum(values[:n]) / n
    for v in values[n:]:
        val = v * k + val * (1 - k)
    return val

def calc_stdev(closes, n):
    s = calc_sma(closes, n)
    if s is None:
        return None
    return math.sqrt(sum((c - s)**2 for c in closes[-n:]) / n)

def calc_atr(klines, n):
    """True range series then RMA."""
    if len(klines) < n + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i]["h"], klines[i]["l"], klines[i-1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return calc_rma(trs, n)

# ── SQZ Detection ─────────────────────────────────────────────────────────────

def check_sma_sqz(closes, clust_pct):
    """
    SMA SQZ: price, SMA20, SMA100, SMA200 all within clust_pct of each other.
    All four must converge — as seen on the user's exchange chart.
    Returns full breakdown dict so the user can verify against their chart.
    """
    result = {
        "state": "none",
        "price": closes[-1],
        "sma20": None,
        "sma100": None,
        "sma200": None,
        "gap_price_sma20":   None,
        "gap_price_sma100":  None,
        "gap_price_sma200":  None,
        "gap_sma20_sma100":  None,
        "gap_sma20_sma200":  None,
        "gap_sma100_sma200": None,
        "max_gap":   None,
        "threshold": round(clust_pct * 100, 3),
        "reason": "",
    }

    if len(closes) < 201:
        result["reason"] = f"Not enough candles (have {len(closes)}, need 201 for SMA200)"
        return result

    price  = closes[-1]
    sma20  = calc_sma(closes, 20)
    sma100 = calc_sma(closes, 100)
    sma200 = calc_sma(closes, 200)

    if not all([sma20, sma100, sma200]):
        result["reason"] = "Could not compute one or more SMAs"
        return result

    g1 = abs(price  - sma20)  / price * 100
    g2 = abs(price  - sma100) / price * 100
    g3 = abs(price  - sma200) / price * 100
    g4 = abs(sma20  - sma100) / price * 100
    g5 = abs(sma20  - sma200) / price * 100
    g6 = abs(sma100 - sma200) / price * 100
    max_gap = max(g1, g2, g3, g4, g5, g6)

    result.update({
        "sma20":  round(sma20,  6),
        "sma100": round(sma100, 6),
        "sma200": round(sma200, 6),
        "gap_price_sma20":   round(g1, 4),
        "gap_price_sma100":  round(g2, 4),
        "gap_price_sma200":  round(g3, 4),
        "gap_sma20_sma100":  round(g4, 4),
        "gap_sma20_sma200":  round(g5, 4),
        "gap_sma100_sma200": round(g6, 4),
        "max_gap": round(max_gap, 4),
    })

    thresh_pct = clust_pct * 100

    def _max_gap_at(c):
        if len(c) < 201: return 999.0
        p = c[-1]
        s20 = calc_sma(c, 20); s100 = calc_sma(c, 100); s200 = calc_sma(c, 200)
        if not all([s20, s100, s200]): return 999.0
        return max(
            abs(p-s20)/p, abs(p-s100)/p, abs(p-s200)/p,
            abs(s20-s100)/p, abs(s20-s200)/p, abs(s100-s200)/p
        ) * 100

    # SQZ ON
    if max_gap <= thresh_pct:
        result["state"]  = "on"
        result["reason"] = (
            f"ALL 4 within {thresh_pct}% — max gap {round(max_gap,4)}%
"
            f"Price↔SMA20={round(g1,4)}%  Price↔SMA100={round(g2,4)}%  "
            f"Price↔SMA200={round(g3,4)}%  SMA20↔SMA100={round(g4,4)}%  "
            f"SMA20↔SMA200={round(g5,4)}%  SMA100↔SMA200={round(g6,4)}%"
        )
        return result

    # FIRE: was ON last bar, now OFF
    prev_max = _max_gap_at(closes[:-1])
    if prev_max <= thresh_pct:
        result["state"]  = "fire"
        result["reason"] = (
            f"Was in SQZ last bar (gap={round(prev_max,4)}%), "
            f"now separated (gap={round(max_gap,4)}%)"
        )
        return result

    # FORMING: gap narrowing over last 3 bars, within 5x threshold
    if max_gap <= thresh_pct * 5:
        p2_max = _max_gap_at(closes[:-1])
        p3_max = _max_gap_at(closes[:-2])
        if max_gap < p2_max < p3_max:
            result["state"]  = "forming"
            result["reason"] = (
                f"All 4 MAs converging: {round(p3_max,3)}% → {round(p2_max,3)}% → {round(max_gap,3)}% "
                f"(threshold {thresh_pct}%)"
            )
            return result

    result["reason"] = (
        f"Max gap {round(max_gap,4)}% exceeds threshold {thresh_pct}% — no SQZ
"
        f"Worst pair: "
        f"{'Price↔SMA20' if g1==max_gap else 'Price↔SMA100' if g2==max_gap else 'Price↔SMA200' if g3==max_gap else 'SMA20↔SMA100' if g4==max_gap else 'SMA20↔SMA200' if g5==max_gap else 'SMA100↔SMA200'}"
    )
    return result


def check_vol_sqz(klines, bb_len=20, bb_mult=2.0, kc_len=20, kc_mult=1.5):
    """
    Vol SQZ: BB (SMA+stdev) inside KC (EMA+RMA-ATR).
    Returns dict with full breakdown.
    """
    closes = [k["c"] for k in klines]
    result = {
        "state": "none",
        "bb_width": None,
        "kc_width": None,
        "bb_upper": None, "bb_lower": None,
        "kc_upper": None, "kc_lower": None,
        "reason": "",
    }

    if len(klines) < bb_len + 2:
        result["reason"] = f"Not enough candles (have {len(klines)}, need {bb_len+2})"
        return result

    def _state(k_sl):
        c_sl  = [k["c"] for k in k_sl]
        bb_b  = calc_sma(c_sl, bb_len)
        bb_d  = calc_stdev(c_sl, bb_len)
        kc_b  = calc_ema(c_sl, kc_len)
        kc_a  = calc_atr(k_sl, kc_len)
        if not all([bb_b, bb_d, kc_b, kc_a]):
            return False, None, None, None, None, None, None
        bb_u = bb_b + bb_mult * bb_d
        bb_l = bb_b - bb_mult * bb_d
        kc_u = kc_b + kc_mult * kc_a
        kc_l = kc_b - kc_mult * kc_a
        squeezed = bb_u < kc_u and bb_l > kc_l
        return squeezed, round(bb_u-bb_l, 6), round(kc_u-kc_l, 6), round(bb_u,6), round(bb_l,6), round(kc_u,6), round(kc_l,6)

    cur_sq, bb_w, kc_w, bb_u, bb_l, kc_u, kc_l = _state(klines)
    result["bb_width"] = bb_w
    result["kc_width"] = kc_w
    result["bb_upper"] = bb_u; result["bb_lower"] = bb_l
    result["kc_upper"] = kc_u; result["kc_lower"] = kc_l

    if cur_sq:
        result["state"]  = "on"
        result["reason"] = f"BB width {bb_w} inside KC width {kc_w}"
        return result

    prev_sq, *_ = _state(klines[:-1])
    if prev_sq:
        result["state"]  = "fire"
        result["reason"] = f"Was squeezed last bar, now BB broke outside KC"
        return result

    # FORMING: BB/KC ratio shrinking
    if len(klines) >= kc_len + 4 and bb_w and kc_w:
        ratio_cur  = bb_w / kc_w if kc_w else 1
        _, bb_w2, kc_w2, *_ = _state(klines[:-1])
        _, bb_w3, kc_w3, *_ = _state(klines[:-2])
        if bb_w2 and kc_w2 and bb_w3 and kc_w3:
            ratio_p2 = bb_w2 / kc_w2
            ratio_p3 = bb_w3 / kc_w3
            if ratio_cur < ratio_p2 < ratio_p3 and ratio_cur < 1.2:
                result["state"]  = "forming"
                result["reason"] = f"BB/KC ratio narrowing: {round(ratio_p3,3)} → {round(ratio_p2,3)} → {round(ratio_cur,3)}"
                return result

    result["reason"] = f"BB width {bb_w} outside KC width {kc_w} — no squeeze"
    return result


def find_sqz_range(klines, closes, sma_info, vol_info, clust_pct):
    """
    Walk back to find the squeeze block, compute:
    - sqz_high / sqz_low (price range of squeeze candles)
    - sqz_avg_body (avg candle body size during squeeze)
    - sqz_bar_count
    Then scan forward for elephant/tail bar that closes outside the range.
    """
    n = len(closes)
    sqz_start = None
    sqz_end   = None

    # Walk back up to 60 bars to find the squeeze block
    for i in range(n - 1, max(n - 62, 100), -1):
        c_sl = closes[:i+1]
        k_sl = klines[:i+1]

        # SMA state at bar i
        if len(c_sl) >= 101:
            p = c_sl[-1]
            s20  = calc_sma(c_sl, 20)
            s100 = calc_sma(c_sl, 100)
            if s20 and s100:
                g = max(abs(p-s20), abs(p-s100), abs(s20-s100)) / p * 100
                sma_on = g <= clust_pct * 100
            else:
                sma_on = False
        else:
            sma_on = False

        # Vol state at bar i (lightweight check)
        if len(k_sl) >= 22:
            c2 = [k["c"] for k in k_sl]
            bb_b = calc_sma(c2, 20); bb_d = calc_stdev(c2, 20)
            kc_b = calc_ema(c2, 20); kc_a = calc_atr(k_sl, 20)
            if all([bb_b, bb_d, kc_b, kc_a]):
                bb_u = bb_b + 2*bb_d; bb_l = bb_b - 2*bb_d
                kc_u = kc_b + 1.5*kc_a; kc_l = kc_b - 1.5*kc_a
                vol_on = bb_u < kc_u and bb_l > kc_l
            else:
                vol_on = False
        else:
            vol_on = False

        in_sqz = sma_on or vol_on
        if in_sqz:
            if sqz_end is None:
                sqz_end = i
            sqz_start = i
        else:
            if sqz_end is not None:
                break   # found the block

    if sqz_start is None or sqz_end is None:
        return {"sqzBars":0,"sqzHigh":0,"sqzLow":0,"sqzAvgBody":0,
                "confirmBarAgo":-1,"confirmMult":0,"confirmWick":0,
                "confirmOutside":False,"confirmType":"none","breakoutDir":"none"}

    sqz_bars    = sqz_end - sqz_start + 1
    sqz_highs   = [klines[i]["h"] for i in range(sqz_start, sqz_end+1)]
    sqz_lows    = [klines[i]["l"] for i in range(sqz_start, sqz_end+1)]
    sqz_bodies  = [abs(closes[i] - klines[i]["o"]) for i in range(sqz_start, sqz_end+1)]
    sqz_high    = max(sqz_highs)
    sqz_low     = min(sqz_lows)
    sqz_avg     = sum(sqz_bodies) / len(sqz_bodies) if sqz_bodies else 0

    # Breakout direction: which side did first post-SQZ bar close on
    breakout_dir = "none"
    if sqz_end + 1 < n:
        first_post = closes[sqz_end + 1]
        if first_post > sqz_high:
            breakout_dir = "up"
        elif first_post < sqz_low:
            breakout_dir = "down"
        else:
            breakout_dir = "inside"

    # Forward scan: look for elephant or tail bar in up to 5 post-SQZ bars
    best_mult = 0; best_wick = 0; confirm_ago = -1
    confirm_outside = False; confirm_type = "none"

    if sqz_avg > 0:
        for j in range(sqz_end + 1, min(n, sqz_end + 6)):
            body  = abs(closes[j] - klines[j]["o"])
            rng   = klines[j]["h"] - klines[j]["l"]
            wick  = (rng - body) / rng if rng > 0 else 0
            mult  = body / sqz_avg
            close_out = closes[j] > sqz_high or closes[j] < sqz_low
            wick_out  = klines[j]["h"] > sqz_high or klines[j]["l"] < sqz_low
            outside   = close_out or wick_out
            if mult > best_mult and outside:
                best_mult = mult; best_wick = wick
                confirm_ago = (n - 1) - j
                confirm_outside = close_out
                confirm_type = "tail" if (wick >= 0.6 and wick_out) else "elephant"

    return {
        "sqzBars":       sqz_bars,
        "sqzHigh":       round(sqz_high, 8),
        "sqzLow":        round(sqz_low, 8),
        "sqzAvgBody":    round(sqz_avg, 8),
        "confirmBarAgo": confirm_ago,
        "confirmMult":   round(best_mult, 2),
        "confirmWick":   round(best_wick, 2),
        "confirmOutside":confirm_outside,
        "confirmType":   confirm_type,
        "breakoutDir":   breakout_dir,
    }


# ── API ───────────────────────────────────────────────────────────────────────

@app.route('/api/scan/<tf>')
def scan(tf):
    interval  = TF_MAP.get(tf, "15m")
    try:    clust_pct = float(request.args.get("clust","0.1")) / 100.0
    except: clust_pct = 0.001
    try:    bb_mult = float(request.args.get("bbm","2.0"))
    except: bb_mult = 2.0
    try:    kc_mult = float(request.args.get("kcm","1.5"))
    except: kc_mult = 1.5

    prices   = get_live_prices()
    fundings = get_funding()
    out      = []

    for sym in PAIRS:
        t = prices.get(sym)
        if not t:
            continue

        klines = get_closed_klines(sym, interval)
        if len(klines) < 105:
            continue

        # Append live price as synthetic last bar so SMAs match your chart
        live = t["price"]
        klines_live = klines + [{"o":live,"h":live,"l":live,"c":live}]
        closes_live = [k["c"] for k in klines_live]

        sma_info = check_sma_sqz(closes_live, clust_pct)
        vol_info = check_vol_sqz(klines_live, bb_mult=bb_mult, kc_mult=kc_mult)

        sma_state = sma_info["state"]
        vol_state = vol_info["state"]

        # Only include if at least one SQZ is active (on/forming/fire)
        if sma_state == "none" and vol_state == "none":
            continue

        sqz_range = find_sqz_range(klines_live, closes_live, sma_info, vol_info, clust_pct)

        display = sym.replace("1000BONKUSDT","BONK/USDT").replace("USDT","/USDT")
        funding = round(fundings.get(sym, 0.0) * 100, 4)

        out.append({
            "sym": sym, "display": display,
            "price": live, "chg": round(t["chg"],2), "volume": t["volume"],
            "funding": funding,
            # SQZ states
            "smaState": sma_state,
            "volState": vol_state,
            "bothOn":   sma_state == "on"   and vol_state == "on",
            "bothFire": sma_state == "fire"  and vol_state == "fire",
            # Full breakdowns for the detail tap
            "sma": sma_info,
            "vol": vol_info,
            # Range + confirmation
            "sqzBars":       sqz_range["sqzBars"],
            "sqzHigh":       sqz_range["sqzHigh"],
            "sqzLow":        sqz_range["sqzLow"],
            "sqzAvgBody":    sqz_range["sqzAvgBody"],
            "confirmBarAgo": sqz_range["confirmBarAgo"],
            "confirmMult":   sqz_range["confirmMult"],
            "confirmWick":   sqz_range["confirmWick"],
            "confirmOutside":sqz_range["confirmOutside"],
            "confirmType":   sqz_range["confirmType"],
            "breakoutDir":   sqz_range["breakoutDir"],
        })

    # Sort: fire first, then on, then forming. Within each group by smaRange asc
    order = {"fire":0,"on":1,"forming":2,"none":3}
    out.sort(key=lambda r: (
        min(order.get(r["smaState"],3), order.get(r["volState"],3)),
        r["sma"].get("max_gap") or 999
    ))

    return jsonify(out)


@app.route('/api/debug/<sym>/<tf>')
def debug(sym, tf):
    """Full diagnostic for one pair — used by the detail modal."""
    interval = TF_MAP.get(tf, "15m")
    try:    clust_pct = float(request.args.get("clust","0.1")) / 100.0
    except: clust_pct = 0.001

    prices = get_live_prices()
    t      = prices.get(sym)
    if not t:
        return jsonify({"error": f"No ticker data for {sym}"})

    klines = get_closed_klines(sym, interval)
    if len(klines) < 105:
        return jsonify({"error": f"Only {len(klines)} closed candles — need 105+"})

    live = t["price"]
    klines_live = klines + [{"o":live,"h":live,"l":live,"c":live}]
    closes_live = [k["c"] for k in klines_live]

    sma_info = check_sma_sqz(closes_live, clust_pct)
    vol_info = check_vol_sqz(klines_live)
    sqz_range = find_sqz_range(klines_live, closes_live, sma_info, vol_info, clust_pct)

    return jsonify({
        "sym": sym, "tf": tf, "live_price": live,
        "candles_used": len(klines_live),
        "sma_sqz": sma_info,
        "vol_sqz": vol_info,
        "sqz_range": sqz_range,
    })


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="theme-color" content="#0a0c11"/>
<title>SQZ Scanner</title>
<style>
:root{
  --bg:#0a0c11;--s1:#111318;--s2:#181c24;--s3:#222736;--border:#252b3a;
  --text:#e2e8f0;--muted:#56637a;--dim:#1e2330;
  --bull:#22c55e;--bear:#ef4444;--warn:#f59e0b;
  --purple:#a855f7;--orange:#f97316;--blue:#3b82f6;
  --bull-bg:rgba(34,197,94,.09);--bear-bg:rgba(239,68,68,.09);
  --purple-bg:rgba(168,85,247,.10);--warn-bg:rgba(245,158,11,.10);
  --orange-bg:rgba(249,115,22,.12);
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:'SF Mono','Fira Code',monospace}
body{display:flex;flex-direction:column;overflow:hidden}

/* TOP BAR */
.topbar{background:var(--s1);border-bottom:1px solid var(--border);padding:10px 14px 8px;flex-shrink:0}
.row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.brand{font-size:15px;font-weight:700;letter-spacing:2px;color:var(--text)}
.brand b{color:var(--orange)}
.live{display:flex;align-items:center;gap:5px;font-size:10px;color:var(--bull);letter-spacing:1px}
.dot-live{width:6px;height:6px;border-radius:50%;background:var(--bull);animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
.scan-btn{background:var(--orange);color:#fff;border:none;padding:6px 18px;border-radius:5px;font-size:12px;font-weight:700;font-family:inherit;letter-spacing:1px;cursor:pointer;flex-shrink:0}
.scan-btn:active{opacity:.7}
.scan-btn.busy{background:var(--s3);color:var(--muted)}
.tf-row{display:flex;gap:4px;margin-top:8px}
.tf{flex:1;padding:5px 0;font-size:11px;font-weight:700;letter-spacing:.8px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:inherit;cursor:pointer}
.tf.on{background:var(--s3);color:var(--text);border-color:var(--muted)}

/* SETTINGS */
.settings-toggle{display:flex;align-items:center;justify-content:space-between;padding:7px 14px;background:var(--s2);border-bottom:1px solid var(--border);cursor:pointer;font-size:10px;letter-spacing:1px;color:var(--muted);flex-shrink:0}
.settings-body{display:none;background:var(--s1);border-bottom:1px solid var(--border);padding:10px 14px;flex-shrink:0}
.settings-body.open{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.scard{background:var(--s2);border-radius:6px;padding:8px 10px}
.slbl{font-size:10px;color:var(--muted);letter-spacing:.8px;margin-bottom:4px}
.srow{display:flex;align-items:center;gap:6px}
.srow input[type=range]{flex:1;accent-color:var(--orange)}
.sval{font-size:11px;font-weight:700;color:var(--orange);min-width:34px;text-align:right}

/* STATS */
.stats{display:grid;grid-template-columns:repeat(3,1fr);border-bottom:1px solid var(--border);flex-shrink:0}
.stat{padding:8px 6px;text-align:center;border-right:1px solid var(--border)}
.stat:last-child{border-right:none}
.stat-n{font-size:20px;font-weight:700}
.stat-l{font-size:9px;color:var(--muted);letter-spacing:.5px;margin-top:1px}

/* LEGEND */
.legend{display:flex;gap:12px;padding:5px 14px;border-bottom:1px solid var(--border);overflow-x:auto;flex-shrink:0;scrollbar-width:none}
.legend::-webkit-scrollbar{display:none}
.leg{display:flex;align-items:center;gap:4px;font-size:10px;color:var(--muted);white-space:nowrap}
.sqz-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.d-on{background:var(--purple)}
.d-fire{background:var(--bull)}
.d-forming{background:var(--warn);animation:blink 1.5s infinite}
.d-none{background:var(--border)}

/* CARDS */
.cards{flex:1;overflow-y:auto;padding:8px 10px 80px;-webkit-overflow-scrolling:touch}
.empty{text-align:center;padding:48px 20px;color:var(--muted);font-size:12px;line-height:1.8}
.card{background:var(--s1);border:1px solid var(--border);border-radius:10px;margin-bottom:8px;padding:11px 13px;cursor:pointer;transition:border-color .1s}
.card:active{background:var(--s2)}
.card.state-fire{border-left:3px solid var(--bull)}
.card.state-on{border-left:3px solid var(--purple)}
.card.state-forming{border-left:3px solid var(--warn)}
.card.both-fire{border-color:var(--orange)}

.card-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:9px}
.pname{font-size:14px;font-weight:700;letter-spacing:.3px}
.pmeta{font-size:10px;color:var(--muted);margin-top:2px}
.pright{text-align:right}
.pprice{font-size:13px;font-weight:700}
.pchg{font-size:11px;margin-top:2px}
.up{color:var(--bull)}.dn{color:var(--bear)}.neu{color:var(--muted)}

.sqz-row{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:9px}
.sqz-box{background:var(--s2);border:1px solid var(--border);border-radius:6px;padding:7px 9px}
.sqz-box.box-on{border-color:var(--purple);background:var(--purple-bg)}
.sqz-box.box-fire{border-color:var(--bull);background:var(--bull-bg)}
.sqz-box.box-forming{border-color:var(--warn);background:var(--warn-bg)}
.sqz-lbl{font-size:9px;color:var(--muted);letter-spacing:.8px;margin-bottom:3px}
.sqz-state{display:flex;align-items:center;gap:5px;font-size:11px;font-weight:700}

.card-bot{display:flex;justify-content:space-between;align-items:center}
.meta{display:flex;gap:8px;align-items:center}
.fund{font-size:10px;font-weight:700;padding:2px 7px;border-radius:3px}
.fund-p{background:var(--bull-bg);color:var(--bull)}.fund-n{background:var(--bear-bg);color:var(--bear)}
.vol{font-size:10px;color:var(--muted)}
.bias{font-size:10px;font-weight:700;padding:3px 9px;border-radius:3px;letter-spacing:.6px}
.bias-l{background:var(--bull-bg);color:var(--bull)}
.bias-s{background:var(--bear-bg);color:var(--bear)}
.bias-f{background:var(--s3);color:var(--muted)}

.banner{font-size:10px;font-weight:700;letter-spacing:.7px;padding:5px 9px;border-radius:4px;margin-top:7px}
.b-confirm{background:var(--orange-bg);color:var(--orange);border:1px solid var(--orange)}
.b-noconfirm{background:var(--bear-bg);color:var(--bear);border:1px solid var(--bear)}
.b-forming{background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn)}
.star{color:var(--orange);margin-left:3px}

/* MODAL */
.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:100;align-items:flex-end}
.overlay.open{display:flex}
.sheet{background:var(--s1);border-radius:14px 14px 0 0;border-top:1px solid var(--border);width:100%;max-height:88vh;overflow-y:auto;padding-bottom:32px}
.sheet-handle{width:36px;height:4px;background:var(--border);border-radius:2px;margin:10px auto 0}
.sheet-head{padding:12px 16px 10px;border-bottom:1px solid var(--border);position:relative}
.sheet-title{font-size:18px;font-weight:700}
.sheet-sub{font-size:11px;color:var(--muted);margin-top:2px}
.sheet-close{position:absolute;right:14px;top:12px;background:var(--s3);border:none;color:var(--muted);width:28px;height:28px;border-radius:50%;font-size:16px;cursor:pointer;font-family:inherit;display:flex;align-items:center;justify-content:center}
.sheet-body{padding:14px 16px}

.section{margin-bottom:18px}
.sec-title{font-size:10px;color:var(--muted);letter-spacing:1.5px;font-weight:700;margin-bottom:8px}

.dbgrid{display:grid;grid-template-columns:1fr 1fr;gap:7px}
.dbcard{background:var(--s2);border:1px solid var(--border);border-radius:7px;padding:9px 11px}
.dblbl{font-size:10px;color:var(--muted);letter-spacing:.4px}
.dbval{font-size:13px;font-weight:700;margin-top:3px;word-break:break-all}

.verdict{border-radius:8px;padding:12px 14px;margin-bottom:8px;border:1px solid}
.verdict-on{background:var(--purple-bg);border-color:var(--purple)}
.verdict-fire{background:var(--bull-bg);border-color:var(--bull)}
.verdict-forming{background:var(--warn-bg);border-color:var(--warn)}
.verdict-none{background:var(--s2);border-color:var(--border)}
.verdict-title{font-size:12px;font-weight:700;margin-bottom:4px}
.verdict-reason{font-size:11px;color:var(--muted);line-height:1.6}

.rule-box{background:var(--purple-bg);border:1px solid var(--purple);border-radius:7px;padding:11px 13px;font-size:11px;line-height:1.7}
.rule-box b{color:var(--orange)}

.loading-screen{position:fixed;inset:0;background:var(--bg);display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:200;gap:12px}
.loading-title{font-size:15px;font-weight:700;letter-spacing:2px}
.loading-title b{color:var(--orange)}
.spinner{width:26px;height:26px;border:2px solid var(--border);border-top-color:var(--orange);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.err{background:var(--bear-bg);border:1px solid var(--bear);border-radius:6px;padding:9px 13px;margin:8px 10px;font-size:11px;color:var(--bear);display:none}
</style>
</head>
<body>

<div class="loading-screen" id="loader">
  <div class="loading-title">CRYPTO <b>SQZ</b></div>
  <div class="spinner"></div>
  <div style="font-size:10px;color:var(--muted);letter-spacing:1px">SCANNING LIVE DATA...</div>
</div>

<div class="topbar">
  <div class="row">
    <div class="brand">CRYPTO <b>SQZ</b></div>
    <div style="display:flex;align-items:center;gap:10px">
      <div class="live"><span class="dot-live"></span>LIVE</div>
      <button class="scan-btn" id="scanBtn" onclick="doScan()">&#8635; SCAN</button>
    </div>
  </div>
  <div class="tf-row">
    <button class="tf" onclick="setTF(this,'3m')">3M</button>
    <button class="tf" onclick="setTF(this,'5m')">5M</button>
    <button class="tf on" onclick="setTF(this,'15m')">15M</button>
    <button class="tf" onclick="setTF(this,'1h')">1H</button>
    <button class="tf" onclick="setTF(this,'4h')">4H</button>
    <button class="tf" onclick="setTF(this,'1d')">1D</button>
  </div>
</div>

<div class="settings-toggle" id="stoggle" onclick="toggleSettings()">
  <span>SETTINGS</span><span id="sarrow">&#9660;</span>
</div>
<div class="settings-body" id="sbody">
  <div class="scard">
    <div class="slbl">SMA CLUSTER %</div>
    <div class="srow">
      <input type="range" min="1" max="30" value="1" step="1" id="clustR" oninput="sv('clustV',(this.value/10).toFixed(1)+'%')">
      <span class="sval" id="clustV">0.1%</span>
    </div>
  </div>
  <div class="scard">
    <div class="slbl">BB MULT</div>
    <div class="srow">
      <input type="range" min="10" max="30" value="20" step="1" id="bbmR" oninput="sv('bbmV',(this.value/10).toFixed(1))">
      <span class="sval" id="bbmV">2.0</span>
    </div>
  </div>
  <div class="scard">
    <div class="slbl">KC MULT</div>
    <div class="srow">
      <input type="range" min="10" max="25" value="15" step="1" id="kcmR" oninput="sv('kcmV',(this.value/10).toFixed(1))">
      <span class="sval" id="kcmV">1.5</span>
    </div>
  </div>
  <div class="scard">
    <div class="slbl">ELEPHANT MULT</div>
    <div class="srow">
      <input type="range" min="5" max="30" value="10" step="1" id="elephR" oninput="sv('elephV',(this.value/10).toFixed(1)+'x')">
      <span class="sval" id="elephV">1.0x</span>
    </div>
  </div>
</div>

<div class="stats" id="statsBar">
  <div class="stat"><div class="stat-n" style="color:var(--bull)">-</div><div class="stat-l">FIRED</div></div>
  <div class="stat"><div class="stat-n" style="color:var(--purple)">-</div><div class="stat-l">IN SQZ</div></div>
  <div class="stat"><div class="stat-n" style="color:var(--warn)">-</div><div class="stat-l">FORMING</div></div>
</div>

<div class="legend">
  <div class="leg"><span class="sqz-dot d-fire"></span>FIRED</div>
  <div class="leg"><span class="sqz-dot d-on"></span>IN SQZ</div>
  <div class="leg"><span class="sqz-dot d-forming"></span>FORMING</div>
</div>

<div id="err" class="err">Could not reach any exchange. Check connection.</div>
<div class="cards" id="cards"></div>

<!-- DETAIL SHEET -->
<div class="overlay" id="overlay" onclick="closeSheet(event)">
  <div class="sheet" id="sheet">
    <div class="sheet-handle"></div>
    <button class="sheet-close" onclick="closeSheetBtn()">&#10005;</button>
    <div class="sheet-head" id="sheetHead"></div>
    <div class="sheet-body" id="sheetBody"></div>
  </div>
</div>

<script>
let TF='15m', DATA=[];

function sv(id,v){document.getElementById(id).textContent=v;}
function getClust(){return parseFloat(document.getElementById('clustV').textContent);}
function getBbm(){return document.getElementById('bbmV').textContent;}
function getKcm(){return document.getElementById('kcmV').textContent;}
function getEleph(){return parseFloat(document.getElementById('elephV').textContent);}

function setTF(el,tf){
  document.querySelectorAll('.tf').forEach(b=>b.classList.remove('on'));
  el.classList.add('on'); TF=tf; doScan();
}
function toggleSettings(){
  const b=document.getElementById('sbody'); b.classList.toggle('open');
  document.getElementById('sarrow').innerHTML=b.classList.contains('open')?'&#9650;':'&#9660;';
}

function fmtPrice(v){
  if(!v) return '-';
  if(v>=1000) return '$'+v.toLocaleString('en',{maximumFractionDigits:1});
  if(v>=1)    return '$'+v.toFixed(3);
  if(v>=0.0001) return '$'+v.toFixed(5);
  return '$'+v.toFixed(8);
}
function fmtVol(v){
  if(v>=1e9) return (v/1e9).toFixed(1)+'B';
  if(v>=1e6) return (v/1e6).toFixed(1)+'M';
  return (v/1e3).toFixed(0)+'K';
}

function dotHtml(state){
  const cls={fire:'d-fire',on:'d-on',forming:'d-forming',none:'d-none'}[state]||'d-none';
  const lbl={fire:'FIRE',on:'ON',forming:'FORMING',none:'&mdash;'}[state]||'&mdash;';
  return `<span class="sqz-dot ${cls}"></span> ${lbl}`;
}
function boxCls(state){return{fire:'box-fire',on:'box-on',forming:'box-forming'}[state]||'';}

async function doScan(){
  const btn=document.getElementById('scanBtn');
  btn.textContent='...'; btn.classList.add('busy');
  document.getElementById('err').style.display='none';
  try{
    const url=`/api/scan/${TF}?clust=${getClust()}&bbm=${getBbm()}&kcm=${getKcm()}`;
    const res=await fetch(url);
    if(!res.ok) throw new Error('HTTP '+res.status);
    DATA=await res.json();
    render();
    document.getElementById('loader').style.display='none';
  }catch(e){
    document.getElementById('err').style.display='block';
    document.getElementById('loader').style.display='none';
  }
  btn.innerHTML='&#8635; SCAN'; btn.classList.remove('busy');
}

function render(){
  const eleph=getEleph();
  const fired   = DATA.filter(r=>r.smaState==='fire'||r.volState==='fire').length;
  const inSqz   = DATA.filter(r=>r.smaState==='on'  ||r.volState==='on').length;
  const forming  = DATA.filter(r=>r.smaState==='forming'||r.volState==='forming').length;
  document.getElementById('statsBar').innerHTML=`
    <div class="stat"><div class="stat-n" style="color:var(--bull)">${fired}</div><div class="stat-l">FIRED</div></div>
    <div class="stat"><div class="stat-n" style="color:var(--purple)">${inSqz}</div><div class="stat-l">IN SQZ</div></div>
    <div class="stat"><div class="stat-n" style="color:var(--warn)">${forming}</div><div class="stat-l">FORMING</div></div>
  `;
  if(!DATA.length){
    document.getElementById('cards').innerHTML=`<div class="empty">No squeezes detected on ${TF.toUpperCase()}<br>Try a different timeframe or loosen the cluster threshold in Settings</div>`;
    return;
  }
  document.getElementById('cards').innerHTML=DATA.map(r=>{
    const chgCls=r.chg>0?'up':r.chg<0?'dn':'neu';
    const chgStr=(r.chg>0?'+':'')+r.chg.toFixed(2)+'%';
    const best=r.smaState==='fire'||r.volState==='fire'?'fire':
               r.smaState==='on'  ||r.volState==='on'  ?'on':'forming';
    let cardCls=`card state-${best}`;
    if(r.bothFire) cardCls+=' both-fire';
    const star=r.bothFire?`<span class="star">&#9733;</span>`:'';
    const biasCls=r.chg>1.5?'bias-l':r.chg<-1.5?'bias-s':'bias-f';
    const biasTxt=r.chg>1.5?'LONG':r.chg<-1.5?'SHORT':'FLAT';
    const fundCls=r.funding<0?'fund-n':'fund-p';
    const fundStr=(r.funding>0?'+':'')+r.funding.toFixed(4)+'%';
    const isFire=r.smaState==='fire'||r.volState==='fire';
    const confirmed=isFire&&r.confirmMult>=eleph&&r.confirmOutside;
    const dir=r.breakoutDir==='up'?'&#9650;':r.breakoutDir==='down'?'&#9660;':'';
    const dirCol=r.breakoutDir==='up'?'var(--bull)':r.breakoutDir==='down'?'var(--bear)':'var(--muted)';
    let banner='';
    if(confirmed){
      const type=r.confirmType==='tail'?'TAIL BAR':'ELEPHANT BAR';
      banner=`<div class="banner b-confirm"><span style="color:${dirCol}">${dir}</span> ${type} CONFIRMED &mdash; ENTER &bull; ${r.confirmMult.toFixed(1)}x</div>`;
    } else if(isFire){
      banner=`<div class="banner b-noconfirm"><span style="color:${dirCol}">${dir}</span> SQZ ENDED &mdash; NO CONFIRM YET &bull; WAIT</div>`;
    } else if(r.smaState==='forming'||r.volState==='forming'){
      banner=`<div class="banner b-forming">SQZ FORMING &mdash; WATCH</div>`;
    }
    return `<div class="${cardCls}" onclick="openSheet('${r.sym}')">
      <div class="card-top">
        <div><div class="pname">${r.display}${star}</div><div class="pmeta">PERP &bull; ${TF.toUpperCase()} &bull; ${r.sqzBars>0?r.sqzBars+' SQZ bars':''}</div></div>
        <div class="pright"><div class="pprice">${fmtPrice(r.price)}</div><div class="pchg ${chgCls}">${chgStr}</div></div>
      </div>
      <div class="sqz-row">
        <div class="sqz-box ${boxCls(r.volState)}">
          <div class="sqz-lbl">VOL SQZ (BB/KC)</div>
          <div class="sqz-state">${dotHtml(r.volState)}</div>
        </div>
        <div class="sqz-box ${boxCls(r.smaState)}">
          <div class="sqz-lbl">SMA SQZ (20/100)</div>
          <div class="sqz-state">${dotHtml(r.smaState)}</div>
        </div>
      </div>
      <div class="card-bot">
        <div class="meta">
          <span class="fund ${fundCls}">${fundStr}</span>
          <span class="vol">${fmtVol(r.volume)}</span>
        </div>
        <span class="bias ${biasCls}">${biasTxt}</span>
      </div>
      ${banner}
    </div>`;
  }).join('');
}

async function openSheet(sym){
  const r=DATA.find(d=>d.sym===sym); if(!r) return;
  const eleph=getEleph();
  const confirmed=((r.smaState==='fire'||r.volState==='fire'))&&r.confirmMult>=eleph&&r.confirmOutside;
  document.getElementById('sheetHead').innerHTML=`
    <div class="sheet-title">${r.display} ${r.bothFire?'<span class="star">&#9733;</span>':''}</div>
    <div class="sheet-sub">${fmtPrice(r.price)} &bull; ${r.chg>0?'+':''}${r.chg.toFixed(2)}% &bull; ${TF.toUpperCase()}</div>
  `;
  // Build SMA debug section
  const s=r.sma;
  const smaBorderColor={on:'var(--purple)',fire:'var(--bull)',forming:'var(--warn)',none:'var(--border)'}[s.state];
  const v=r.vol;
  const volBorderColor={on:'var(--purple)',fire:'var(--bull)',forming:'var(--warn)',none:'var(--border)'}[v.state];
  const dir=r.breakoutDir==='up'?'&#9650; UP':r.breakoutDir==='down'?'&#9660; DOWN':r.breakoutDir==='inside'?'Inside range':'&mdash;';
  const dirCol=r.breakoutDir==='up'?'var(--bull)':r.breakoutDir==='down'?'var(--bear)':'var(--muted)';

  document.getElementById('sheetBody').innerHTML=`

    <div class="section">
      <div class="sec-title">SMA SQZ BREAKDOWN</div>
      <div class="verdict verdict-${s.state}" style="border-color:${smaBorderColor}">
        <div class="verdict-title" style="color:${smaBorderColor}">STATE: ${s.state.toUpperCase()}</div>
        <div class="verdict-reason">${s.reason}</div>
      </div>
      <div class="dbgrid">
        <div class="dbcard"><div class="dblbl">Live price</div><div class="dbval">${fmtPrice(s.price)}</div></div>
        <div class="dbcard"><div class="dblbl">SMA20</div><div class="dbval">${s.sma20?fmtPrice(s.sma20):'&mdash;'}</div></div>
        <div class="dbcard"><div class="dblbl">SMA100</div><div class="dbval">${s.sma100?fmtPrice(s.sma100):'&mdash;'}</div></div>
        <div class="dbcard"><div class="dblbl">Threshold</div><div class="dbval">${s.threshold}%</div></div>
        <div class="dbcard" style="border-color:${s.max_gap<=s.threshold?'var(--bull)':'var(--bear)'}">
          <div class="dblbl">Max gap (worst pair)</div>
          <div class="dbval" style="color:${s.max_gap<=s.threshold?'var(--bull)':'var(--bear)'}">${s.max_gap!==null?s.max_gap+'%':'&mdash;'}</div>
        </div>
        <div class="dbcard"><div class="dblbl">Price&#8596;SMA20</div><div class="dbval">${s.gap_price_sma20!==null?s.gap_price_sma20+'%':'&mdash;'}</div></div>
        <div class="dbcard"><div class="dblbl">Price&#8596;SMA100</div><div class="dbval">${s.gap_price_sma100!==null?s.gap_price_sma100+'%':'&mdash;'}</div></div>
        <div class="dbcard"><div class="dblbl">SMA20&#8596;SMA100</div><div class="dbval">${s.gap_sma20_sma100!==null?s.gap_sma20_sma100+'%':'&mdash;'}</div></div>
      </div>
    </div>

    <div class="section">
      <div class="sec-title">VOL SQZ BREAKDOWN</div>
      <div class="verdict verdict-${v.state}" style="border-color:${volBorderColor}">
        <div class="verdict-title" style="color:${volBorderColor}">STATE: ${v.state.toUpperCase()}</div>
        <div class="verdict-reason">${v.reason}</div>
      </div>
      <div class="dbgrid">
        <div class="dbcard"><div class="dblbl">BB width</div><div class="dbval">${v.bb_width??'&mdash;'}</div></div>
        <div class="dbcard"><div class="dblbl">KC width</div><div class="dbval">${v.kc_width??'&mdash;'}</div></div>
        <div class="dbcard"><div class="dblbl">BB upper</div><div class="dbval">${v.bb_upper?fmtPrice(v.bb_upper):'&mdash;'}</div></div>
        <div class="dbcard"><div class="dblbl">BB lower</div><div class="dbval">${v.bb_lower?fmtPrice(v.bb_lower):'&mdash;'}</div></div>
        <div class="dbcard"><div class="dblbl">KC upper</div><div class="dbval">${v.kc_upper?fmtPrice(v.kc_upper):'&mdash;'}</div></div>
        <div class="dbcard"><div class="dblbl">KC lower</div><div class="dbval">${v.kc_lower?fmtPrice(v.kc_lower):'&mdash;'}</div></div>
      </div>
    </div>

    <div class="section">
      <div class="sec-title">SQZ RANGE &amp; CONFIRMATION</div>
      <div class="dbgrid">
        <div class="dbcard"><div class="dblbl">SQZ candles</div><div class="dbval">${r.sqzBars}</div></div>
        <div class="dbcard"><div class="dblbl">Breakout dir</div><div class="dbval" style="color:${dirCol}">${dir}</div></div>
        <div class="dbcard"><div class="dblbl">SQZ high</div><div class="dbval" style="color:var(--bear)">${r.sqzHigh?fmtPrice(r.sqzHigh):'&mdash;'}</div></div>
        <div class="dbcard"><div class="dblbl">SQZ low</div><div class="dbval" style="color:var(--bull)">${r.sqzLow?fmtPrice(r.sqzLow):'&mdash;'}</div></div>
        <div class="dbcard" style="border-color:${confirmed?'var(--orange)':'var(--border)'}">
          <div class="dblbl">Best post-SQZ bar</div>
          <div class="dbval" style="color:${confirmed?'var(--orange)':'var(--muted)'}">${r.confirmMult>0?r.confirmMult+'x':'&mdash;'}</div>
        </div>
        <div class="dbcard"><div class="dblbl">Broke range?</div><div class="dbval" style="color:${r.confirmOutside?'var(--bull)':'var(--bear)'}">${r.confirmOutside?'YES':'NO'}</div></div>
        <div class="dbcard"><div class="dblbl">Confirm type</div><div class="dbval">${r.confirmType!=='none'?r.confirmType.toUpperCase():'&mdash;'}</div></div>
        <div class="dbcard"><div class="dblbl">Bars ago</div><div class="dbval">${r.confirmBarAgo>=0?r.confirmBarAgo:'&mdash;'}</div></div>
      </div>
    </div>

    <div class="section">
      <div class="rule-box">
        <b>SMA SQZ:</b> Price, SMA20 &amp; SMA100 within <b>${s.threshold}%</b> of each other.<br>
        <b>VOL SQZ:</b> Bollinger Bands inside Keltner Channel.<br>
        <b>CONFIRM:</b> Elephant or tail bar &ge; <b>${eleph}x</b> avg SQZ candle, closing outside SQZ range.<br>
        <b>ENTRY:</b> Immediately on confirmation.
      </div>
    </div>
  `;
  document.getElementById('overlay').classList.add('open');
}
function closeSheet(e){if(e.target===document.getElementById('overlay'))closeSheetBtn();}
function closeSheetBtn(){document.getElementById('overlay').classList.remove('open');}

doScan();
setInterval(doScan, 60000);
</script>
</body>
</html>"""

@app.route('/')
def index():
    return render_template_string(HTML)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
