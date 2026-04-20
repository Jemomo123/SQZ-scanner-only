from flask import Flask, render_template_string
import os

app = Flask(__name__)

HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="theme-color" content="#0a0c11"/>
<title>SQZ Scanner</title>
<style>
  :root {
    --bg:#0a0c11;--s1:#111318;--s2:#181c24;--s3:#1f2430;--border:#252b3a;
    --text:#e2e8f0;--muted:#56637a;--dim:#2e3647;
    --bull:#22c55e;--bear:#ef4444;--warn:#f59e0b;
    --purple:#a855f7;--orange:#f97316;--blue:#3b82f6;
    --bull-bg:rgba(34,197,94,0.08);--bear-bg:rgba(239,68,68,0.08);
    --purple-bg:rgba(168,85,247,0.10);--orange-bg:rgba(249,115,22,0.12);
  }
  *{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
  html,body{height:100%;background:var(--bg);color:var(--text);font-family:\'SF Mono\',\'Fira Code\',monospace}
  body{display:flex;flex-direction:column;overflow:hidden}

  .topbar{background:var(--s1);border-bottom:0.5px solid var(--border);padding:10px 14px 8px;flex-shrink:0}
  .topbar-row1{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
  .brand{font-size:14px;font-weight:700;letter-spacing:2px}
  .brand span{color:var(--orange)}
  .scan-btn{background:var(--orange);color:#fff;border:none;padding:6px 16px;border-radius:5px;
    font-size:12px;font-family:inherit;font-weight:700;letter-spacing:1px;cursor:pointer}
  .scan-btn:active{opacity:0.75}
  .scan-btn.loading{background:var(--s3);color:var(--muted)}
  .tf-row{display:flex;gap:4px}
  .tf-btn{flex:1;padding:5px 0;font-size:11px;font-weight:700;letter-spacing:1px;border-radius:4px;
    border:0.5px solid var(--border);background:transparent;color:var(--muted);font-family:inherit;cursor:pointer}
  .tf-btn.on{background:var(--s3);color:var(--text);border-color:var(--dim)}

  .settings-toggle{display:flex;align-items:center;justify-content:space-between;
    padding:7px 14px;background:var(--s2);border-bottom:0.5px solid var(--border);
    cursor:pointer;font-size:11px;letter-spacing:1px;color:var(--muted);flex-shrink:0}
  .settings-drawer{background:var(--s1);border-bottom:0.5px solid var(--border);
    padding:10px 14px 12px;display:none;flex-shrink:0}
  .settings-drawer.open{display:block}
  .settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .set-card{background:var(--s2);border-radius:6px;padding:8px 10px}
  .set-lbl{font-size:10px;color:var(--muted);letter-spacing:.8px;margin-bottom:4px}
  .set-row{display:flex;align-items:center;gap:6px}
  .set-row input[type=range]{flex:1;accent-color:var(--orange);height:2px}
  .set-val{font-size:11px;font-weight:700;color:var(--orange);min-width:28px;text-align:right}
  .set-section{font-size:10px;color:var(--purple);letter-spacing:1.5px;margin:8px 0 5px;font-weight:700}

  .stats-bar{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:0.5px solid var(--border);flex-shrink:0}
  .stat{padding:8px 4px;text-align:center;border-right:0.5px solid var(--border)}
  .stat:last-child{border-right:none}
  .stat-n{font-size:18px;font-weight:700}
  .stat-l{font-size:9px;color:var(--muted);letter-spacing:.5px;margin-top:1px}

  .filter-row{display:flex;gap:5px;padding:7px 14px;overflow-x:auto;
    border-bottom:0.5px solid var(--border);flex-shrink:0;scrollbar-width:none}
  .filter-row::-webkit-scrollbar{display:none}
  .fpill{flex-shrink:0;padding:4px 11px;font-size:10px;font-weight:700;letter-spacing:.7px;
    border-radius:20px;border:0.5px solid var(--border);background:transparent;
    color:var(--muted);font-family:inherit;cursor:pointer;white-space:nowrap}
  .fpill.on{border-color:var(--purple);color:var(--purple);background:var(--purple-bg)}

  .legend{display:flex;gap:12px;padding:5px 14px 6px;border-bottom:0.5px solid var(--border);
    flex-shrink:0;overflow-x:auto;scrollbar-width:none}
  .legend::-webkit-scrollbar{display:none}
  .li{display:flex;align-items:center;gap:4px;font-size:10px;color:var(--muted);white-space:nowrap}
  .dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
  .d-on{background:var(--purple)}
  .d-fire{background:var(--bull)}
  .d-none{background:var(--border)}

  .cards{flex:1;overflow-y:auto;padding:8px 10px 80px;-webkit-overflow-scrolling:touch}
  .card{background:var(--s1);border:0.5px solid var(--border);border-radius:8px;
    margin-bottom:7px;padding:10px 12px;cursor:pointer}
  .card:active{background:var(--s2)}
  .card.both-fire{border-color:var(--orange)}
  .card.vol-fire{border-left:2px solid var(--bull)}
  .card.sma-fire{border-left:2px solid var(--purple)}

  .card-top{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:8px}
  .pair-name{font-size:14px;font-weight:700;letter-spacing:.3px}
  .pair-cat{font-size:10px;color:var(--muted);margin-top:1px}
  .price-col{text-align:right}
  .price{font-size:13px;font-weight:700}
  .chg{font-size:11px;margin-top:1px}
  .up{color:var(--bull)}.dn{color:var(--bear)}.neu{color:var(--muted)}

  .card-mid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px}
  .sqz-box{background:var(--s2);border-radius:5px;padding:6px 8px;border:0.5px solid var(--border)}
  .sqz-box.active-on{border-color:var(--purple);background:var(--purple-bg)}
  .sqz-box.active-fire{border-color:var(--bull);background:var(--bull-bg)}
  .sqz-type{font-size:9px;color:var(--muted);letter-spacing:1px;margin-bottom:3px}
  .sqz-status{display:flex;align-items:center;gap:5px;font-size:11px;font-weight:700}
  .sqz-status .dot{width:8px;height:8px}

  .card-bot{display:flex;align-items:center;justify-content:space-between}
  .meta{display:flex;gap:10px;align-items:center}
  .fund-tag{font-size:10px;font-weight:700;padding:2px 7px;border-radius:3px;letter-spacing:.5px}
  .fund-pos{background:var(--bull-bg);color:var(--bull)}
  .fund-neg{background:var(--bear-bg);color:var(--bear)}
  .oi-wrap{display:flex;align-items:center;gap:5px}
  .oi-label{font-size:10px;color:var(--muted)}
  .oi-bar{width:40px;height:3px;background:var(--border);border-radius:2px}
  .oi-fill{height:100%;border-radius:2px;background:var(--blue)}
  .bias-badge{font-size:10px;font-weight:700;padding:3px 9px;border-radius:3px;letter-spacing:.8px}
  .b-long{background:var(--bull-bg);color:var(--bull)}
  .b-short{background:var(--bear-bg);color:var(--bear)}
  .b-flat{background:var(--s3);color:var(--muted)}

  .breakout-banner{font-size:10px;font-weight:700;letter-spacing:.8px;padding:4px 8px;
    border-radius:3px;margin-top:6px;display:flex;align-items:center;gap:5px}
  .bb-elephant{background:var(--orange-bg);color:var(--orange);border:0.5px solid var(--orange)}
  .bb-tail{background:rgba(59,130,246,0.1);color:var(--blue);border:0.5px solid var(--blue)}
  .star{color:var(--orange);font-size:13px;margin-left:3px}

  .modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:100;align-items:flex-end}
  .modal-bg.open{display:flex}
  .modal{background:var(--s1);border-radius:14px 14px 0 0;border:0.5px solid var(--border);
    width:100%;padding:0 0 30px;max-height:85vh;overflow-y:auto}
  .modal-handle{width:36px;height:4px;background:var(--dim);border-radius:2px;margin:10px auto 14px}
  .modal-close{position:absolute;top:12px;right:16px;background:var(--s3);border:none;
    color:var(--muted);font-size:18px;width:28px;height:28px;border-radius:50%;
    cursor:pointer;display:flex;align-items:center;justify-content:center;font-family:inherit}
  .modal-header{padding:0 16px 12px;border-bottom:0.5px solid var(--border);position:relative}
  .modal-pair{font-size:20px;font-weight:700;letter-spacing:.5px}
  .modal-price{font-size:16px;margin-top:2px}
  .modal-body{padding:14px 16px}
  .modal-section{margin-bottom:16px}
  .modal-section-title{font-size:10px;color:var(--muted);letter-spacing:1.5px;margin-bottom:8px;font-weight:700}
  .detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .detail-card{background:var(--s2);border-radius:6px;padding:8px 10px}
  .detail-lbl{font-size:10px;color:var(--muted);letter-spacing:.5px}
  .detail-val{font-size:13px;font-weight:700;margin-top:2px}
  .confirm-box{background:var(--s2);border-radius:8px;padding:12px;border:0.5px solid var(--border);margin-top:6px}
  .confirm-title{font-size:11px;font-weight:700;letter-spacing:1px;margin-bottom:8px}
  .confirm-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px}
  .confirm-lbl{font-size:11px;color:var(--muted)}
  .confirm-val{font-size:11px;font-weight:700}
  .rule-box{background:var(--purple-bg);border:0.5px solid var(--purple);border-radius:6px;
    padding:10px 12px;margin-top:6px;font-size:11px;line-height:1.7;color:var(--text)}
  .rule-box strong{color:var(--orange)}
  .empty{text-align:center;padding:40px 20px;color:var(--muted);font-size:12px}
