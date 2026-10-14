#!/usr/bin/env python3
"""sniffo message-stats — page renderer (build-order step 3).

Emits one HTML shell per (network, channel) that fetches its own <slug>.json
at runtime (cache-busted) and renders client-side: grappa.chat palette, CVD-
validated categorical series (dataviz skill), interactive SVG charts (hover
tooltips + legend-toggle), a particle-network background, both themes, a table.
The daily-history chart is an inlined uPlot (vendored, no CDN) with drag-zoom
and a deep-linkable ?from=&to= date range; the rest stay hand-rolled SVG.
Dedication: "a dante lo scettico".
"""
import html
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import metrics as M  # noqa: E402

_TZ = ZoneInfo("Europe/Rome")

# Vendored uPlot (v1.6.32, MIT) — inlined into every page so the output stays
# self-contained: no CDN, no extra deploy artifact (the daemon's rsync filter
# ships *.html + *.json only). Powers the zoomable + deep-linkable daily chart.
_UPLOT_JS = (HERE / "vendor" / "uplot.min.js").read_text(encoding="utf-8")
_UPLOT_CSS = (HERE / "vendor" / "uplot.min.css").read_text(encoding="utf-8")

# CVD-validated categorical series (validate_palette.js, both themes) drawn
# from the grappa.chat nick ramp; service = grappa --muted.
PALETTE = {
    "dark":  {"human": "#55a23f", "ai": "#6484de", "classic": "#d85f9c", "service": "#707070"},
    "light": {"human": "#2e7d32", "ai": "#3049b8", "classic": "#b02c72", "service": "#7f7f7f"},
}

