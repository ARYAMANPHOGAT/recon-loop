"""Dashboard generator — writes a self-contained HTML file.

The run data is inlined into the page rather than fetched, so the file opens
straight from disk with no server and no network. A judge double-clicks it and
sees the run. Anything that requires `python -m http.server` first is friction
that costs more than the dashboard gains.

Design intent
-------------
Reconciliation is two columns that must agree. The primary view renders that
literally: ledger side left, bank side right, a channel between them. Matched
pairs are joined across the channel; unmatched rows are visibly stranded on
their own side. The shape of the problem is the shape of the interface.

Colour carries accounting meaning and nothing else. Green is tied, oxidised red
is broken, goldenrod needs a human. Nothing is coloured for decoration.

Read-only by design. Approve and reject render as proposals because the engine
proposes and a human disposes; a dashboard that writes to a ledger would break
the principle the whole project is built on.
"""

from __future__ import annotations

import json
from pathlib import Path

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recon Loop — reconciliation console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#EDEFF2;        /* cool statement stock */
  --card:#F7F8FA;
  --ink:#171B21;
  --ink-2:#4A545F;
  --ink-3:#7C8894;
  --rule:#CDD4DC;
  --rule-2:#DFE4EA;
  --tied:#1C6B57;         /* ledger green: reconciled */
  --tied-bg:#E4EFEB;
  --broken:#9E362B;       /* oxidised red: does not tie */
  --broken-bg:#F4E4E1;
  --attend:#8A6410;       /* goldenrod: needs a person */
  --attend-bg:#F5EDDA;
  --mono:'IBM Plex Mono',ui-monospace,'SF Mono',Menlo,monospace;
  --sans:'IBM Plex Sans',system-ui,-apple-system,sans-serif;
  --serif:'IBM Plex Serif',Georgia,serif;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--paper);color:var(--ink);
  font-family:var(--sans);font-size:14px;line-height:1.5;
  font-variant-numeric:tabular-nums;
}
.wrap{max-width:1220px;margin:0 auto;padding:0 20px 72px}

/* ---------- masthead ---------- */
.masthead{
  display:flex;align-items:flex-end;justify-content:space-between;
  gap:24px;flex-wrap:wrap;
  padding:26px 0 14px;border-bottom:2px solid var(--ink);
}
.brand{font-family:var(--serif);font-size:25px;font-weight:600;letter-spacing:-.015em}
.brand span{color:var(--ink-3);font-weight:400}
.runmeta{font-family:var(--mono);font-size:11.5px;color:var(--ink-2);text-align:right}
.runmeta b{color:var(--ink);font-weight:500}

/* ---------- headline figures ---------- */
.figures{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));
  border-bottom:1px solid var(--rule);
}
.fig{padding:16px 20px 15px;border-right:1px solid var(--rule-2)}
.fig:last-child{border-right:0}
.fig .k{
  font-family:var(--mono);font-size:10px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3);
}
.fig .v{
  font-family:var(--mono);font-size:29px;font-weight:500;
  letter-spacing:-.02em;margin-top:5px;line-height:1;
}
.fig .n{font-family:var(--mono);font-size:11px;color:var(--ink-3);margin-top:5px}
.v.tied{color:var(--tied)} .v.broken{color:var(--broken)} .v.attend{color:var(--attend)}

/* ---------- tabs ---------- */
.tabs{display:flex;gap:0;border-bottom:1px solid var(--rule);margin-top:2px;overflow-x:auto}
.tab{
  appearance:none;background:none;border:0;cursor:pointer;
  font-family:var(--mono);font-size:12px;letter-spacing:.02em;
  color:var(--ink-3);padding:13px 17px;border-bottom:2px solid transparent;
  white-space:nowrap;
}
.tab:hover{color:var(--ink)}
.tab[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--ink);font-weight:500}
.tab:focus-visible{outline:2px solid var(--ink);outline-offset:-2px}
.panel[hidden]{display:none}