</style>
</head>
<body>

<div class="topbar">
  <div class="topbar-row1">
    <div class="brand">CRYPTO <span>SQZ</span></div>
    <button class="scan-btn" id="scanBtn" onclick="doScan()">&#8635; SCAN</button>
  </div>
  <div class="tf-row">
    <button class="tf-btn" onclick="setTF(this,\'15m\')">15M</button>
    <button class="tf-btn" onclick="setTF(this,\'1h\')">1H</button>
    <button class="tf-btn on" onclick="setTF(this,\'4h\')">4H</button>
    <button class="tf-btn" onclick="setTF(this,\'1d\')">1D</button>
    <button class="tf-btn" onclick="setTF(this,\'3d\')">3D</button>
    <button class="tf-btn" onclick="setTF(this,\'1w\')">1W</button>
  </div>
</div>

<div class="settings-toggle" onclick="toggleSettings()">
  <span>INDICATOR SETTINGS</span>
  <span id="settingsArrow">&#9660;</span>
</div>
<div class="settings-drawer" id="settingsDrawer">
  <div class="set-section">VOLATILITY SQZ &mdash; BB vs KC</div>
  <div class="settings-grid">
    <div class="set-card">
      <div class="set-lbl">BB LENGTH</div>
      <div class="set-row">
        <input type="range" min="10" max="30" value="20" step="1" oninput="sv(\'bbLv\',this.value)">
        <span class="set-val" id="bbLv">20</span>
      </div>
    </div>
    <div class="set-card">
      <div class="set-lbl">BB MULT</div>
      <div class="set-row">
        <input type="range" min="10" max="30" value="20" step="1" oninput="sv(\'bbMv\',(this.value/10).toFixed(1))">
        <span class="set-val" id="bbMv">2.0</span>
      </div>
    </div>
    <div class="set-card">
      <div class="set-lbl">KC LENGTH</div>
      <div class="set-row">
        <input type="range" min="10" max="30" value="20" step="1" oninput="sv(\'kcLv\',this.value)">
        <span class="set-val" id="kcLv">20</span>
      </div>
    </div>
    <div class="set-card">
      <div class="set-lbl">KC MULT</div>
      <div class="set-row">
        <input type="range" min="10" max="20" value="15" step="1" oninput="sv(\'kcMv\',(this.value/10).toFixed(1))">
        <span class="set-val" id="kcMv">1.5</span>
      </div>
    </div>
  </div>
  <div class="set-section">SMA SQZ &mdash; PRICE + SMA20 + SMA100 CLUSTER</div>
  <div class="settings-grid">
    <div class="set-card">
      <div class="set-lbl">CLUSTER THRESHOLD %</div>
      <div class="set-row">
        <input type="range" min="1" max="30" value="10" step="1" oninput="sv(\'clustV\',(this.value/10).toFixed(1)+\'%\')">
        <span class="set-val" id="clustV">1.0%</span>
      </div>
    </div>
    <div class="set-card">
      <div class="set-lbl">SQZ CANDLES MIN</div>
      <div class="set-row">
        <input type="range" min="2" max="20" value="5" step="1" oninput="sv(\'sqzBarsV\',this.value)">
        <span class="set-val" id="sqzBarsV">5</span>
      </div>
    </div>
    <div class="set-card">
      <div class="set-lbl">ELEPHANT BAR MULT</div>
      <div class="set-row">
        <input type="range" min="10" max="30" value="10" step="1" oninput="sv(\'elephV\',(this.value/10).toFixed(1)+\'x\')">
        <span class="set-val" id="elephV">1.0x</span>
      </div>
    </div>
    <div class="set-card">
      <div class="set-lbl">TAIL WICK RATIO %</div>
      <div class="set-row">
        <input type="range" min="40" max="90" value="60" step="5" oninput="sv(\'tailV\',this.value+\'%\')">
        <span class="set-val" id="tailV">60%</span>
      </div>
    </div>
  </div>