TEMPLATE = r"""<!DOCTYPE html>
<html lang="it" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>__TITLE__ · stats</title>
<style>
:root{
  --bg:#0a0a0a; --bg-alt:#111111; --fg:#e0e0e0; --accent:#5fafd7;
  --muted:#707070; --border:#1f1f1f; --panel:#0f0f0f;
  --human:#55a23f; --ai:#6484de; --classic:#d85f9c; --service:#707070;
}
:root[data-theme="light"]{
  --bg:#ffffff; --bg-alt:#f5f5f5; --fg:#111111; --accent:#00007f;
  --muted:#7f7f7f; --border:#c0c0c0; --panel:#fafafa;
  --human:#2e7d32; --ai:#3049b8; --classic:#b02c72; --service:#7f7f7f;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg); color:var(--fg);
  font-family:"DejaVu Sans Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  font-size:14px; line-height:1.5; overflow-x:hidden;
}
#bg{position:fixed; inset:0; z-index:-1; pointer-events:none}
.wrap{max-width:1000px; margin:0 auto; padding:24px 16px 64px}
header{display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:4px}
h1{font-size:20px; margin:0; font-weight:700}
h1 .net{color:var(--accent)}
.dedica{color:var(--muted); font-style:italic; font-size:12px}
.span{color:var(--muted); font-size:12px; margin:2px 0 20px}
.themebtn{margin-left:auto; background:var(--bg-alt); color:var(--fg);
  border:1px solid var(--border); border-radius:6px; padding:4px 10px; cursor:pointer; font:inherit; font-size:12px}
section{background:var(--panel); border:1px solid var(--border); border-radius:10px;
  padding:16px; margin:14px 0}
h2{font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
  margin:0 0 12px; font-weight:600}
.tiles{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px}
.tile{background:var(--bg-alt); border:1px solid var(--border); border-radius:8px; padding:12px}
.tile .k{color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em}
.tile .v{font-size:26px; font-weight:700; margin-top:4px}
.tile .s{color:var(--muted); font-size:11px}
.hero{display:flex; gap:24px; align-items:center; flex-wrap:wrap}
.hero .num{font-size:52px; font-weight:800; line-height:1}
.hero .num small{font-size:16px; color:var(--muted); font-weight:400}
.hero .lead{flex:1; min-width:240px}
.legend{display:flex; gap:14px; flex-wrap:wrap; margin:10px 0 0; font-size:12px}
.legend button{background:none; border:0; color:inherit; cursor:pointer; font:inherit;
  display:flex; align-items:center; gap:6px; padding:2px 4px; opacity:1}
.legend button.off{opacity:.35; text-decoration:line-through}
.sw{width:11px; height:11px; border-radius:3px; display:inline-block}
svg{display:block; width:100%; height:auto; overflow:visible}
.bar-track{fill:var(--bg-alt)}
.axis{stroke:var(--border); stroke-width:1}
.gridline{stroke:var(--border); stroke-width:1; opacity:.5}
.tick{fill:var(--muted); font-size:10px}
.dlabel{fill:var(--fg); font-size:11px}
.rowlabel{fill:var(--fg); font-size:12px}
.scale{position:relative; height:34px; border-radius:6px; overflow:hidden;
  background:linear-gradient(90deg,#5a9,#8b4,#db4,#d72,#b22); margin-top:8px}
.scale .marker{position:absolute; top:-2px; bottom:-2px; width:3px; background:var(--fg);
  box-shadow:0 0 0 1px var(--bg)}
.scale-labels{display:flex; justify-content:space-between; color:var(--muted); font-size:10px; margin-top:4px}
.tier-badge{display:inline-block; padding:3px 10px; border-radius:20px; font-weight:700;
  background:var(--bg-alt); border:1px solid var(--border)}
#tip{position:fixed; z-index:10; pointer-events:none; background:var(--bg-alt);
  border:1px solid var(--border); border-radius:6px; padding:6px 9px; font-size:12px;
  color:var(--fg); opacity:0; transition:opacity .08s; max-width:260px; box-shadow:0 4px 14px rgba(0,0,0,.4)}
table{border-collapse:collapse; width:100%; font-size:12px}
th,td{text-align:left; padding:4px 8px; border-bottom:1px solid var(--border)}
th{color:var(--muted); font-weight:600}
td.n{text-align:right; font-variant-numeric:tabular-nums}
details{margin-top:8px} summary{cursor:pointer; color:var(--muted); font-size:12px}
.mono{font-variant-numeric:tabular-nums}
.rangebadge{font-size:11px;color:var(--accent);font-weight:400;text-transform:none;letter-spacing:0;margin-left:6px}
.allperiod{font-size:11px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0;margin-left:6px}
.static-sep{margin:30px 0 0;padding-top:16px;border-top:1px solid var(--border);
  color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.resetbtn{background:var(--bg-alt);color:var(--fg);border:1px solid var(--border);border-radius:6px;
  padding:4px 10px;cursor:pointer;font:inherit;font-size:12px;margin-top:10px}
a{color:var(--accent)}
@media (max-width:560px){.hero .num{font-size:40px}}
</style>
<style>
__UPLOT_CSS__
.u-legend{font:12px "DejaVu Sans Mono",ui-monospace,Menlo,Consolas,monospace;color:var(--fg);margin-top:10px}
.u-legend .u-marker{width:11px;height:11px;border-radius:3px;border:0!important}
.u-legend th{font-weight:600}
.u-legend .u-value{font-variant-numeric:tabular-nums;color:var(--fg)}
.u-legend .u-series{cursor:pointer}
.u-legend .u-series.u-off *{opacity:.35}
.uplot .u-select{background:color-mix(in srgb, var(--accent) 22%, transparent)}
.uplot .u-cursor-x,.uplot .u-cursor-y{border-right:1px dashed var(--muted)}
.uplot .u-cursor-pt{border-color:var(--accent)}
#history,#presence{touch-action:pan-y}
.hist-hint{color:var(--muted);font-size:11px;margin:0}
</style>
</head>
<body>
<canvas id="bg"></canvas>
<div class="wrap">
<header>
  <h1>__CHAN__ <span class="net">· __NET__</span></h1>
  <button class="themebtn" id="themebtn">tema</button>
</header>
<div class="dedica">a dante lo scettico</div>
<div class="span" id="span"></div>

<!-- ===== DINAMICI — reagiscono allo zoom dello storico ===== -->
<section>
  <h2>Storico giornaliero</h2>
  <div class="legend" id="hist-legend"></div>
  <div id="history"></div>
  <button class="resetbtn" id="hist-reset" style="display:none">⟲ tutto il periodo</button>
</section>

<section id="s-presence">
  <h2>Presenze in canale</h2>
  <div class="legend" id="pres-legend"></div>
  <div id="presence"></div>
</section>

<section id="s-hero">
  <h2>Bot vs Umani</h2>
  <div class="hero">
    <div><div class="num" id="ratio-num"></div></div>
    <div class="lead">
      <div id="composition"></div>
      <div class="legend" id="legend"></div>
    </div>
  </div>
</section>

<section>
  <h2>In numeri</h2>
  <div class="tiles" id="tiles"></div>
</section>

<section>
  <h2>Attività per ora del giorno</h2>
  <div id="hourly"></div>
</section>

<section>
  <h2>Trasporto (messaggi umani)</h2>
  <div id="transport"></div>
</section>

<section class="best-only">
  <h2>Bestemmiometro</h2>
  <p style="margin:0 0 4px">Intensità: <span class="tier-badge" id="tier"></span>
     <span class="mono" id="best-ratio" style="color:var(--muted)"></span></p>
  <div class="scale"><div class="marker" id="scale-marker"></div></div>
  <div class="scale-labels"><span>catechismo</span><span>chierichetto</span><span>peccatore</span><span>bestemmiatore</span><span>super bestemmiato</span></div>
</section>

<!-- ===== STATICI — sempre sull'intero periodo, non filtrati dallo zoom ===== -->
<div class="static-sep">tutto il periodo · non filtrati dallo zoom</div>

<section>
  <h2>Chi parla di più <span class="allperiod" id="ap-talkers"></span></h2>
  <div id="talkers"></div>
</section>

<section class="best-only">
  <h2>Top bestemmiatori <span class="allperiod" id="ap-best"></span></h2>
  <div id="best"></div>
</section>

<section>
  <h2>Umani distinti <span class="allperiod" id="ap-distinct"></span></h2>
  <div class="hero"><div class="num" id="distinct-num"></div></div>
  <!-- vjt 01/08: si contano per NICK, punto — niente ricucitura della catena
       dei rename. Il numero e' quindi un po' gonfio e lo si dichiara qui. -->
  <p class="hist-hint">si contano i <b>nick</b>, non le persone: chi cambia nick (o viene rinominato in <code>GuestNNNNN</code> dai services dopo un netsplit) conta piu' volte</p>
</section>

<details>
  <summary>vista tabella (tutti i numeri)</summary>
  <div id="table"></div>
</details>

</div>
<div id="tip"></div>
<script>__UPLOT_JS__</script>
<script>
const SLUG = "__SLUG__";
// Malekith 14/10: some channels want the blasphemy panels gone (classes.json hide_blasphemy)
const NOBEST = __NOBEST__;
if(NOBEST)document.querySelectorAll(".best-only").forEach(e=>e.remove());
let DATA = null;
// ---- range aggregation: dynamic panels re-sum the per-day history buckets ----
let AGG = null;    // aggregate over the active range — dynamic panels read this, not DATA.totals
let RANGE = null;  // {min,max} epoch secs, or null = full extent (no zoom active)
const BEST_TIERS=[[0.5,"catechismo"],[2,"chierichetto"],[5,"peccatore"],[12,"bestemmiatore"],[Infinity,"super bestemmiato"]];
function tierFromPer100(p){for(const [hi,name] of BEST_TIERS)if(p<hi)return name;return BEST_TIERS[BEST_TIERS.length-1][1];}
function dayTs(dstr){return Math.floor(Date.parse(dstr+"T00:00:00")/1000);}  // midnight — matches uPlot x-values
function median(v){if(!v.length)return 0;const s=[...v].sort((x,y)=>x-y),n=s.length;
  return n%2?s[n>>1]:(s[n/2-1]+s[n/2])/2;}
function computeAgg(){
  const H=DATA.history||[];
  const inR = RANGE ? (b=>{const t=dayTs(b.date);return t>=RANGE.min&&t<=RANGE.max;}) : (()=>true);
  const a={messages:0,human:0,ai:0,classic:0,service:0,bestemmie:0,irc:0,telegram:0,
           hourly:new Array(24).fill(0),days:0,from:null,to:null};
  // DAU is NON-additive (vjt order 05/08): summing `dau` over the range would
  // count the same regular once per day. The honest range figure is the UNION
  // of the daily sets (indices into DATA.dau_nick_table); the typical-day
  // figure is the MEDIAN of the daily values, so one crowded day is not "the
  // norm" — same reasoning as the presence median.
  const daus=[], union=new Set();
  for(const b of H){ if(!inR(b))continue;
    a.messages+=b.messages||0; a.human+=b.human||0; a.ai+=b.ai||0; a.classic+=b.classic||0;
    a.service+=b.service||0; a.bestemmie+=b.bestemmie||0; a.irc+=b.irc||0; a.telegram+=b.telegram||0;
    const bh=b.hourly||[]; for(let i=0;i<24;i++)a.hourly[i]+=bh[i]||0;
    if(b.dau!=null){daus.push(b.dau); for(const i of (b.dau_nicks||[]))union.add(i);}
    a.days++; if(a.from===null)a.from=b.date; a.to=b.date;
  }
  a.dau_med=median(daus); a.dau_union=union.size;
  a.dau_last=daus.length?daus[daus.length-1]:0;
  const m=a.messages||1;
  a.per100=Math.round(100*a.bestemmie/m*100)/100;
  a.tier=tierFromPer100(a.per100);
  a.ai_vs_human=a.human?a.ai/a.human:0;
  a.allbots_vs_human=a.human?(a.ai+a.classic)/a.human:0;
  AGG=a;
}
function rangeFromURL(){
  const H=DATA.history||[]; if(!H.length)return null;
  const xs=H.map(b=>dayTs(b.date)), lo=xs[0], hi=xs[xs.length-1];
  const p=new URLSearchParams(location.search),f=p.get("from"),t=p.get("to");
  if(!f&&!t)return null;
  let mn=f?Math.floor(Date.parse(f+"T00:00:00")/1000):lo;
  let mx=t?Math.floor(Date.parse(t+"T23:59:59")/1000):hi;
  if(isNaN(mn))mn=lo; if(isNaN(mx))mx=hi;
  if(mn>mx){const s=mn;mn=mx;mx=s;}
  if(mn<=lo && mx>=hi)return null;
  return {min:mn,max:mx};
}
function applyRange(mn,mx,u){
  const xs=u.data[0], lo=xs[0], hi=xs[xs.length-1];
  RANGE=(mn<=lo+1 && mx>=hi-1)?null:{min:mn,max:mx};
  renderDynamic();
}
function updateRangeUI(){
  const z=RANGE!=null;
  const rb=document.getElementById("hist-reset"); if(rb)rb.style.display=z?"inline-block":"none";
  const ap=z?"(intero periodo)":"";
  for(const id of ["ap-talkers","ap-best","ap-distinct"]){const e=document.getElementById(id);if(e)e.textContent=ap;}
}
const PAL = getComputedStyle(document.documentElement);
function col(k){return getComputedStyle(document.documentElement).getPropertyValue('--'+k).trim();}
const CLASSES=[["human","umani"],["ai","IA (clanker)"],["classic","bot classici"],["service","servizi"]];
const NF=new Intl.NumberFormat('it-IT');
const SVGNS="http://www.w3.org/2000/svg";
function el(tag,attrs,kids){const e=document.createElementNS(SVGNS,tag);
  for(const k in (attrs||{}))e.setAttribute(k,attrs[k]);
  for(const c of (kids||[]))e.appendChild(c); return e;}
function svg(w,h){const s=el("svg",{viewBox:`0 0 ${w} ${h}`,preserveAspectRatio:"xMidYMid meet"});s._w=w;s._h=h;return s;}
const tip=document.getElementById("tip");
function showTip(ev,htmlStr){tip.innerHTML=htmlStr;tip.style.opacity=1;
  let x=ev.clientX+14,y=ev.clientY+14;
  if(x+tip.offsetWidth>innerWidth)x=ev.clientX-tip.offsetWidth-14;
  if(y+tip.offsetHeight>innerHeight)y=ev.clientY-tip.offsetHeight-14;
  tip.style.left=x+"px";tip.style.top=y+"px";}
function hideTip(){tip.style.opacity=0;}
function hover(node,htmlFn){node.addEventListener("mousemove",e=>showTip(e,htmlFn()));
  node.addEventListener("mouseleave",hideTip);node.style.cursor="default";}

// ---- span ----
function fmtDate(ts){return new Date(ts*1000).toLocaleDateString('it-IT',{day:'2-digit',month:'short',year:'numeric'});}
function fmtDay(dstr){return fmtDate(dayTs(dstr));}
function drawSpan(){const s=document.getElementById("span");
  if(RANGE&&AGG.from){
    s.innerHTML=`<b style="color:var(--accent)">zoom</b> ${fmtDay(AGG.from)} → ${fmtDay(AGG.to)} · ${NF.format(AGG.messages)} messaggi <span style="color:var(--muted)">· trascina lo storico o «⟲ tutto il periodo» per cambiare</span>`;
  }else{
    s.textContent=`${fmtDate(DATA.span.from)} → ${fmtDate(DATA.span.to)} · ${NF.format(AGG.messages)} messaggi`;
  }}

// ---- hero: ratio + composition ----
const muted=new Set();
function ratable(){return ["human","ai","classic"].filter(k=>!muted.has(k));}
function drawRatio(){
  const num=document.getElementById("ratio-num");
  // TUTTI i bot, non solo l'IA (vjt, #sbiffo 2026-09-11 09:10): su un canale
  // dove il traffico automatico e' quasi tutto codice classico il rapporto
  // IA:umani si leggeva 0.00 e la fetta grossa spariva dal titolo.
  const r = AGG.human? ((AGG.ai+AGG.classic)/AGG.human):0;
  num.innerHTML = `${r.toFixed(2)} <small>bot : umani</small>`;
}
function drawComposition(){
  const box=document.getElementById("composition");box.innerHTML="";
  const t=AGG; const keys=["human","ai","classic","service"].filter(k=>(t[k]||0)>0 && !muted.has(k));
  const tot=keys.reduce((s,k)=>s+t[k],0)||1;
  const W=560,H=42,gap=2; const s=svg(W,H);
  let x=0;
  for(const k of keys){
    const w=Math.max(0,(t[k]/tot)*W - gap);
    const rect=el("rect",{x:x, y:0, width:w, height:H, rx:5, fill:col(k)});
    const pct=(100*t[k]/tot);
    hover(rect,()=>`<b>${CLASSES.find(c=>c[0]==k)[1]}</b><br>${NF.format(t[k])} msg · ${pct.toFixed(1)}%`);
    s.appendChild(rect);
    if(w>44)s.appendChild(el("text",{x:x+w/2,y:H/2+4,"text-anchor":"middle",class:"dlabel"},[txt(`${pct.toFixed(0)}%`)]));
    x+=w+gap;
  }
  box.appendChild(s);
}
function txt(str){const t=document.createElementNS(SVGNS,"text");return document.createTextNode(str);}
function textNode(str){return document.createTextNode(str);}
function drawLegend(){
  const lg=document.getElementById("legend");lg.innerHTML="";
  for(const [k,label] of CLASSES){
    if((AGG[k]||0)===0) continue;
    const b=document.createElement("button");b.className=muted.has(k)?"off":"";
    b.innerHTML=`<span class="sw" style="background:${col(k)}"></span>${label} <span class="mono" style="color:var(--muted)">${NF.format(AGG[k]||0)}</span>`;
    b.onclick=()=>{if(muted.has(k))muted.delete(k);else muted.add(k);renderDynamic();};
    lg.appendChild(b);
  }
}

// ---- tiles ----
function drawTiles(){
  const tiles=[
    ["messaggi", NF.format(AGG.messages), ""],
    // "attivo" = ha PARLATO quel giorno (un lurker nei NAMES non e' un attivo:
    // quello lo misura il grafico presenze). Mediana per il giorno tipo, union
    // per il periodo — mai la somma, vedi computeAgg.
    ["attivi al giorno", NF.format(AGG.dau_med),
     `${NF.format(AGG.dau_union)} distinti nel periodo · ultimo giorno ${NF.format(AGG.dau_last)}`],
    // l'hero ora e' tutti-bot:umani, qui resta la quota della sola IA
    ["ratio IA : umani", (AGG.ai_vs_human||0).toFixed(2), ""],
    ["bestemmie", NF.format(AGG.bestemmie), `${AGG.per100}/100 msg`],
  ].filter(t=>!NOBEST||t[0]!=="bestemmie");
  const box=document.getElementById("tiles");box.innerHTML="";
  for(const [k,v,s] of tiles){
    const d=document.createElement("div");d.className="tile";
    d.innerHTML=`<div class="k">${k}</div><div class="v mono">${v}</div><div class="s">${s}</div>`;
    box.appendChild(d);
  }
}

// ---- hourly histogram ----
function drawHourly(){
  const box=document.getElementById("hourly");box.innerHTML="";
  const data=AGG.hourly; const max=Math.max(...data,1);
  const W=920,H=200,padL=30,padB=22,padT=8;
  const s=svg(W,H); const bw=(W-padL)/24;
  for(const gy of [0.25,0.5,0.75,1]){
    const y=padT+(H-padT-padB)*(1-gy);
    s.appendChild(el("line",{x1:padL,y1:y,x2:W,y2:y,class:"gridline"}));
    s.appendChild(el("text",{x:padL-4,y:y+3,"text-anchor":"end",class:"tick"},[textNode(NF.format(Math.round(max*gy)))]));
  }
  data.forEach((v,h)=>{
    const bh=(H-padT-padB)*(v/max); const x=padL+h*bw+1; const y=H-padB-bh;
    const rect=el("rect",{x:x,y:y,width:bw-2,height:bh,rx:3,fill:col("accent")});
    hover(rect,()=>`<b>ore ${String(h).padStart(2,'0')}:00</b><br>${NF.format(v)} messaggi`);
    s.appendChild(rect);
    if(h%3===0)s.appendChild(el("text",{x:x+bw/2,y:H-6,"text-anchor":"middle",class:"tick"},[textNode(String(h).padStart(2,'0'))]));
  });
  box.appendChild(s);
}

// ---- daily history: uPlot time-series (drag-zoom + deep-linkable range) ----
// Bestemmie ride a SECOND y scale ("best", right axis): counts run ~1:45 against
// messages, so on the shared scale the series is a flat line on the floor.
const histSeries=["messages","human","ai","bestemmie"].filter(k=>!NOBEST||k!=="bestemmie");
const histLabel={messages:"messaggi",human:"umani",ai:"IA",bestemmie:"bestemmie"};
const histCol={messages:"muted",human:"human",ai:"ai",bestemmie:"classic"};
const histScale={bestemmie:"best"};          // default "y" for everything else
let uHist=null;
function drawHistLegend(){
  const lg=document.getElementById("hist-legend");
  lg.className="";
  lg.innerHTML='<p class="hist-hint">trascina in orizzontale (o pizzica col dito) per zoomare — i pannelli qui sopra si aggiornano · «⟲ tutto il periodo» (o doppio-click) per azzerare · clic sulla legenda per isolare una serie'+(NOBEST?'':' · le bestemmie stanno sull\'asse destro')+' · la URL segue lo zoom (condivisibile)</p>';
}
function ymd(ts){const d=new Date(ts*1000);
  return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0");}
function histRangeFromURL(xs){
  const p=new URLSearchParams(location.search), f=p.get("from"), t=p.get("to");
  if(!f&&!t) return {};
  const lo=xs[0], hi=xs[xs.length-1];
  let mn=f?Math.floor(Date.parse(f+"T00:00:00")/1000):lo;
  let mx=t?Math.floor(Date.parse(t+"T23:59:59")/1000):hi;
  if(isNaN(mn))mn=lo; if(isNaN(mx))mx=hi;
  return {min:Math.max(lo,Math.min(mn,mx)), max:Math.min(hi,Math.max(mn,mx))};
}
function histSyncURL(u){
  const xs=u.data[0]; if(!xs.length) return;
  const lo=xs[0], hi=xs[xs.length-1];
  const mn=u.scales.x.min, mx=u.scales.x.max;
  const p=new URLSearchParams(location.search);
  if(mn<=lo+1 && mx>=hi-1){p.delete("from");p.delete("to");}   // full extent → clean URL
  else{p.set("from",ymd(mn));p.set("to",ymd(mx));}
  const qs=p.toString();
  history.replaceState(null,"",location.pathname+(qs?"?"+qs:"")+location.hash);
}
// uPlot 1.6.32 binds mousedown/mousemove/mouseup ONLY — zero touch handling, so
// on a phone/tablet the chart is frozen. This adds the two gestures by hand:
// one finger = pan, two fingers = pinch-zoom on x. We preventDefault on touchmove,
// which also suppresses the synthetic mouse events that would fire uPlot's own
// drag-select afterwards. #history has touch-action:pan-y so the page still
// scrolls vertically over the chart, but horizontal + pinch reach us.
const HIST_MIN_SPAN=2*86400;      // don't zoom past a 2-day window
function histTouch(u,box){
  const ov=u.over;
  let mode=0, sMin=0, sMax=0, sDist=0, sAnchor=0, sX=0, pend=null;
  const extent=()=>{const xs=u.data[0]; return [xs[0], xs[xs.length-1]];};
  const plotW=()=>ov.getBoundingClientRect().width||1;
  const relX=t=>t.clientX-ov.getBoundingClientRect().left;
  const dist=ts=>Math.abs(ts[0].clientX-ts[1].clientX);
  // Slide the window inside the data extent instead of clipping it: a pan that
  // runs into the edge keeps its width, it doesn't squash against the wall.
  const commit=(mn,mx)=>{
    const [lo,hi]=extent(), span=Math.min(mx-mn, hi-lo);
    if(mn<lo){mn=lo;mx=lo+span;} if(mx>hi){mx=hi;mn=hi-span;}
    if(pend)cancelAnimationFrame(pend);
    pend=requestAnimationFrame(()=>{pend=null;u.setScale("x",{min:mn,max:mx});});
  };
  ov.addEventListener("touchstart",e=>{
    const t=e.touches; sMin=u.scales.x.min; sMax=u.scales.x.max;
    if(t.length===1){mode=1; sX=relX(t[0]);}
    else if(t.length>=2){mode=2; sDist=dist(t)||1;
      sAnchor=u.posToVal((relX(t[0])+relX(t[1]))/2,"x");}
    else mode=0;
  },{passive:true});
  ov.addEventListener("touchmove",e=>{
    if(!mode)return;
    const t=e.touches, span=sMax-sMin;
    if(mode===1&&t.length===1){
      e.preventDefault();
      const d=(sX-relX(t[0]))*span/plotW();
      commit(sMin+d, sMax+d);
    } else if(mode===2&&t.length>=2){
      e.preventDefault();
      const [lo,hi]=extent();
      const f=Math.min(Math.max(span*(sDist/(dist(t)||1)),HIST_MIN_SPAN),hi-lo);
      const frac=span?(sAnchor-sMin)/span:0.5;
      commit(sAnchor-frac*f, sAnchor-frac*f+f);
    }
  },{passive:false});
  ov.addEventListener("touchend",e=>{if(e.touches.length===0)mode=0;},{passive:true});
  ov.addEventListener("touchcancel",()=>{mode=0;},{passive:true});
}
function drawHistory(){
  const box=document.getElementById("history");
  if(uHist){uHist.destroy();uHist=null;}
  box.innerHTML="";
  const Hh=DATA.history; if(!Hh.length){box.textContent="—";return;}
  const xs=Hh.map(d=>Math.floor(Date.parse(d.date+"T00:00:00")/1000));
  const data=[xs,...histSeries.map(k=>Hh.map(d=>d[k]||0))];
  const grid={stroke:col("border"),width:1}, ticks={stroke:col("border"),width:1};
  const axFont='11px "DejaVu Sans Mono",ui-monospace,monospace';
  const rng=histRangeFromURL(xs);
  const dfmt=new Intl.DateTimeFormat("it-IT",{day:"2-digit",month:"short",year:"numeric"});
  const opts={
    width:Math.max(280,box.clientWidth||920), height:260,
    scales:{x:{time:true,min:rng.min,max:rng.max},
            y:{range:(u,mn,mx)=>[0,(mx*1.05)||1]},
            best:{range:(u,mn,mx)=>[0,(mx*1.15)||1]}},
    // uPlot paints the series colour on the marker BORDER (background comes from
    // series.fill, which we never set). Our CSS zeroes that border to keep the
    // swatch square, so mirror the stroke into the fill or every marker is grey.
    legend:{show:true, markers:{fill:(u,i)=>u.series[i].stroke(u,i)}},
    cursor:{drag:{x:true,y:false,uni:12}, points:{size:6}},
    axes:[
      // Buckets are per-DAY, so uPlot's sub-day splits at tight zoom would print
      // the same dd/mm six times in a row. Label the first tick of each day only.
      {stroke:col("muted"),grid:grid,ticks:ticks,font:axFont,
       values:(u,sp)=>{let prev=null; return sp.map(v=>{const d=new Date(v*1000);
         const s=String(d.getDate()).padStart(2,"0")+"/"+String(d.getMonth()+1).padStart(2,"0");
         if(s===prev)return null; prev=s; return s;});}},
      {stroke:col("muted"),grid:grid,ticks:ticks,font:axFont,size:46,
       values:(u,sp)=>sp.map(v=>NF.format(v))},
      // right axis: bestemmie only. No grid — a second set of horizontal lines
      // over the first is noise, and the left one already carries the reading.
      {scale:"best",side:1,stroke:col(histCol.bestemmie),
       grid:{show:false},ticks:{stroke:col(histCol.bestemmie),width:1},
       font:axFont,size:40,values:(u,sp)=>sp.map(v=>NF.format(v))},
    ].slice(0,NOBEST?2:3),
    series:[
      {value:(u,ts)=>ts==null?"":dfmt.format(new Date(ts*1000))},
      ...histSeries.map(k=>({label:histLabel[k],scale:histScale[k]||"y",
        stroke:col(histCol[k]),width:2,dash:k==="bestemmie"?[5,3]:undefined,
        points:{show:false}, value:(u,v)=>v==null?"–":NF.format(v)})),
    ],
    hooks:{setScale:[(u,key)=>{if(key==="x"){histSyncURL(u);applyRange(u.scales.x.min,u.scales.x.max,u);syncX(u,uPres);}}]},
  };
  uHist=new uPlot(opts,data,box);
  histTouch(uHist,box);
  // uPlot core has drag-zoom-IN but NO reset — wire zoom-OUT to full extent, two ways:
  // a visible button (discoverable) + dblclick (bonus). setScale hook re-aggregates.
  // .ondblclick=/.onclick= (not addEventListener) so theme redraws reassign, never stack.
  const toFull=()=>{const xf=uHist.data[0];
    if(xf.length)uHist.setScale("x",{min:xf[0],max:xf[xf.length-1]});};
  box.ondblclick=toFull;
  const rb=document.getElementById("hist-reset"); if(rb)rb.onclick=toFull;
}
addEventListener("resize",()=>{if(uHist){const b=document.getElementById("history");
  uHist.setSize({width:Math.max(280,b.clientWidth||920),height:260});}});

// ---- channel presence: daily MEDIAN of humans in channel, min/max as a band ----
// present_* is a STATE (membership replayed from NAMES + join/part/quit/nick/kick),
// not a sum: the dynamic panels must never re-aggregate it over a zoomed range.
// Median, not max, is the headline: a netsplit dumps ~20 sessions for a minute and
// would own any max-based bucket. Days with no reconstruction (before the bot ever
// joined) stay null so the line BREAKS instead of dropping to a fake zero.
let uPres=null;
let xSync=false;                 // guard: the two charts push x to each other
function syncX(from,to){
  if(xSync||!to) return;
  xSync=true;
  try{to.setScale("x",{min:from.scales.x.min,max:from.scales.x.max});}
  finally{xSync=false;}
}
function drawPresLegend(){
  const lg=document.getElementById("pres-legend"); if(!lg)return;
  lg.className="";
  lg.innerHTML='<p class="hist-hint">quante persone stavano in canale: la linea e\' la <b>mediana</b> del giorno, la banda dietro il <b>minimo/massimo</b> · la tratteggiata sono gli <b>attivi</b>, cioe\' chi ha <b>parlato</b> quel giorno (nick distinti): starci non vuol dire parlare, quindi sta quasi sempre sotto · lo zoom e\' condiviso col grafico qui sopra · ricostruito dai NAMES + join/part/quit/nick/kick, quindi un buco di log <b>congela</b> la membership e i netsplit fanno dirupi</p>';
}
function drawPresence(){
  const sec=document.getElementById("s-presence"); if(!sec)return;
  if(uPres){uPres.destroy();uPres=null;}
  const box=document.getElementById("presence");
  const Hh=(DATA&&DATA.history)||[];
  // Nothing to draw until the DB has presence rows — hide the whole section
  // rather than show an empty axis.
  // presence and DAU are independent: a channel with no NAMES-derived presence
  // still has talkers, so either one is enough to keep the section alive.
  if(!Hh.some(d=>d.present_med!=null||d.dau!=null)){sec.style.display="none";box.innerHTML="";return;}
  sec.style.display="";
  box.innerHTML="";
  const xs=Hh.map(d=>Math.floor(Date.parse(d.date+"T00:00:00")/1000));
  const pick=k=>Hh.map(d=>d[k]==null?null:d[k]);
  // series order matters: the band fills between #1 (max) and #2 (min), so the
  // median (#3) is drawn last and stays on top of the fill.
  const data=[xs,pick("present_max"),pick("present_min"),pick("present_med"),pick("dau")];
  const grid={stroke:col("border"),width:1}, ticks={stroke:col("border"),width:1};
  const axFont='11px "DejaVu Sans Mono",ui-monospace,monospace';
  const rng=histRangeFromURL(xs);
  const dfmt=new Intl.DateTimeFormat("it-IT",{day:"2-digit",month:"short",year:"numeric"});
  const edge=col("human")+"66", fill=col("human")+"26";
  const opts={
    width:Math.max(280,box.clientWidth||920), height:200,
    scales:{x:{time:true,min:rng.min,max:rng.max},
            y:{range:(u,mn,mx)=>[0,(mx*1.1)||1]}},
    legend:{show:true, markers:{fill:(u,i)=>u.series[i].stroke(u,i)}},
    cursor:{drag:{x:true,y:false,uni:12}, points:{size:6}},
    bands:[{series:[1,2], fill:fill}],
    axes:[
      {stroke:col("muted"),grid:grid,ticks:ticks,font:axFont,
       values:(u,sp)=>{let prev=null; return sp.map(v=>{const d=new Date(v*1000);
         const s=String(d.getDate()).padStart(2,"0")+"/"+String(d.getMonth()+1).padStart(2,"0");
         if(s===prev)return null; prev=s; return s;});}},
      {stroke:col("muted"),grid:grid,ticks:ticks,font:axFont,size:46,
       values:(u,sp)=>sp.map(v=>NF.format(v))},
    ],
    series:[
      {value:(u,ts)=>ts==null?"":dfmt.format(new Date(ts*1000))},
      {label:"max",   scale:"y",stroke:edge,width:1,points:{show:false},
       value:(u,v)=>v==null?"–":NF.format(v)},
      {label:"min",   scale:"y",stroke:edge,width:1,points:{show:false},
       value:(u,v)=>v==null?"–":NF.format(v)},
      {label:"mediana",scale:"y",stroke:col("human"),width:2,points:{show:false},
       value:(u,v)=>v==null?"–":NF.format(v)},
      {label:"attivi",scale:"y",stroke:col("accent"),width:2,dash:[6,3],points:{show:false},
       value:(u,v)=>v==null?"–":NF.format(v)},
    ],
    hooks:{setScale:[(u,key)=>{if(key==="x")syncX(u,uHist);}]},
  };
  uPres=new uPlot(opts,data,box);
  histTouch(uPres,box);
  box.ondblclick=()=>{const xf=uPres.data[0];
    if(xf.length)uPres.setScale("x",{min:xf[0],max:xf[xf.length-1]});};
}
addEventListener("resize",()=>{if(uPres){const b=document.getElementById("presence");
  uPres.setSize({width:Math.max(280,b.clientWidth||920),height:200});}});

// ---- horizontal bars (talkers / bestemmie) ----
function hbars(box, items, valFn, labelFn, colorFn, tipFn){
  box.innerHTML="";
  const rowH=22, gap=6, padL=140, W=920, padR=54;
  const H=items.length*(rowH+gap)+4;
  const max=Math.max(1,...items.map(valFn));
  const s=svg(W,H);
  items.forEach((it,i)=>{
    const y=i*(rowH+gap)+2;
    const bw=(W-padL-padR)*(valFn(it)/max);
    s.appendChild(el("rect",{x:padL,y:y,width:W-padL-padR,height:rowH,rx:4,class:"bar-track"}));
    const rect=el("rect",{x:padL,y:y,width:Math.max(2,bw),height:rowH,rx:4,fill:colorFn(it)});
    hover(rect,()=>tipFn(it)); s.appendChild(rect);
    s.appendChild(el("text",{x:padL-8,y:y+rowH/2+4,"text-anchor":"end",class:"rowlabel"},[textNode(labelFn(it))]));
    s.appendChild(el("text",{x:padL+Math.max(2,bw)+6,y:y+rowH/2+4,class:"dlabel"},[textNode(NF.format(valFn(it)))]));
  });
  box.appendChild(s);
}
function drawTalkers(){
  const items=DATA.top_talkers.slice(0,15);
  hbars(document.getElementById("talkers"), items,
    it=>it.count, it=>it.nick, it=>col(it.klass),
    it=>`<b>${it.nick}</b><br>${NF.format(it.count)} msg · ${CLASSES.find(c=>c[0]==it.klass)?.[1]||it.klass}`);
}
// dynamic: tier + scale marker recompute from the range aggregate
function drawBestGauge(){
  const p=AGG.per100;
  document.getElementById("tier").textContent=AGG.tier;
  document.getElementById("best-ratio").textContent=`(${p} ogni 100 messaggi · ${NF.format(AGG.bestemmie)} totali)`;
  // scale marker position: map per_100 onto tier band centers (0..100 across 5 bands)
  const bands=[0.5,2,5,12,30]; let pos=0;
  if(p>=bands[3])pos=80+Math.min(20,(p-12)/18*20);
  else{for(let i=0;i<4;i++){if(p<bands[i]){const lo=i?bands[i-1]:0;pos=i*20+ (p-lo)/(bands[i]-lo)*20;break;}}}
  document.getElementById("scale-marker").style.left=Math.max(0,Math.min(99,pos))+"%";
}
// static: per-nick top blasphemers, always all-time
function drawBestUsers(){
  const items=DATA.bestemmie.by_user.slice(0,12);
  const box=document.getElementById("best");
  if(!items.length){box.innerHTML='<span style="color:var(--muted)">nessuna bestemmia registrata — che catechismo</span>';return;}
  hbars(box, items,
    it=>it.count, it=>it.nick, ()=>col("classic"),
    it=>`<b>${it.nick}</b><br>${NF.format(it.count)} bestemmie`);
}
// static: distinct human nicks (non-additive, always all-time)
function drawDistinct(){
  document.getElementById("distinct-num").innerHTML=`${NF.format(DATA.active_users)} <small>nick umani</small>`;
}
function drawTransport(){
  const box=document.getElementById("transport");box.innerHTML="";
  const irc=AGG.irc||0, tg=AGG.telegram||0, tot=irc+tg||1;
  const W=560,H=34,gap=2,s=svg(W,H);
  const parts=[["irc",irc,"accent"],["telegram",tg,"classic"]]; let x=0;
  for(const [lab,v,c] of parts){
    if(v===0)continue; const w=Math.max(0,(v/tot)*W-gap);
    const rect=el("rect",{x:x,y:0,width:w,height:H,rx:5,fill:col(c)});
    hover(rect,()=>`<b>${lab}</b><br>${NF.format(v)} msg umani · ${(100*v/tot).toFixed(1)}%`);
    s.appendChild(rect);
    if(w>60)s.appendChild(el("text",{x:x+w/2,y:H/2+4,"text-anchor":"middle",class:"dlabel"},[textNode(`${lab} ${(100*v/tot).toFixed(0)}%`)]));
    x+=w+gap;
  }
  box.appendChild(s);
}

// ---- table view ----
function drawTable(){
  let h=`<table><tr><th>metrica</th><th>valore</th></tr>`;
  const rows=[["messaggi",NF.format(AGG.messages)],["umani",NF.format(AGG.human)],["IA",NF.format(AGG.ai)],
    ["bot classici",NF.format(AGG.classic)],["servizi",NF.format(AGG.service)],
    ["umani distinti (tutto il periodo)",NF.format(DATA.active_users)],
    ["attivi al giorno (mediana)",NF.format(AGG.dau_med)],
    ["attivi distinti nel periodo",NF.format(AGG.dau_union)],
    ["ratio IA:umani",(AGG.ai_vs_human||0).toFixed(4)],["ratio tutti-bot:umani",(AGG.allbots_vs_human||0).toFixed(4)],
    ["msg IRC (umani)",NF.format(AGG.irc||0)],["msg Telegram (umani)",NF.format(AGG.telegram||0)],
    ["bestemmie totali",NF.format(AGG.bestemmie)],["bestemmie/100 msg",AGG.per100],["tier",AGG.tier]].filter(r=>!NOBEST||!/bestemmie|tier/.test(r[0]));
  for(const [k,v] of rows)h+=`<tr><td>${k}</td><td class="n mono">${v}</td></tr>`;
  h+=`</table>`;
  document.getElementById("table").innerHTML=h;
}

// dynamic panels: recompute the range aggregate, then redraw everything that reads it
function renderDynamic(){
  computeAgg();
  drawSpan();drawRatio();drawComposition();drawLegend();drawTiles();
  drawHourly();drawTransport();if(!NOBEST)drawBestGauge();drawTable();
  updateRangeUI();
}
// static panels: all-time, per-nick — redrawn only on full render (load / theme), not on zoom
function renderStatic(){drawTalkers();if(!NOBEST)drawBestUsers();drawDistinct();}
function renderAll(){
  if(!DATA) return;
  RANGE=rangeFromURL();
  renderStatic();
  renderDynamic();
  drawHistLegend();drawHistory();
  drawPresLegend();drawPresence();
}
fetch(SLUG+".json?_="+Date.now(),{cache:"no-store"})
  .then(r=>{if(!r.ok)throw new Error("HTTP "+r.status);return r.json();})
  .then(d=>{DATA=d;renderAll();})
  .catch(e=>{document.getElementById("span").textContent="errore nel caricamento dati ("+e+")";});

// ---- theme toggle ----
const root=document.documentElement;
function setTheme(t){root.setAttribute("data-theme",t);localStorage.setItem("sniffo-theme",t);renderAll();}
document.getElementById("themebtn").onclick=()=>setTheme(root.getAttribute("data-theme")==="dark"?"light":"dark");
(function(){const saved=localStorage.getItem("sniffo-theme");
  if(saved)root.setAttribute("data-theme",saved);
  else if(matchMedia("(prefers-color-scheme: light)").matches)root.setAttribute("data-theme","light");})();

// ---- particle-network background ----
(function(){
  const c=document.getElementById("bg"),x=c.getContext("2d");
  const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;
  let W,Hh,pts;
  function acc(){return getComputedStyle(root).getPropertyValue("--accent").trim();}
  function init(){W=c.width=innerWidth;Hh=c.height=innerHeight;
    const n=Math.min(70,Math.floor(W*Hh/22000));
    pts=Array.from({length:n},()=>({x:Math.random()*W,y:Math.random()*Hh,
      vx:(Math.random()-.5)*.25,vy:(Math.random()-.5)*.25}));}
  function frame(){
    x.clearRect(0,0,W,Hh);
    const a=acc();
    for(const p of pts){p.x+=p.vx;p.y+=p.vy;
      if(p.x<0||p.x>W)p.vx*=-1; if(p.y<0||p.y>Hh)p.vy*=-1;}
    for(let i=0;i<pts.length;i++){
      for(let j=i+1;j<pts.length;j++){
        const dx=pts[i].x-pts[j].x,dy=pts[i].y-pts[j].y,d=Math.hypot(dx,dy);
        if(d<130){x.strokeStyle=a;x.globalAlpha=(1-d/130)*.14;x.lineWidth=1;
          x.beginPath();x.moveTo(pts[i].x,pts[i].y);x.lineTo(pts[j].x,pts[j].y);x.stroke();}
      }
    }
    x.globalAlpha=.5;x.fillStyle=a;
    for(const p of pts){x.beginPath();x.arc(p.x,p.y,1.5,0,7);x.fill();}
    x.globalAlpha=1;
    if(!reduce)requestAnimationFrame(frame);
  }
  init();frame();
  addEventListener("resize",()=>{init();if(reduce)frame();});
})();
</script>
</body>
</html>
"""


