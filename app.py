from flask import Flask, render_template_string, jsonify, request
from concurrent.futures import ThreadPoolExecutor, as_completed
import os, requests, math

app = Flask(__name__)

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

# ── Fetch: Binance -> Bybit -> OKX ────────────────────────────────────────────

def get_prices():
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=8)
        if r.status_code == 200:
            return {d["symbol"]: float(d["lastPrice"]) for d in r.json()}
    except Exception:
        pass
    try:
        r = requests.get("https://api.bybit.com/v5/market/tickers",
                         params={"category":"linear"}, timeout=8)
        if r.status_code == 200:
            items = r.json().get("result",{}).get("list",[])
            return {d["symbol"]: float(d["lastPrice"]) for d in items}
    except Exception:
        pass
    return {}

def get_klines_binance(symbol, interval):
    r = requests.get("https://fapi.binance.com/fapi/v1/klines",
        params={"symbol":symbol,"interval":interval,"limit":122}, timeout=8)
    if r.status_code != 200:
        return []
    rows = r.json()[:-1]
    return [float(row[4]) for row in rows]

def get_klines_bybit(symbol, interval):
    tf = BYBIT_TF.get(interval, "15")
    r  = requests.get("https://api.bybit.com/v5/market/kline",
        params={"category":"linear","symbol":symbol,"interval":tf,"limit":122}, timeout=8)
    if r.status_code != 200:
        return []
    rows = r.json().get("result",{}).get("list",[])
    if not rows:
        return []
    rows = list(reversed(rows))[:-1]
    return [float(row[4]) for row in rows]

def get_klines_okx(symbol, interval):
    sym = symbol.replace("1000BONKUSDT","BONK-USDT-SWAP").replace("USDT","-USDT-SWAP")
    tf  = OKX_TF.get(interval, "15m")
    r   = requests.get("https://www.okx.com/api/v5/market/candles",
        params={"instId":sym,"bar":tf,"limit":122}, timeout=8)
    if r.status_code != 200:
        return []
    rows = r.json().get("data",[])
    if not rows:
        return []
    rows = list(reversed(rows))[:-1]
    return [float(row[4]) for row in rows]

def get_klines(symbol, interval):
    """Closed candle closes. Binance -> Bybit -> OKX."""
    for fn in [get_klines_binance, get_klines_bybit, get_klines_okx]:
        try:
            closes = fn(symbol, interval)
            if len(closes) >= 100:
                return closes
        except Exception:
            pass
    return []

# ── SMA ───────────────────────────────────────────────────────────────────────

def sma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n

# ── Core check ────────────────────────────────────────────────────────────────

def check(sym, interval, live_price, threshold_pct):
    closes = get_klines(sym, interval)
    if len(closes) < 100:
        return None

    # Append live price so SMA matches what the chart shows right now
    closes = closes + [live_price]

    s20  = sma(closes, 20)
    s100 = sma(closes, 100)

    if s20 is None or s100 is None:
        return None

    price = closes[-1]

    # Three gaps — all must be within threshold
    g_price_s20  = abs(price - s20)  / price * 100
    g_price_s100 = abs(price - s100) / price * 100
    g_s20_s100   = abs(s20   - s100) / price * 100
    max_gap      = max(g_price_s20, g_price_s100, g_s20_s100)

    in_sqz = max_gap <= threshold_pct

    # Check previous bar for FIRE state
    prev_closes = closes[:-1]
    ps20  = sma(prev_closes, 20)
    ps100 = sma(prev_closes, 100)
    prev_in_sqz = False
    if ps20 and ps100:
        pp = prev_closes[-1]
        prev_max = max(
            abs(pp - ps20)  / pp * 100,
            abs(pp - ps100) / pp * 100,
            abs(ps20 - ps100) / pp * 100
        )
        prev_in_sqz = prev_max <= threshold_pct

    if in_sqz:
        state = "on"
    elif prev_in_sqz:
        state = "fire"
    else:
        state = "none"

    display = sym.replace("1000BONKUSDT","BONK/USDT").replace("USDT","/USDT")

    return {
        "sym":          sym,
        "display":      display,
        "price":        price,
        "sma20":        round(s20, 8),
        "sma100":       round(s100, 8),
        "g_price_s20":  round(g_price_s20, 4),
        "g_price_s100": round(g_price_s100, 4),
        "g_s20_s100":   round(g_s20_s100, 4),
        "max_gap":      round(max_gap, 4),
        "threshold":    threshold_pct,
        "state":        state,
    }