</div>

<div class="stats-bar" id="statsBar"></div>

<div class="filter-row">
  <button class="fpill on" onclick="setFilter(this,\'all\')">ALL</button>
  <button class="fpill" onclick="setFilter(this,\'both\')">BOTH SQZ</button>
  <button class="fpill" onclick="setFilter(this,\'vol\')">VOL SQZ</button>
  <button class="fpill" onclick="setFilter(this,\'sma\')">SMA SQZ</button>
  <button class="fpill" onclick="setFilter(this,\'elephant\')">ELEPHANT BAR</button>
  <button class="fpill" onclick="setFilter(this,\'tail\')">TAIL BAR</button>
  <button class="fpill" onclick="setFilter(this,\'long\')">LONG BIAS</button>
  <button class="fpill" onclick="setFilter(this,\'short\')">SHORT BIAS</button>
  <button class="fpill" onclick="setFilter(this,\'neg_fund\')">NEG FUNDING</button>
</div>

<div class="legend">
  <div class="li"><span class="dot d-on"></span>SQZ ON</div>
  <div class="li"><span class="dot d-fire"></span>SQZ FIRE</div>
  <div class="li"><span class="dot d-none"></span>NONE</div>
  <div class="li"><span style="color:var(--orange);font-size:11px">&#9733;</span> BOTH FIRING</div>