def _hide_blasphemy():
    try:
        cfg = json.loads((HERE / "classes.json").read_text())
        return {c.lower() for c in cfg.get("hide_blasphemy", [])}
    except Exception:
        return set()


def render_channel(metric):
    net = metric["network"].capitalize()
    chan = metric["channel"]
    # Static shell; data fetched at runtime from <slug>.json?_=<ts> (cache-busted,
    # so the zone-wide CF edge cache never serves a stale snapshot — tsk.json model).
    return (TEMPLATE
            .replace("__TITLE__", html.escape(f"{chan} {net}"))
            .replace("__CHAN__", html.escape(chan))
            .replace("__NET__", html.escape(net))
            .replace("__SLUG__", metric["slug"])
            .replace("__NOBEST__", "true" if chan.lower() in _hide_blasphemy() else "false")
            .replace("__UPLOT_CSS__", _UPLOT_CSS)
            .replace("__UPLOT_JS__", _UPLOT_JS))


INDEX_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>sniffo stats · indice</title>
<style>
:root{--bg:#0a0a0a;--bg-alt:#111;--fg:#e0e0e0;--accent:#5fafd7;--muted:#707070;--border:#1f1f1f;--panel:#0f0f0f;
  --human:#55a23f;--ai:#6484de;--classic:#d85f9c}
:root[data-theme="light"]{--bg:#fff;--bg-alt:#f5f5f5;--fg:#111;--accent:#00007f;--muted:#7f7f7f;--border:#c0c0c0;--panel:#fafafa;
  --human:#2e7d32;--ai:#3049b8;--classic:#b02c72}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
  font-family:"DejaVu Sans Mono",ui-monospace,Menlo,Consolas,monospace;font-size:14px;line-height:1.5;overflow-x:hidden}
#bg{position:fixed;inset:0;z-index:-1;pointer-events:none}
.wrap{max-width:900px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 2px}.dedica{color:var(--muted);font-style:italic;font-size:12px;margin-bottom:18px}
.themebtn{float:right;background:var(--bg-alt);color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:4px 10px;cursor:pointer;font:inherit;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left}
th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em}
td.n{text-align:right;font-variant-numeric:tabular-nums}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.chan{font-weight:700}.net{color:var(--muted);font-size:11px}
.bar{height:7px;border-radius:4px;background:var(--bg-alt);overflow:hidden;min-width:80px;display:flex}
.bar i{display:block;height:100%}
.tier{padding:2px 8px;border-radius:20px;background:var(--bg-alt);border:1px solid var(--border);font-size:11px;white-space:nowrap}
.note{color:var(--muted);font-size:11px;margin-top:20px}
</style></head>
<body><canvas id="bg"></canvas><div class="wrap">
<button class="themebtn" id="themebtn">tema</button>
<h1>📊 sniffo stats</h1><div class="dedica">a dante lo scettico</div>
<table><thead><tr><th>canale</th><th class="n">messaggi</th><th>umani / IA</th><th class="n">IA:umani</th><th>bestemmie</th></tr></thead>
<tbody id="rows"></tbody></table>
<div class="note">URL privati e non indicizzati. Nick reali — non condividere i link a canali riservati.</div>
</div>
<div id="tipwrap"></div>
<script>
const IDXSLUG="__IDXSLUG__";let IDX={channels:[]};const NF=new Intl.NumberFormat('it-IT');
const rows=document.getElementById('rows');
function col(k){return getComputedStyle(document.documentElement).getPropertyValue('--'+k).trim();}
function draw(){rows.innerHTML="";
  (IDX.channels||[]).forEach(c=>{
    const tr=document.createElement('tr');
    const h=c.human_pct, a=c.ai_pct, o=Math.max(0,100-h-a);
    tr.innerHTML=`<td><a href="${c.slug}.html"><span class="chan">${c.channel}</span></a> <span class="net">${c.network}</span></td>
      <td class="n">${NF.format(c.messages)}</td>
      <td><div class="bar"><i style="width:${h}%;background:${col('human')}"></i><i style="width:${a}%;background:${col('ai')}"></i><i style="width:${o}%;background:${col('classic')}"></i></div>
        <span class="net">${h}% / ${a}%</span></td>
      <td class="n">${c.ai_vs_human==null?'—':c.ai_vs_human.toFixed(2)}</td>
      <td><span class="tier">${c.tier}</span> <span class="net">${c.bestemmia_per_100}/100</span></td>`;
    rows.appendChild(tr);
  });}