# ── API ───────────────────────────────────────────────────────────────────────

@app.route('/api/scan/<tf>')
def scan(tf):
    interval = TF_MAP.get(tf, "15m")
    try:    threshold = float(request.args.get("t", "0.1"))
    except: threshold = 0.1

    prices = get_prices()

    def run(sym):
        live = prices.get(sym)
        if not live:
            return None
        try:
            return check(sym, interval, live, threshold)
        except Exception:
            return None

    results = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = [ex.submit(run, sym) for sym in PAIRS]
        for f in as_completed(futures, timeout=20):
            try:
                r = f.result()
                if r and r["state"] != "none":
                    results.append(r)
            except Exception:
                pass

    # Sort: fire first, then on. Within each group tightest gap first.
    order = {"fire": 0, "on": 1}
    results.sort(key=lambda r: (order.get(r["state"], 9), r["max_gap"]))

    return jsonify(results)


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="theme-color" content="#0a0c11"/>
<title>SQZ</title>
<style>
:root{
  --bg:#0a0c11;--s1:#111318;--s2:#181c24;--s3:#222736;
  --border:#252b3a;--text:#e2e8f0;--muted:#56637a;
  --bull:#22c55e;--bear:#ef4444;--warn:#f59e0b;
  --purple:#a855f7;--orange:#f97316;
  --bull-bg:rgba(34,197,94,.09);--bear-bg:rgba(239,68,68,.09);
  --purple-bg:rgba(168,85,247,.10);--orange-bg:rgba(249,115,22,.12);
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:'SF Mono','Fira Code',monospace}
body{display:flex;flex-direction:column;overflow:hidden}