</div>

<div class="cards" id="cards"></div>

<div class="modal-bg" id="modalBg" onclick="closeModal(event)">
  <div class="modal" id="modal">
    <div class="modal-handle"></div>
    <button class="modal-close" onclick="closeModalBtn()">&#10005;</button>
    <div class="modal-header" id="modalHeader"></div>
    <div class="modal-body" id="modalBody"></div>
  </div>
</div>

<script>
const PAIRS=[
  {p:"BTC/USDT",cat:"BTC",base:84200,dec:1},
  {p:"ETH/USDT",cat:"ETH",base:3180,dec:2},
  {p:"SOL/USDT",cat:"ALT",base:148,dec:3},
  {p:"BNB/USDT",cat:"ALT",base:582,dec:2},
  {p:"XRP/USDT",cat:"ALT",base:0.615,dec:4},
  {p:"DOGE/USDT",cat:"MEME",base:0.168,dec:5},
  {p:"AVAX/USDT",cat:"ALT",base:38.4,dec:3},
  {p:"LINK/USDT",cat:"ALT",base:14.2,dec:3},
  {p:"ARB/USDT",cat:"L2",base:1.04,dec:4},
  {p:"OP/USDT",cat:"L2",base:2.21,dec:4},
  {p:"SUI/USDT",cat:"ALT",base:1.87,dec:4},
  {p:"INJ/USDT",cat:"DeFi",base:28.4,dec:3},
  {p:"WIF/USDT",cat:"MEME",base:2.84,dec:4},
  {p:"PEPE/USDT",cat:"MEME",base:0.00001062,dec:8},
  {p:"TON/USDT",cat:"ALT",base:6.12,dec:4},
  {p:"NEAR/USDT",cat:"ALT",base:7.34,dec:4},
  {p:"APT/USDT",cat:"ALT",base:9.87,dec:3},
  {p:"TIA/USDT",cat:"ALT",base:11.2,dec:3},
  {p:"JTO/USDT",cat:"ALT",base:3.45,dec:4},
  {p:"ORDI/USDT",cat:"BTC",base:42.1,dec:3},
  {p:"SEI/USDT",cat:"L1",base:0.78,dec:4},
  {p:"PYTH/USDT",cat:"DeFi",base:0.52,dec:4},
  {p:"W/USDT",cat:"ALT",base:0.67,dec:4},
  {p:"BONK/USDT",cat:"MEME",base:0.0000248,dec:8},
];