fetch(IDXSLUG+".json?_="+Date.now(),{cache:"no-store"})
  .then(r=>{if(!r.ok)throw new Error("HTTP "+r.status);return r.json();})
  .then(d=>{IDX=d;draw();})
  .catch(e=>{rows.innerHTML='<tr><td colspan="5">errore nel caricamento ('+e+')</td></tr>';});
const root=document.documentElement;
function setTheme(t){root.setAttribute('data-theme',t);localStorage.setItem('sniffo-theme',t);draw();}
document.getElementById('themebtn').onclick=()=>setTheme(root.getAttribute('data-theme')==='dark'?'light':'dark');
(function(){const s=localStorage.getItem('sniffo-theme');if(s)root.setAttribute('data-theme',s);
  else if(matchMedia('(prefers-color-scheme: light)').matches)root.setAttribute('data-theme','light');})();
(function(){const c=document.getElementById("bg"),x=c.getContext("2d");
  const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;let W,H,pts;
  function acc(){return getComputedStyle(root).getPropertyValue("--accent").trim();}
  function init(){W=c.width=innerWidth;H=c.height=innerHeight;const n=Math.min(60,Math.floor(W*H/24000));
    pts=Array.from({length:n},()=>({x:Math.random()*W,y:Math.random()*H,vx:(Math.random()-.5)*.25,vy:(Math.random()-.5)*.25}));}
  function frame(){x.clearRect(0,0,W,H);const a=acc();
    for(const p of pts){p.x+=p.vx;p.y+=p.vy;if(p.x<0||p.x>W)p.vx*=-1;if(p.y<0||p.y>H)p.vy*=-1;}
    for(let i=0;i<pts.length;i++)for(let j=i+1;j<pts.length;j++){const dx=pts[i].x-pts[j].x,dy=pts[i].y-pts[j].y,d=Math.hypot(dx,dy);
      if(d<130){x.strokeStyle=a;x.globalAlpha=(1-d/130)*.13;x.lineWidth=1;x.beginPath();x.moveTo(pts[i].x,pts[i].y);x.lineTo(pts[j].x,pts[j].y);x.stroke();}}
    x.globalAlpha=.5;x.fillStyle=a;for(const p of pts){x.beginPath();x.arc(p.x,p.y,1.5,0,7);x.fill();}x.globalAlpha=1;
    if(!reduce)requestAnimationFrame(frame);}
  init();frame();addEventListener("resize",()=>{init();if(reduce)frame();});})();