.top{background:var(--s1);border-bottom:1px solid var(--border);padding:10px 14px 8px;flex-shrink:0}
.row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.brand{font-size:15px;font-weight:700;letter-spacing:2px}
.brand b{color:var(--orange)}
.live{display:flex;align-items:center;gap:5px;font-size:10px;color:var(--bull);letter-spacing:1px}
.ld{width:6px;height:6px;border-radius:50%;background:var(--bull);animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
.scan-btn{background:var(--orange);color:#fff;border:none;padding:7px 20px;border-radius:5px;font-size:12px;font-weight:700;font-family:inherit;letter-spacing:1px;cursor:pointer}
.scan-btn:active{opacity:.7}
.scan-btn.busy{background:var(--s3);color:var(--muted)}
.tf-row{display:flex;gap:4px;margin-top:8px}
.tf{flex:1;padding:6px 0;font-size:11px;font-weight:700;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:inherit;cursor:pointer}
.tf.on{background:var(--s3);color:var(--text);border-color:var(--muted)}

.thresh-row{display:flex;align-items:center;gap:10px;padding:8px 14px;background:var(--s2);border-bottom:1px solid var(--border);flex-shrink:0}
.thresh-lbl{font-size:11px;color:var(--muted);white-space:nowrap}
.thresh-row input[type=range]{flex:1;accent-color:var(--orange)}
.thresh-val{font-size:12px;font-weight:700;color:var(--orange);min-width:36px;text-align:right}

.stats{display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid var(--border);flex-shrink:0}
.stat{padding:8px 4px;text-align:center;border-right:1px solid var(--border)}
.stat:last-child{border-right:none}
.stat-n{font-size:22px;font-weight:700}
.stat-l{font-size:9px;color:var(--muted);letter-spacing:.5px;margin-top:1px}

.cards{flex:1;overflow-y:auto;padding:8px 10px 60px;-webkit-overflow-scrolling:touch}
.empty{text-align:center;padding:60px 20px;color:var(--muted);font-size:13px;line-height:2}

.card{background:var(--s1);border:1px solid var(--border);border-radius:10px;margin-bottom:8px;padding:12px 14px;cursor:pointer}
.card:active{background:var(--s2)}
.card.state-fire{border-left:3px solid var(--bull)}
.card.state-on{border-left:3px solid var(--purple)}

.card-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}
.pname{font-size:15px;font-weight:700}
.ptf{font-size:10px;color:var(--muted);margin-top:2px;letter-spacing:.5px}
.pright{text-align:right}
.pprice{font-size:14px;font-weight:700}
.pgap{font-size:11px;margin-top:2px}
.gap-ok{color:var(--bull)}
.gap-no{color:var(--bear)}

.mas{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:10px}
.ma-box{background:var(--s2);border-radius:6px;padding:7px 9px;border:1px solid var(--border)}
.ma-lbl{font-size:9px;color:var(--muted);letter-spacing:.8px;margin-bottom:3px}
.ma-val{font-size:12px;font-weight:700}

.gaps{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:10px}
.gap-box{background:var(--s2);border-radius:6px;padding:6px 8px;border:1px solid var(--border);text-align:center}
.gap-lbl{font-size:9px;color:var(--muted);letter-spacing:.5px;margin-bottom:2px}
.gap-val{font-size:12px;font-weight:700}

.state-badge{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.8px}
.badge-on{background:var(--purple-bg);color:var(--purple);border:1px solid var(--purple)}
.badge-fire{background:var(--bull-bg);color:var(--bull);border:1px solid var(--bull)}
.dot{width:8px;height:8px;border-radius:50%}
.dot-on{background:var(--purple)}
.dot-fire{background:var(--bull)}

.loader{position:fixed;inset:0;background:var(--bg);display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:200;gap:14px}
.loader-t{font-size:16px;font-weight:700;letter-spacing:2px}
.loader-t b{color:var(--orange)}
.spinner{width:28px;height:28px;border:2px solid var(--border);border-top-color:var(--orange);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.err-box{background:var(--bear-bg);border:1px solid var(--bear);border-radius:6px;padding:10px 14px;margin:8px 10px;font-size:11px;color:var(--bear);display:none}
</style>
</head>
<body>

<div class="loader" id="loader">
  <div class="loader-t">CRYPTO <b>SQZ</b></div>
  <div class="spinner"></div>
  <div style="font-size:10px;color:var(--muted);letter-spacing:1px">SCANNING...</div>
</div>

<div class="top">
  <div class="row">
    <div class="brand">CRYPTO <b>SQZ</b></div>
    <div style="display:flex;align-items:center;gap:10px">
      <div class="live"><span class="ld"></span>LIVE</div>
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

<div class="thresh-row">
  <span class="thresh-lbl">CLUSTER THRESHOLD</span>
  <input type="range" min="1" max="20" value="1" step="1" id="tSlider"
         oninput="document.getElementById('tVal').textContent=(this.value/10).toFixed(1)+'%'">
  <span class="thresh-val" id="tVal">0.1%</span>
</div>

<div class="stats" id="statsBar">
  <div class="stat"><div class="stat-n" style="color:var(--bull)">-</div><div class="stat-l">FIRED</div></div>
  <div class="stat"><div class="stat-n" style="color:var(--purple)">-</div><div class="stat-l">IN SQZ</div></div>
</div>

<div id="errBox" class="err-box">Could not reach Binance. Check connection.</div>
<div class="cards" id="cards"></div>

<script>
var TF = '15m';
var DATA = [];

function getThreshold(){
  return parseFloat(document.getElementById('tVal').textContent);
}

function setTF(el, tf){
  document.querySelectorAll('.tf').forEach(function(b){ b.classList.remove('on'); });
  el.classList.add('on');
  TF = tf;
  doScan();
}

function fmtPrice(v){
  if(!v && v !== 0) return '-';
  if(v >= 1000)   return '$' + v.toLocaleString('en', {maximumFractionDigits:1});
  if(v >= 1)      return '$' + v.toFixed(3);
  if(v >= 0.0001) return '$' + v.toFixed(5);
  return '$' + v.toFixed(8);
}

async function doScan(){
  var btn = document.getElementById('scanBtn');
  btn.textContent = '...'; btn.classList.add('busy');
  document.getElementById('errBox').style.display = 'none';
  try {
    var t = getThreshold();
    var res = await fetch('/api/scan/' + TF + '?t=' + t);
    if(!res.ok) throw new Error('HTTP ' + res.status);
    DATA = await res.json();
    render();
    document.getElementById('loader').style.display = 'none';
  } catch(e) {
    document.getElementById('errBox').style.display = 'block';
    document.getElementById('loader').style.display = 'none';
  }
  btn.innerHTML = '&#8635; SCAN'; btn.classList.remove('busy');
}

function render(){
  var fired = DATA.filter(function(r){ return r.state === 'fire'; }).length;
  var inSqz = DATA.filter(function(r){ return r.state === 'on'; }).length;
  document.getElementById('statsBar').innerHTML =
    '<div class="stat"><div class="stat-n" style="color:var(--bull)">' + fired + '</div><div class="stat-l">FIRED</div></div>' +
    '<div class="stat"><div class="stat-n" style="color:var(--purple)">' + inSqz + '</div><div class="stat-l">IN SQZ</div></div>';

  if(!DATA.length){
    document.getElementById('cards').innerHTML =
      '<div class="empty">No squeezes found on ' + TF.toUpperCase() + '<br>' +
      'Try a different timeframe<br>or increase the threshold above</div>';
    return;
  }

  var html = '';
  DATA.forEach(function(r){
    var thresh = r.threshold;
    var g1ok = r.g_price_s20  <= thresh;
    var g2ok = r.g_price_s100 <= thresh;
    var g3ok = r.g_s20_s100   <= thresh;

    var badgeCls = r.state === 'fire' ? 'badge-fire' : 'badge-on';
    var dotCls   = r.state === 'fire' ? 'dot-fire'   : 'dot-on';
    var stateTxt = r.state === 'fire' ? 'FIRED' : 'IN SQZ';
    var cardCls  = 'card state-' + r.state;

    html +=
      '<div class="' + cardCls + '">' +
        '<div class="card-top">' +
          '<div>' +
            '<div class="pname">' + r.display + '</div>' +
            '<div class="ptf">PERP &bull; ' + TF.toUpperCase() + '</div>' +
          '</div>' +
          '<div class="pright">' +
            '<div class="pprice">' + fmtPrice(r.price) + '</div>' +
            '<div class="pgap ' + (r.max_gap <= thresh ? 'gap-ok' : 'gap-no') + '">' +
              'MAX GAP: ' + r.max_gap + '%' +
            '</div>' +
          '</div>' +
        '</div>' +

        '<div class="mas">' +
          '<div class="ma-box">' +
            '<div class="ma-lbl">PRICE</div>' +
            '<div class="ma-val">' + fmtPrice(r.price) + '</div>' +
          '</div>' +
          '<div class="ma-box">' +
            '<div class="ma-lbl">SMA20</div>' +
            '<div class="ma-val">' + fmtPrice(r.sma20) + '</div>' +
          '</div>' +
          '<div class="ma-box">' +
            '<div class="ma-lbl">SMA100</div>' +
            '<div class="ma-val">' + fmtPrice(r.sma100) + '</div>' +
          '</div>' +
        '</div>' +

        '<div class="gaps">' +
          '<div class="gap-box" style="border-color:' + (g1ok?'var(--bull)':'var(--bear)') + '">' +
            '<div class="gap-lbl">P&harr;SMA20</div>' +
            '<div class="gap-val" style="color:' + (g1ok?'var(--bull)':'var(--bear)') + '">' + r.g_price_s20 + '%</div>' +
          '</div>' +
          '<div class="gap-box" style="border-color:' + (g2ok?'var(--bull)':'var(--bear)') + '">' +
            '<div class="gap-lbl">P&harr;SMA100</div>' +
            '<div class="gap-val" style="color:' + (g2ok?'var(--bull)':'var(--bear)') + '">' + r.g_price_s100 + '%</div>' +
          '</div>' +
          '<div class="gap-box" style="border-color:' + (g3ok?'var(--bull)':'var(--bear)') + '">' +
            '<div class="gap-lbl">S20&harr;S100</div>' +
            '<div class="gap-val" style="color:' + (g3ok?'var(--bull)':'var(--bear)') + '">' + r.g_s20_s100 + '%</div>' +
          '</div>' +
        '</div>' +

        '<span class="state-badge ' + badgeCls + '">' +
          '<span class="dot ' + dotCls + '"></span>' + stateTxt +
        '</span>' +
      '</div>';
  });

  document.getElementById('cards').innerHTML = html;
}

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