let st={tf:"4h",filter:"all",data:[],settings:{}};

function rnd(a,b){return Math.random()*(b-a)+a;}
function fmtP(v,d){return "$"+v.toFixed(d);}

function getSetts(){
  return {
    bbMult:parseFloat(document.getElementById("bbMv").textContent),
    kcMult:parseFloat(document.getElementById("kcMv").textContent),
    clustPct:parseFloat(document.getElementById("clustV").textContent)/100,
    sqzBars:+document.getElementById("sqzBarsV").textContent,
    elephMult:parseFloat(document.getElementById("elephV").textContent),
    tailRatio:+document.getElementById("tailV").textContent/100,
  };
}

function gen(){
  const s=getSetts();
  return PAIRS.map(t=>{
    const price=t.base*(1+rnd(-0.07,0.07));
    const chg=+rnd(-9,9).toFixed(2);
    const sma20=price*(1+rnd(-0.02,0.02));
    const sma100=price*(1+rnd(-0.04,0.04));
    const smaRange=Math.max(Math.abs(price-sma20),Math.abs(price-sma100),Math.abs(sma20-sma100))/price;
    const smaOn=smaRange<s.clustPct;
    const smaFire=!smaOn&&Math.random()<0.30;
    const smaSqz=smaFire?"fire":smaOn?"on":"none";
    const volRatio=rnd(0.5,1.9);
    const volThresh=(s.kcMult/s.bbMult)*0.92;
    const volOn=volRatio<volThresh;
    const volFire=!volOn&&Math.random()<0.35;
    const volSqz=volFire?"fire":volOn?"on":"none";
    const both=volSqz!=="none"&&smaSqz!=="none";
    const bothFire=volSqz==="fire"&&smaSqz==="fire";
    const sqzAvg=rnd(0.3,1.2);
    const lastBar=rnd(0.2,2.8);
    const lastWick=rnd(0.2,0.9);
    const isElephant=(volFire||smaFire)&&lastBar>=s.elephMult*sqzAvg;
    const isTail=(volFire||smaFire)&&lastWick>=s.tailRatio&&lastBar>=s.elephMult*sqzAvg;
    const confirmType=isTail?"tail":isElephant?"elephant":null;
    const funding=+rnd(-0.04,0.055).toFixed(4);
    const oi=+rnd(0.15,1).toFixed(2);
    const bias=chg>1.8?"long":chg<-1.8?"short":"flat";
    const sqzBarsCount=Math.round(rnd(3,18));
    return {...t,price,chg,sma20,sma100,smaRange,smaSqz,volSqz,both,bothFire,
      sqzAvg,lastBar,lastWick,isElephant,isTail,confirmType,funding,oi,bias,sqzBarsCount};
  });
}