</script></body></html>
"""


def render_index(index_slug):
    # Static shell; index data fetched at runtime from <index_slug>.json (secret name).
    return INDEX_TEMPLATE.replace("__IDXSLUG__", index_slug)


def index_slug_for():
    import hashlib
    return "idx-" + hashlib.sha256((M.slug_salt() + "|INDEX").encode()).hexdigest()[:14]


def render_all(conn, outdir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    idx = M.export(conn, outdir)  # writes per-channel <slug>.json + index.json (index.json NOT deployed)
    for network, channel, _ in M.list_channels(conn):
        m = M.channel_metrics(conn, network, channel)
        (out / f"{m['slug']}.html").write_text(render_channel(m))
    # index page + its data both at hashed names (vjt's private entry point)
    islug = index_slug_for()
    (out / f"{islug}.html").write_text(render_index(islug))
    (out / f"{islug}.json").write_text(json.dumps(
        {"dedication": "a dante lo scettico", "channels": idx}, ensure_ascii=False))
    return idx, islug


def _main(argv):
    if len(argv) >= 2 and argv[0] == "render":
        conn = sqlite3.connect(argv[1])
        outdir = argv[2] if len(argv) > 2 else str(HERE / "out")
        idx, index_slug = render_all(conn, outdir)
        print(f"rendered {len(idx)} channel pages + index -> {outdir}")
        print(f"  INDEX: {index_slug}.html")
        for r in idx:
            print(f"  {r['channel']:20} {r['slug']}.html")
        return
    print("usage: render.py render <db> [outdir]")


if __name__ == "__main__":
    _main(sys.argv[1:])