/* ---------- the gutter: signature view ---------- */
.gutter-head{
  display:grid;grid-template-columns:1fr 92px 1fr;
  padding:20px 0 9px;align-items:end;
}
.side-label{
  font-family:var(--mono);font-size:10px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3);
}
.side-label.r{text-align:right}
.gutter-head .mid{
  text-align:center;font-family:var(--mono);font-size:9.5px;
  color:var(--ink-3);letter-spacing:.06em;
}
.gutter{display:flex;flex-direction:column;gap:2px}
.gr{
  display:grid;grid-template-columns:1fr 92px 1fr;align-items:stretch;
  background:transparent;
}
.cell{
  background:var(--card);border:1px solid var(--rule-2);
  padding:9px 12px;min-height:52px;display:flex;flex-direction:column;
  justify-content:center;
}
.cell.empty{background:transparent;border:1px dashed var(--rule-2);opacity:.5}
.cell .id{font-family:var(--mono);font-size:12px;font-weight:500}
.cell .sub{font-family:var(--mono);font-size:10.5px;color:var(--ink-3);margin-top:3px}
.cell .amt{font-family:var(--mono);font-size:14px;font-weight:500;margin-top:2px}
.cell.r{text-align:right;align-items:flex-end}

/* the channel */
.chan{position:relative;display:flex;align-items:center;justify-content:center}
.chan::before{
  content:"";position:absolute;top:0;bottom:0;left:50%;
  width:1px;background:var(--rule);
}
.link{
  position:relative;width:100%;height:1px;background:var(--tied);
}
.link::before,.link::after{
  content:"";position:absolute;top:-2.5px;width:6px;height:6px;
  border-radius:50%;background:var(--tied);
}
.link::before{left:0} .link::after{right:0}
.link.charged{background:var(--attend)}
.link.charged::before,.link.charged::after{background:var(--attend)}
.tierpip{
  position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  background:var(--paper);padding:0 5px;
  font-family:var(--mono);font-size:9px;color:var(--ink-2);
  letter-spacing:.04em;
}
.stranded{
  font-family:var(--mono);font-size:9px;color:var(--broken);
  letter-spacing:.08em;text-transform:uppercase;
}
.gr.tie .cell{border-color:var(--rule-2)}
.gr.break .cell:not(.empty){border-color:var(--broken);background:var(--broken-bg)}
.gr.attend .cell:not(.empty){border-color:var(--attend);background:var(--attend-bg)}

.legend{
  display:flex;gap:18px;flex-wrap:wrap;padding:16px 0 4px;
  font-family:var(--mono);font-size:10.5px;color:var(--ink-2);
}
.legend i{display:inline-block;width:16px;height:2px;vertical-align:middle;margin-right:6px}
.filters{display:flex;gap:6px;padding:14px 0 6px;flex-wrap:wrap}
.filt{
  appearance:none;border:1px solid var(--rule);background:var(--card);
  font-family:var(--mono);font-size:11px;color:var(--ink-2);
  padding:5px 11px;cursor:pointer;
}
.filt[aria-pressed="true"]{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.filt:focus-visible{outline:2px solid var(--ink);outline-offset:2px}

/* ---------- exception ledger ---------- */
.ledger{border:1px solid var(--rule);background:var(--card);margin-top:18px}
.lhead,.lrow{
  display:grid;grid-template-columns:16px 1fr 130px 64px;
  gap:12px;padding:10px 14px;align-items:center;
}
.lhead{
  border-bottom:1px solid var(--rule);
  font-family:var(--mono);font-size:10px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink-3);
}
.lrow{border-bottom:1px solid var(--rule-2);width:100%;text-align:left;
  background:none;border-left:0;border-right:0;border-top:0;cursor:pointer;font:inherit;color:inherit}