function sv(id,val){document.getElementById(id).textContent=val;}

function setTF(el,tf){
  document.querySelectorAll(".tf-btn").forEach(b=>b.classList.remove("on"));
  el.classList.add("on");st.tf=tf;doScan();
}
function setFilter(el,f){
  document.querySelectorAll(".fpill").forEach(b=>b.classList.remove("on"));
  el.classList.add("on");st.filter=f;render();
}
function toggleSettings(){
  const d=document.getElementById("settingsDrawer");
  const a=document.getElementById("settingsArrow");
  d.classList.toggle("open");
  a.innerHTML=d.classList.contains("open")?"&#9650;":"&#9660;";
}

function doScan(){
  const btn=document.getElementById("scanBtn");
  btn.textContent="...";btn.classList.add("loading");
  setTimeout(()=>{st.data=gen();render();btn.innerHTML="&#8635; SCAN";btn.classList.remove("loading");},450);
}

function dotEl(v){
  const cls=v==="fire"?"d-fire":v==="on"?"d-on":"d-none";
  const lbl=v==="fire"?"FIRE":v==="on"?"ON":"&mdash;";
  return `<span class="dot ${cls}"></span> ${lbl}`;
}

function render(){
  let rows=[...st.data];
  if(st.filter==="vol") rows=rows.filter(r=>r.volSqz!=="none");
  if(st.filter==="sma") rows=rows.filter(r=>r.smaSqz!=="none");
  if(st.filter==="both") rows=rows.filter(r=>r.both);
  if(st.filter==="elephant") rows=rows.filter(r=>r.isElephant);
  if(st.filter==="tail") rows=rows.filter(r=>r.isTail);
  if(st.filter==="long") rows=rows.filter(r=>r.bias==="long");
  if(st.filter==="short") rows=rows.filter(r=>r.bias==="short");
  if(st.filter==="neg_fund") rows=rows.filter(r=>r.funding<0);

  const bothFire=st.data.filter(r=>r.bothFire).length;
  const confirmed=st.data.filter(r=>r.confirmType).length;
  const volAct=st.data.filter(r=>r.volSqz!=="none").length;
  const negF=st.data.filter(r=>r.funding<0).length;

  document.getElementById("statsBar").innerHTML=`
    <div class="stat"><div class="stat-n" style="color:#f97316">${bothFire}</div><div class="stat-l">BOTH FIRE</div></div>
    <div class="stat"><div class="stat-n" style="color:#22c55e">${confirmed}</div><div class="stat-l">CONFIRMED</div></div>
    <div class="stat"><div class="stat-n" style="color:#a855f7">${volAct}</div><div class="stat-l">VOL SQZ</div></div>
    <div class="stat"><div class="stat-n" style="color:#ef4444">${negF}</div><div class="stat-l">NEG FUND</div></div>
  `;

  if(!rows.length){
    document.getElementById("cards").innerHTML=`<div class="empty">No pairs match this filter</div>`;
    return;
  }

  document.getElementById("cards").innerHTML=rows.map(r=>{
    const chgCls=r.chg>0?"up":r.chg<0?"dn":"neu";
    const chgStr=(r.chg>0?"+":"")+r.chg.toFixed(2)+"%";
    const bCls=r.bias==="long"?"b-long":r.bias==="short"?"b-short":"b-flat";
    const bTxt=r.bias==="long"?"LONG":r.bias==="short"?"SHORT":"FLAT";
    const fundCls=r.funding<0?"fund-neg":"fund-pos";
    const fundStr=(r.funding>0?"+":"")+r.funding.toFixed(4)+"%";
    const oiPct=Math.round(r.oi*100);
    const star=r.bothFire?`<span class="star">&#9733;</span>`:"";
    const vBox=r.volSqz==="fire"?"active-fire":r.volSqz==="on"?"active-on":"";
    const sBox=r.smaSqz==="fire"?"active-fire":r.smaSqz==="on"?"active-on":"";
    let cardCls="card";
    if(r.bothFire) cardCls+=" both-fire";
    else if(r.volSqz==="fire") cardCls+=" vol-fire";
    else if(r.smaSqz==="fire") cardCls+=" sma-fire";
    let banner="";
    if(r.isTail) banner=`<div class="breakout-banner bb-tail">TAIL BAR &mdash; ENTER NOW &bull; ${r.lastBar.toFixed(1)}x SQZ SIZE</div>`;
    else if(r.isElephant) banner=`<div class="breakout-banner bb-elephant">ELEPHANT BAR &mdash; ENTER NOW &bull; ${r.lastBar.toFixed(1)}x SQZ SIZE</div>`;
    return `<div class="${cardCls}" onclick="openModal('${r.p}')">
      <div class="card-top">
        <div><div class="pair-name">${r.p}${star}</div><div class="pair-cat">${r.cat} PERP &bull; ${st.tf.toUpperCase()}</div></div>
        <div class="price-col"><div class="price">${fmtP(r.price,r.dec)}</div><div class="chg ${chgCls}">${chgStr}</div></div>
      </div>
      <div class="card-mid">
        <div class="sqz-box ${vBox}"><div class="sqz-type">VOL SQZ (BB/KC)</div><div class="sqz-status">${dotEl(r.volSqz)}</div></div>
        <div class="sqz-box ${sBox}"><div class="sqz-type">SMA SQZ (20/100)</div><div class="sqz-status">${dotEl(r.smaSqz)}</div></div>
      </div>
      <div class="card-bot">
        <div class="meta">
          <span class="fund-tag ${fundCls}">${fundStr}</span>
          <div class="oi-wrap"><span class="oi-label">OI</span><div class="oi-bar"><div class="oi-fill" style="width:${oiPct}%"></div></div></div>
        </div>
        <span class="bias-badge ${bCls}">${bTxt}</span>
      </div>
      ${banner}
    </div>`;
  }).join("");
}

function openModal(pair){
  const r=st.data.find(d=>d.p===pair);
  if(!r) return;
  const s=getSetts();
  const chgCls=r.chg>0?"up":r.chg<0?"dn":"neu";
  const chgStr=(r.chg>0?"+":"")+r.chg.toFixed(2)+"%";
  const star=r.bothFire?`<span class="star">&#9733;</span>`:"";
  document.getElementById("modalHeader").innerHTML=`
    <div class="modal-pair">${r.p}${star}</div>
    <div class="modal-price"><span class="${chgCls}">${fmtP(r.price,r.dec)}</span> <span style="font-size:12px" class="${chgCls}">${chgStr}</span></div>
  `;
  const fundCls=r.funding<0?"fund-neg":"fund-pos";
  const smaRangePct=(r.smaRange*100).toFixed(2);
  let confirmSection="";
  if(r.confirmType){
    const type=r.isTail?"TAIL BAR":"ELEPHANT BAR";
    const color=r.isTail?"var(--blue)":"var(--orange)";
    confirmSection=`
      <div class="modal-section">
        <div class="modal-section-title">BREAKOUT CONFIRMATION</div>
        <div class="confirm-box" style="border-color:${color}">
          <div class="confirm-title" style="color:${color}">${type} DETECTED &mdash; ENTRY VALID</div>
          <div class="confirm-row"><span class="confirm-lbl">Bar size vs SQZ avg</span><span class="confirm-val" style="color:${color}">${r.lastBar.toFixed(2)}x</span></div>
          <div class="confirm-row"><span class="confirm-lbl">Required mult</span><span class="confirm-val">${s.elephMult.toFixed(1)}x</span></div>
          ${r.isTail?`<div class="confirm-row"><span class="confirm-lbl">Wick ratio</span><span class="confirm-val">${(r.lastWick*100).toFixed(0)}% (min ${(s.tailRatio*100).toFixed(0)}%)</span></div>`:""}
          <div class="confirm-row"><span class="confirm-lbl">SQZ candles held</span><span class="confirm-val">${r.sqzBarsCount}</span></div>
        </div>
      </div>`;
  } else if(r.volSqz!=="none"||r.smaSqz!=="none"){
    confirmSection=`
      <div class="modal-section">
        <div class="modal-section-title">BREAKOUT CONFIRMATION</div>
        <div class="confirm-box">
          <div class="confirm-title" style="color:var(--muted)">WAITING FOR ELEPHANT / TAIL BAR</div>
          <div class="confirm-row"><span class="confirm-lbl">SQZ candles so far</span><span class="confirm-val">${r.sqzBarsCount}</span></div>
          <div class="confirm-row"><span class="confirm-lbl">Need bar size &ge;</span><span class="confirm-val">${s.elephMult.toFixed(1)}x avg SQZ candle</span></div>
          <div class="confirm-row"><span class="confirm-lbl">Tail wick threshold</span><span class="confirm-val">${(s.tailRatio*100).toFixed(0)}% of bar</span></div>
        </div>
      </div>`;
  }
  document.getElementById("modalBody").innerHTML=`
    <div class="modal-section">
      <div class="modal-section-title">SQUEEZE STATUS</div>
      <div class="detail-grid">
        <div class="detail-card" style="${r.volSqz!=="none"?"border:0.5px solid var(--purple)":""}">
          <div class="detail-lbl">VOL SQZ (BB/KC)</div>
          <div class="detail-val" style="color:${r.volSqz==="fire"?"var(--bull)":r.volSqz==="on"?"var(--purple)":"var(--muted)"}">${r.volSqz==="fire"?"FIRE":r.volSqz==="on"?"ON":"&mdash;"}</div>
        </div>
        <div class="detail-card" style="${r.smaSqz!=="none"?"border:0.5px solid var(--purple)":""}">
          <div class="detail-lbl">SMA SQZ (20/100)</div>
          <div class="detail-val" style="color:${r.smaSqz==="fire"?"var(--bull)":r.smaSqz==="on"?"var(--purple)":"var(--muted)"}">${r.smaSqz==="fire"?"FIRE":r.smaSqz==="on"?"ON":"&mdash;"}</div>
        </div>
        <div class="detail-card">
          <div class="detail-lbl">SMA cluster range</div>
          <div class="detail-val" style="color:${r.smaRange<0.01?"var(--bull)":"var(--text)"}">${smaRangePct}%</div>
        </div>
        <div class="detail-card">
          <div class="detail-lbl">SQZ bars held</div>
          <div class="detail-val">${r.sqzBarsCount}</div>
        </div>
      </div>
    </div>
    <div class="modal-section">
      <div class="modal-section-title">MARKET DATA</div>
      <div class="detail-grid">
        <div class="detail-card"><div class="detail-lbl">SMA20</div><div class="detail-val">${fmtP(r.sma20,r.dec)}</div></div>
        <div class="detail-card"><div class="detail-lbl">SMA100</div><div class="detail-val">${fmtP(r.sma100,r.dec)}</div></div>
        <div class="detail-card"><div class="detail-lbl">Funding rate</div><div class="detail-val ${fundCls}">${(r.funding>0?"+":"")+r.funding.toFixed(4)+"%"}</div></div>
        <div class="detail-card"><div class="detail-lbl">OI strength</div><div class="detail-val">${Math.round(r.oi*100)}%</div></div>
      </div>
    </div>
    ${confirmSection}
    <div class="modal-section">
      <div class="rule-box">
        <strong>SMA SQZ:</strong> Price, SMA20 &amp; SMA100 all clustered together.<br>
        <strong>CONFIRM:</strong> Elephant bar (big body) or Tail bar (long wick) &mdash; must be <strong>${s.elephMult.toFixed(1)}x</strong> the size of the SQZ candles.<br>
        <strong>ACTION:</strong> Enter immediately on confirmation.
      </div>
    </div>
  `;
  document.getElementById("modalBg").classList.add("open");
}

function closeModal(e){if(e.target===document.getElementById("modalBg")) closeModalBtn();}
function closeModalBtn(){document.getElementById("modalBg").classList.remove("open");}

doScan();
</script>
</body>
</html>'''

@app.route('/')
def index():
    return render_template_string(HTML)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