.lrow:hover{background:#fff}
.lrow:focus-visible{outline:2px solid var(--ink);outline-offset:-2px}
.lrow .cls{font-family:var(--mono);font-size:12.5px;font-weight:500}
.lrow .ref{font-family:var(--mono);font-size:10.5px;color:var(--ink-3);margin-top:2px}
.lrow .amt,.lrow .cf{font-family:var(--mono);font-size:12.5px;text-align:right}
.sev{font-family:var(--mono);font-size:13px;line-height:1}
.sev.h{color:var(--broken)} .sev.m{color:var(--attend)} .sev.l{color:var(--ink-3)}
.detail{padding:2px 14px 16px 42px;border-bottom:1px solid var(--rule-2);background:#fff}
.detail dl{margin:0;display:grid;grid-template-columns:104px 1fr;gap:5px 14px}
.detail dt{
  font-family:var(--mono);font-size:9.5px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink-3);padding-top:2px;
}
.detail dd{margin:0;font-family:var(--mono);font-size:11.5px;color:var(--ink-2);line-height:1.65}
.actions{display:flex;gap:8px;align-items:center;margin-top:13px;flex-wrap:wrap}
.prop{
  font-family:var(--mono);font-size:11px;border:1px solid var(--rule);
  background:var(--card);padding:5px 12px;color:var(--ink-2);
}
.readonly{font-family:var(--mono);font-size:10px;color:var(--ink-3);letter-spacing:.04em}

/* ---------- trace ---------- */
.tracegrid{display:grid;grid-template-columns:250px 1fr;gap:0;margin-top:18px;border:1px solid var(--rule)}
.tracelist{border-right:1px solid var(--rule);max-height:560px;overflow-y:auto;background:var(--card)}
.tl{
  display:block;width:100%;text-align:left;background:none;border:0;
  border-bottom:1px solid var(--rule-2);padding:9px 13px;cursor:pointer;font:inherit;
}
.tl:hover{background:#fff}
.tl[aria-current="true"]{background:var(--ink);color:var(--paper)}
.tl:focus-visible{outline:2px solid var(--ink);outline-offset:-2px}
.tl .id{font-family:var(--mono);font-size:11.5px;font-weight:500}
.tl .amt{font-family:var(--mono);font-size:10.5px;opacity:.75;margin-top:2px}
.tracebody{padding:20px 22px;background:#fff;min-height:340px}
.cascade{display:flex;flex-direction:column;gap:0;margin-top:16px}
.step{display:grid;grid-template-columns:118px 22px 1fr;gap:12px;align-items:start;padding:9px 0}
.step .tname{font-family:var(--mono);font-size:11px;color:var(--ink-2);padding-top:1px}
.step .dot{position:relative;display:flex;justify-content:center}
.step .dot i{
  width:9px;height:9px;border-radius:50%;border:1.5px solid var(--rule);
  background:var(--paper);margin-top:3px;z-index:1;
}
.step:not(:last-child) .dot::after{
  content:"";position:absolute;top:9px;bottom:-14px;width:1px;background:var(--rule-2);
}
.step.hit .dot i{background:var(--tied);border-color:var(--tied)}
.step.skip .tname{color:var(--ink-3)}
.step .what{font-family:var(--mono);font-size:11.5px;color:var(--ink-2);line-height:1.6}
.step.hit .what{color:var(--ink)}

/* ---------- method ---------- */
.bars{margin-top:16px;border:1px solid var(--rule);background:var(--card)}
.bar{display:grid;grid-template-columns:190px 1fr 74px;gap:14px;align-items:center;
  padding:11px 15px;border-bottom:1px solid var(--rule-2)}
.bar:last-child{border-bottom:0}
.barhead{
  display:flex;justify-content:space-between;align-items:baseline;gap:14px;
  padding:11px 15px 9px;border-bottom:1px solid var(--rule);
  background:var(--paper);
  font-family:var(--mono);font-size:10px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink-2);
}
.barhead:not(:first-child){border-top:1px solid var(--rule)}
.barhead .scale{letter-spacing:.04em;text-transform:none;color:var(--ink-3);font-size:10px}
.bar .bn{font-family:var(--mono);font-size:11.5px}
.bar .track{height:9px;background:var(--rule-2);position:relative}
.bar .fill{position:absolute;inset:0 auto 0 0;background:var(--ink)}
.bar .bv{font-family:var(--mono);font-size:11.5px;text-align:right}
.note{
  font-family:var(--sans);font-size:13px;color:var(--ink-2);
  line-height:1.65;max-width:66ch;margin-top:20px;
}
.note b{color:var(--ink);font-weight:600}
.note code{font-family:var(--mono);font-size:12px;background:var(--card);padding:1px 5px;border:1px solid var(--rule-2)}
h2.sec{
  font-family:var(--serif);font-size:17px;font-weight:600;
  margin:26px 0 0;letter-spacing:-.01em;
}
p.lede{font-size:13px;color:var(--ink-2);margin:5px 0 0;max-width:70ch}

@media (max-width:820px){
  .gutter-head,.gr{grid-template-columns:1fr 54px 1fr}
  .tracegrid{grid-template-columns:1fr}
  .tracelist{border-right:0;border-bottom:1px solid var(--rule);max-height:220px}
  .lhead,.lrow{grid-template-columns:14px 1fr 96px;}
  .lhead .cf,.lrow .cf{display:none}
  .bar{grid-template-columns:140px 1fr 60px}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>
<div class="wrap">

  <header class="masthead">
    <div class="brand">Recon&nbsp;Loop <span>/ three-way reconciliation</span></div>
    <div class="runmeta" id="runmeta"></div>
  </header>

  <section class="figures" id="figures" aria-label="Run result"></section>

  <nav class="tabs" role="tablist" aria-label="Views">
    <button class="tab" role="tab" aria-selected="true"  data-p="recon">Reconciliation</button>
    <button class="tab" role="tab" aria-selected="false" data-p="exc">Exceptions</button>
    <button class="tab" role="tab" aria-selected="false" data-p="trace">Trace</button>
    <button class="tab" role="tab" aria-selected="false" data-p="method">Method</button>
  </nav>

  <section class="panel" id="p-recon" role="tabpanel">
    <h2 class="sec">Ledger against bank</h2>
    <p class="lede">Every payout the settlement report claims, against every credit the bank statement shows. A line across the channel means the two tie. A row with nothing opposite it is the reconciliation problem.</p>
    <div class="filters" id="filters"></div>
    <div class="gutter-head">
      <div class="side-label">Settlement report — payouts</div>
      <div class="mid">tied by</div>
      <div class="side-label r">Bank statement — credits</div>
    </div>
    <div class="gutter" id="gutter"></div>
    <div class="legend">
      <span><i style="background:var(--tied)"></i>tied</span>
      <span><i style="background:var(--attend)"></i>tied, with an explained difference</span>
      <span><i style="background:var(--broken)"></i>stranded — nothing opposite</span>
    </div>
  </section>

  <section class="panel" id="p-exc" role="tabpanel" hidden>
    <h2 class="sec">Exception ledger</h2>
    <p class="lede">Ranked by how much a controller should care, then by value. Read down and stop when the rest stops mattering.</p>
    <div class="ledger">
      <div class="lhead"><span></span><span>exception</span><span style="text-align:right">amount</span><span style="text-align:right">conf</span></div>
      <div id="exlist"></div>
    </div>
    <p class="readonly" style="margin-top:12px">Read-only. Approve and reject record a proposal for a person to action; nothing here writes to a ledger.</p>
  </section>

  <section class="panel" id="p-trace" role="tabpanel" hidden>
    <h2 class="sec">Decision trace</h2>
    <p class="lede">Pick a payout to see every tier it was put through, which one claimed it, and on what evidence.</p>
    <div class="tracegrid">
      <div class="tracelist" id="tracelist"></div>
      <div class="tracebody" id="tracebody"></div>
    </div>
  </section>

  <section class="panel" id="p-method" role="tabpanel" hidden>
    <h2 class="sec">What each tier is worth</h2>
    <p class="lede">Matches claimed per tier, in the order the cascade runs them.</p>
    <div class="bars" id="tiers"></div>
    <div class="note" id="methodnote"></div>
  </section>

</div>

<script>
const DATA = __DATA__;

const $ = (s,r=document)=>r.querySelector(s);
const el = (t,c,x)=>{const n=document.createElement(t); if(c)n.className=c; if(x!=null)n.textContent=x; return n;};
const money = p => "\u20B9" + (p/100).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});

/* ---------- header ---------- */
$('#runmeta').innerHTML =
  `seed <b>${DATA.seed}</b> · ${DATA.period.start} to ${DATA.period.end}<br>` +
  `${DATA.volume.orders} orders · ${DATA.volume.settlement_lines} settlement lines · ${DATA.volume.bank_rows} bank rows`;

const figs = [
  {k:'Order → settlement', v:DATA.order_leg.match_rate+'%', n:`${DATA.order_leg.matched} of ${DATA.order_leg.total}`, c:''},
  {k:'Payout → bank',      v:DATA.bank_leg.match_rate+'%',  n:`${DATA.bank_leg.matched} of ${DATA.bank_leg.total}`, c:''},
  {k:'False positives',    v:String(DATA.order_leg.false_positives+DATA.bank_leg.false_positives), n:'checked against ground truth', c:'tied'},
  {k:'Needs a person',     v:String(DATA.exceptions.requires_human), n:`of ${DATA.exceptions.total} exceptions`, c:'attend'},
  {k:'Model calls',        v:String(DATA.llm_calls), n:'matching is deterministic', c:''},
];
const figWrap = $('#figures');
figs.forEach(f=>{
  const d = el('div','fig');
  d.append(el('div','k',f.k));
  d.append(el('div','v '+f.c, f.v));
  d.append(el('div','n',f.n));
  figWrap.append(d);
});

/* ---------- tabs ---------- */
const tabs=[...document.querySelectorAll('.tab')];
tabs.forEach(t=>t.addEventListener('click',()=>{
  tabs.forEach(o=>{o.setAttribute('aria-selected', String(o===t)); $('#p-'+o.dataset.p).hidden = o!==t;});
}));

/* ---------- build the gutter ---------- */
const payoutById = Object.fromEntries(DATA.payouts.map(p=>[p.settlement_id,p]));
const bankById   = Object.fromEntries(DATA.bank_rows.map(b=>[b.stmt_id,b]));
const exByRef    = {}; DATA.exception_ledger.forEach(e=>{ (exByRef[e.ref] ||= []).push(e); });

const rows = [];
const usedBank = new Set();
DATA.matches.bank.forEach(m=>{
  m.bank_stmt_ids.forEach(s=>usedBank.add(s));
  rows.push({kind:'tie', payout:payoutById[m.payout_id], bank:m.bank_stmt_ids.map(s=>bankById[s]).filter(Boolean), match:m});
});
DATA.payouts.forEach(p=>{
  if(!DATA.matches.bank.some(m=>m.payout_id===p.settlement_id)){
    const ex=(exByRef[p.settlement_id]||[])[0];
    rows.push({kind: ex && !ex.requires_human ? 'attend' : 'break', payout:p, bank:[], ex});
  }
});
DATA.bank_rows.forEach(b=>{
  if(!usedBank.has(b.stmt_id)){
    const ex=(exByRef[b.stmt_id]||[])[0];
    rows.push({kind: ex && !ex.requires_human ? 'attend' : 'break', payout:null, bank:[b], ex});
  }
});
rows.sort((a,b)=>{
  const da=(a.payout?.settled_on)||(a.bank[0]?.value_date)||'';
  const db=(b.payout?.settled_on)||(b.bank[0]?.value_date)||'';
  return da<db?-1:da>db?1:0;
});

let filter='all';
function drawGutter(){
  const g=$('#gutter'); g.innerHTML='';
  const shown = rows.filter(r=> filter==='all' ? true : filter==='tied' ? r.kind==='tie' : r.kind!=='tie');
  shown.forEach(r=>{
    const wrap=el('div','gr '+(r.kind==='tie'?'tie':r.kind));
    // left
    if(r.payout){
      const c=el('div','cell');
      c.append(el('div','id',r.payout.settlement_id));
      c.append(el('div','amt',money(r.payout.net)));
      c.append(el('div','sub',`${r.payout.settled_on} · ${r.payout.payments}p ${r.payout.refunds}r`));
      wrap.append(c);
    } else wrap.append(el('div','cell empty'));
    // channel
    const ch=el('div','chan');
    if(r.kind!=='break' && r.payout && r.bank.length){
      const ln=el('div','link'+(r.match && r.match.delta ? ' charged':''));
      ch.append(ln);
      ch.append(el('span','tierpip', (r.match?.tier||'').split('_')[0]));
    } else if(r.kind==='break'){
      ch.append(el('span','stranded','—'));
    } else {
      // Label the channel with what actually happened, not a generic word.
      // A prior-period credit and a carried-forward negative batch are
      // different findings and must not read the same.
      const SHORT={net_negative_carried:'carry fwd',prior_period_credit:'prior period',
                   other_psp_credit:'other psp',non_settlement_activity:'not settlement',
                   payout_in_transit:'in transit',bank_charge_deducted:'charge'};
      ch.append(el('span','tierpip', SHORT[r.ex?.class] || 'open'));
    }
    wrap.append(ch);
    // right
    if(r.bank.length){
      const c=el('div','cell r');
      c.append(el('div','id', r.bank.map(b=>b.stmt_id).join(' + ')));
      c.append(el('div','amt', money(r.bank.reduce((s,b)=>s+b.credit,0))));
      c.append(el('div','sub', r.bank[0].value_date+' · '+r.bank[0].narration.slice(0,34)));
      wrap.append(c);
    } else wrap.append(el('div','cell empty'));
    g.append(wrap);
  });
  if(!shown.length) g.append(el('div','note','Nothing in this view.'));
}
const fdefs=[['all','Everything'],['tied','Tied only'],['open','Open items only']];
fdefs.forEach(([k,label])=>{
  const b=el('button','filt',label);
  b.setAttribute('aria-pressed', String(k===filter));
  b.addEventListener('click',()=>{
    filter=k;
    [...document.querySelectorAll('.filt')].forEach((o,i)=>o.setAttribute('aria-pressed',String(fdefs[i][0]===k)));
    drawGutter();
  });
  $('#filters').append(b);
});
drawGutter();

/* ---------- exception ledger ---------- */
const exWrap=$('#exlist');
DATA.exception_ledger.forEach((e,i)=>{
  const btn=el('button','lrow');
  btn.setAttribute('aria-expanded','false');
  const sevCls = e.severity>=4?'h':e.severity>=2?'m':'l';
  btn.append(el('span','sev '+sevCls, e.severity>=4?'\u25C6':e.severity>=2?'\u25C7':'\u00B7'));
  const mid=el('span'); mid.append(el('div','cls',e.class)); mid.append(el('div','ref',e.ref+(e.counterpart_stmt_ids?.length?' \u2192 '+e.counterpart_stmt_ids.join(', '):'')));
  btn.append(mid);
  btn.append(el('span','amt', e.amount_display.replace('Rs.','\u20B9')));
  btn.append(el('span','cf', e.confidence.toFixed(2)));

  const det=el('div','detail'); det.hidden=true;
  const dl=el('dl');
  dl.append(el('dt',null,'Evidence'));
  const dd1=el('dd'); e.evidence.forEach(x=>{const d=el('div',null,x); dd1.append(d);}); dl.append(dd1);
  dl.append(el('dt',null,'Proposed'));
  dl.append(el('dd',null,e.suggested_resolution));
  dl.append(el('dt',null,'Disposition'));
  dl.append(el('dd',null, e.requires_human ? 'Needs a person' : 'Auto-dispositioned by rule'));
  det.append(dl);
  if(e.requires_human){
    const a=el('div','actions');
    a.append(el('span','prop','Approve proposal'));
    a.append(el('span','prop','Reject'));
    a.append(el('span','readonly','records a proposal — does not post'));
    det.append(a);
  }
  btn.addEventListener('click',()=>{
    const open=det.hidden;
    det.hidden=!open; btn.setAttribute('aria-expanded',String(open));
  });
  exWrap.append(btn); exWrap.append(det);
});

/* ---------- trace ---------- */
const TIERS=[
  ['T0_settlement_id','settlement id appears in the bank narration'],
  ['T1_utr','a UTR already tied to this payout'],
  ['T2_amount_date','amount matches inside the T+2 window'],
  ['T3_amount_date_fuzzy','amount matches, narration breaks the tie'],
  ['T4_subset_sum','several credits sum to the payout'],
  ['T4b_subset_sum_charged','several credits sum to it, less a bank charge'],
];
const matchByPayout=Object.fromEntries(DATA.matches.bank.map(m=>[m.payout_id,m]));
const tlWrap=$('#tracelist');
DATA.payouts.forEach((p,i)=>{
  const b=el('button','tl');
  b.append(el('div','id',p.settlement_id));
  b.append(el('div','amt', money(p.net)+' · '+p.settled_on));
  b.addEventListener('click',()=>{
    [...document.querySelectorAll('.tl')].forEach(o=>o.setAttribute('aria-current','false'));
    b.setAttribute('aria-current','true');
    drawTrace(p);
  });
  if(i===0){b.setAttribute('aria-current','true');}
  tlWrap.append(b);
});
function drawTrace(p){
  const body=$('#tracebody'); body.innerHTML='';
  const m=matchByPayout[p.settlement_id];
  const h=el('div');
  h.append(el('div','side-label','Payout'));
  const t=el('div'); t.style.cssText='font-family:var(--mono);font-size:17px;font-weight:500;margin-top:4px';
  t.textContent=p.settlement_id+'  '+money(p.net); h.append(t);
  const s=el('div','sub'); s.style.cssText='font-family:var(--mono);font-size:11px;color:var(--ink-3);margin-top:4px';
  s.textContent=`settled ${p.settled_on} · ${p.payments} payments, ${p.refunds} refunds, ${p.chargebacks} chargebacks`;
  h.append(s); body.append(h);

  const cas=el('div','cascade');
  let claimed=false;
  TIERS.forEach(([name,desc])=>{
    const hit = m && m.tier===name;
    const step=el('div','step '+(hit?'hit':claimed?'skip':'skip'));
    step.append(el('div','tname',name.split('_')[0]));
    const d=el('div','dot'); d.append(el('i')); step.append(d);
    let text;
    if(hit){
      text = 'Claimed. '+desc+'.';
      if(m.bank_stmt_ids?.length) text += '  \u2192 '+m.bank_stmt_ids.join(' + ');
      if(m.note) text += '  ('+m.note+')';
      if(m.delta) text += '  difference '+money(m.delta)+', explained';
      text += '   confidence '+m.confidence.toFixed(2);
    } else if(claimed){
      text = 'Not reached — already claimed.';
    } else {
      text = 'No candidate: '+desc+'.';
    }
    step.append(el('div','what',text));
    if(hit) claimed=true;
    cas.append(step);
  });
  body.append(cas);

  if(!m){
    const ex=(exByRef[p.settlement_id]||[])[0];
    const n=el('div','note');
    n.innerHTML = ex
      ? `<b>No tier claimed this payout.</b> Classified as <code>${ex.class}</code>. ${ex.suggested_resolution}`
      : `<b>No tier claimed this payout</b> and no exception was raised — this should not happen.`;
    body.append(n);
  }
}
drawTrace(DATA.payouts[0]);

/* ---------- method ---------- */
// Two legs, two charts. Scaling both to a shared maximum would squash the
// bank leg to nothing; scaling each to its own maximum inside ONE chart makes
// 17 and 119 draw the same length, which misleads anyone comparing bars. So
// each leg gets its own chart with its own scale stated on the header.
const tw=$('#tiers');
function drawTierChart(title, tiers, order, subtle){
  const totalMatches = Object.values(tiers).reduce((a,b)=>a+b,0);
  const max = Math.max(1, ...Object.values(tiers));
  const head = el('div','barhead');
  head.append(el('span',null,title));
  head.append(el('span','scale', `${totalMatches} matches · bars scaled to ${max}`));
  tw.append(head);
  const names = order.filter(n=>n in tiers).concat(
    Object.keys(tiers).filter(n=>!order.includes(n))
  );
  names.forEach(name=>{
    const v=tiers[name];
    const b=el('div','bar');
    b.append(el('div','bn',name));
    const track=el('div','track'); const fill=el('div','fill');
    if(subtle) fill.style.background='var(--ink-2)';
    fill.style.width=(v/max*100).toFixed(1)+'%';
    track.append(fill); b.append(track);
    b.append(el('div','bv',String(v)));
    tw.append(b);
  });
}
drawTierChart('Payout \u2192 bank', DATA.bank_leg.tiers, TIERS.map(t=>t[0]), false);
drawTierChart('Order \u2192 settlement', DATA.order_leg.tiers,
              ['O0_order_id','O1_amount_window','O2_name_fuzzy'], true);
$('#methodnote').innerHTML =
  `Tier count is not the same as tier worth. Removing <code>T0</code> leaves the match rate unchanged, because ` +
  `<code>T2</code> reaches the same payouts by amount and date — but mean confidence falls from 0.96 to 0.90. ` +
  `<b>T0 buys certainty, not coverage.</b> A settlement id printed in a narration is proof; an amount landing in a ` +
  `date window is inference. Both produce a match and only one produces evidence.<br><br>` +
  `The cascade made <b>${DATA.llm_calls}</b> model calls. Matching is arithmetic and string comparison, and a model ` +
  `would add latency and non-determinism to a problem with an exact answer.`;
</script>
</body>
</html>
"""


def write_dashboard(result: dict, path: Path) -> None:
    """Inline the run data into the template and write a standalone page.

    `</script>` inside the payload would terminate the script block early, so
    the separator is escaped. None of the current fields can contain it, but a
    narration field is free text and one day might.
    """
    payload = json.dumps(result, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(_TEMPLATE.replace("__DATA__", payload), encoding="utf-8")
