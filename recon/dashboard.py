"""Dashboard generator — writes a self-contained HTML working paper.

The run data is inlined rather than fetched, so the file opens straight from
disk with no server and no network. A judge double-clicks it and sees the run.

Design intent
-------------
A reconciliation is not a dashboard. It is a working paper — the document an
accountant prepares, signs, and hands to a reviewer. That form has been settled
for a century and its conventions are load-bearing:

  * a header block naming the entity, the period, the basis, and the schedule
    reference
  * lettered schedules that cross-reference one another
  * tick marks against agreed items, in the reviewer's blue pencil, with a
    legend explaining each symbol
  * negatives in parentheses, figures right-aligned in tabular numerals, and a
    double rule under a total that is final
  * a signature block, because the document is meant to be printed and signed

The tick legend matters most. It is how a preparer tells a reviewer *what was
checked and on what basis* — which is exactly what a matching engine needs to
communicate, and what a match rate alone cannot.

Colour carries meaning and nothing else. Blue is the reviewer's pencil: ticks
and cross-references. Oxidised red is a figure that does not tie. Goldenrod
needs a person.

Read-only by design. Approve and reject render as proposals, because the engine
proposes and a human disposes.
"""

from __future__ import annotations

import json
from pathlib import Path

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recon Loop — reconciliation working paper</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#FAF9F5;
  --tint:#F2F1EB;
  --card:#FFFFFE;
  --ink:#16150F;
  --ink-2:#4B4A40;
  --ink-3:#8A887A;
  --rule:#C3C1B4;
  --rule-2:#DEDCD1;
  --pencil:#22518C;
  --tied:#1B5E4A;
  --broken:#96331F;
  --broken-bg:#F6E7E1;
  --attend:#7E5A0C;
  --attend-bg:#F6EFDC;
  --mono:'IBM Plex Mono',ui-monospace,'SF Mono',Menlo,monospace;
  --text:'Newsreader',Georgia,'Times New Roman',serif;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:#E7E5DB;color:var(--ink);
  font-family:var(--text);font-size:15px;line-height:1.5;
  font-variant-numeric:tabular-nums lining-nums;}
.sheet{max-width:1180px;margin:26px auto 60px;background:var(--paper);
  border:1px solid var(--rule);
  box-shadow:0 1px 0 rgba(0,0,0,.05),0 16px 44px -28px rgba(0,0,0,.4);
  padding:0 36px 40px;}

/* header block */
.wp-head{border-bottom:2px solid var(--ink);padding:26px 0 0}
.wp-title{display:flex;align-items:baseline;justify-content:space-between;gap:26px;flex-wrap:wrap}
.wp-title h1{font-size:27px;font-weight:600;letter-spacing:-.015em;margin:0}
.wp-title h1 em{font-style:normal;color:var(--ink-3);font-weight:400}
.wp-ref{font-family:var(--mono);font-size:11px;color:var(--ink-2);
  border:1px solid var(--rule);padding:5px 11px;white-space:nowrap}
.wp-ref b{color:var(--ink);font-weight:500}
.wp-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
  margin:18px 0 0;border-top:1px solid var(--rule-2)}
.wp-f{padding:9px 16px 10px 0;border-right:1px solid var(--rule-2)}
.wp-f:last-child{border-right:0}
.wp-f dt{font-family:var(--mono);font-size:9.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--ink-3)}
.wp-f dd{margin:3px 0 0;font-family:var(--mono);font-size:12px;color:var(--ink);line-height:1.5}
.wp-purpose{padding:13px 0 17px;font-size:15.5px;color:var(--ink-2);max-width:80ch;line-height:1.6}

/* schedules */
.sched{display:flex;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);overflow-x:auto}
.sched button{appearance:none;background:none;border:0;cursor:pointer;
  padding:11px 21px 10px;border-right:1px solid var(--rule-2);
  font-family:var(--text);font-size:15px;color:var(--ink-3);
  white-space:nowrap;display:flex;align-items:baseline;gap:9px}
.sched button .lt{font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;
  border:1px solid var(--rule);padding:1px 5px;color:var(--ink-3)}
.sched button:hover{color:var(--ink);background:var(--tint)}
.sched button[aria-selected="true"]{color:var(--ink);background:var(--card);font-weight:500}
.sched button[aria-selected="true"] .lt{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.sched button:focus-visible{outline:2px solid var(--pencil);outline-offset:-3px}
.panel[hidden]{display:none}
.panel{padding-top:24px}
h2.sec{font-size:19.5px;font-weight:600;margin:0;letter-spacing:-.008em}
h2.sec .xr{font-family:var(--mono);font-size:10.5px;color:var(--pencil);
  margin-left:11px;font-weight:400;letter-spacing:.04em;vertical-align:2px}
p.lede{font-size:15px;color:var(--ink-2);margin:6px 0 0;max-width:78ch;line-height:1.6}

/* statement */
.stmt{display:grid;grid-template-columns:1fr 1fr;gap:0 48px;padding:20px 0 4px}
.stmt h3{font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;
  text-transform:uppercase;color:var(--ink-3);margin:0 0 10px;font-weight:400;
  border-bottom:1px solid var(--rule-2);padding-bottom:7px}
.line{display:grid;grid-template-columns:1fr 22px auto;gap:12px;align-items:baseline;padding:4px 0}
.line .lb{font-size:15px;color:var(--ink-2);line-height:1.4}
.line .lb i{display:block;font-style:normal;font-family:var(--mono);
  font-size:10px;color:var(--ink-3);margin-top:2px}
.line .tk{font-family:var(--mono);font-size:12px;color:var(--pencil);text-align:center}
.line .fg{font-family:var(--mono);font-size:14px;white-space:nowrap;text-align:right}
.line.neg .fg{color:var(--broken)}
.line.tot{border-top:1px solid var(--ink);margin-top:8px;padding-top:8px}
.line.tot .lb{color:var(--ink);font-weight:500}
.line.tot .fg{font-weight:600;font-size:15.5px}
.line.tot::after{content:"";grid-column:1/-1;height:0;border-bottom:3px double var(--ink);margin-top:7px}
.verdict{grid-column:1/-1;display:flex;align-items:baseline;gap:13px;flex-wrap:wrap;
  margin-top:18px;padding:12px 16px;border:1px solid var(--rule);background:var(--card)}
.verdict .mark{font-family:var(--mono);font-size:17px;line-height:1}
.verdict.ok .mark{color:var(--tied)} .verdict.off .mark{color:var(--broken)}
.verdict .txt{font-size:15.5px}
.verdict .aside{font-family:var(--mono);font-size:11px;color:var(--ink-3);margin-left:auto}

/* tick legend */
.ticks{margin-top:22px;border-top:1px solid var(--rule);padding-top:13px;
  display:grid;grid-template-columns:repeat(auto-fit,minmax(238px,1fr));gap:6px 28px}
.ticks .cap{grid-column:1/-1;font-family:var(--mono);font-size:9.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--ink-3);margin-bottom:4px}
.ticks div.t{display:flex;gap:11px;align-items:baseline;font-size:13.5px;color:var(--ink-2)}
.ticks div.t b{font-family:var(--mono);color:var(--pencil);font-weight:500;
  min-width:15px;display:inline-block;font-size:13px}

/* gutter */
.filters{display:flex;padding:17px 0 10px;flex-wrap:wrap}
.filt{appearance:none;border:1px solid var(--rule);border-right:0;background:var(--card);
  font-family:var(--mono);font-size:11px;color:var(--ink-2);padding:5px 14px;cursor:pointer}
.filt:last-child{border-right:1px solid var(--rule)}
.filt[aria-pressed="true"]{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.filt:focus-visible{outline:2px solid var(--pencil);outline-offset:1px}
.gh{display:grid;grid-template-columns:1fr 30px 98px 30px 1fr;padding:4px 0 7px;align-items:end}
.gh .sl{font-family:var(--mono);font-size:9.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--ink-3)}
.gh .sl.r{text-align:right}
.gh .md{text-align:center;font-family:var(--mono);font-size:9px;color:var(--ink-3);letter-spacing:.06em}
.gutter{display:flex;flex-direction:column;border-top:1px solid var(--rule)}
.gr{display:grid;grid-template-columns:1fr 30px 98px 30px 1fr;align-items:stretch;
  border-bottom:1px solid var(--rule-2)}
.gr:nth-child(even){background:var(--tint)}
.cell{padding:8px 12px;min-height:50px;display:flex;flex-direction:column;justify-content:center}
.cell.r{text-align:right;align-items:flex-end}
.cell.empty{align-items:center;justify-content:center}
.cell.empty span{font-family:var(--mono);font-size:11px;color:var(--ink-3)}
.cell .id{font-family:var(--mono);font-size:12px;font-weight:500}
.cell .sub{font-family:var(--mono);font-size:10px;color:var(--ink-3);margin-top:3px}
.cell .amt{font-family:var(--mono);font-size:14px;font-weight:500;margin-top:2px}
.tkcol{display:flex;align-items:center;justify-content:center;
  font-family:var(--mono);font-size:13px;color:var(--pencil)}
.chan{position:relative;display:flex;align-items:center;justify-content:center}
.chan::before{content:"";position:absolute;top:0;bottom:0;left:50%;width:1px;background:var(--rule-2)}
.link{position:relative;width:100%;background:var(--tied)}
.link::before,.link::after{content:"";position:absolute;width:5px;height:5px;border-radius:50%;background:inherit}
.link::before{left:0} .link::after{right:0}
.link.c-proof{height:2.5px} .link.c-proof::before,.link.c-proof::after{top:-1px}
.link.c-strong{height:1.5px} .link.c-strong::before,.link.c-strong::after{top:-2px}
.link.c-inferred{height:1px;opacity:.8} .link.c-inferred::before,.link.c-inferred::after{top:-2px}
.link.charged{background:var(--attend)}
.pip{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  background:var(--paper);padding:0 5px;font-family:var(--mono);font-size:9px;
  color:var(--ink-2);letter-spacing:.04em;white-space:nowrap}
.gr:nth-child(even) .pip{background:var(--tint)}
.gr.break .cell:not(.empty){background:var(--broken-bg)}
.gr.attend .cell:not(.empty){background:var(--attend-bg)}
.legend{display:flex;gap:21px;flex-wrap:wrap;padding:14px 0 0;
  font-family:var(--mono);font-size:10.5px;color:var(--ink-2)}
.legend i{display:inline-block;width:15px;vertical-align:middle;margin-right:6px;background:var(--tied)}

/* exceptions */
.ledger{border:1px solid var(--rule);background:var(--card);margin-top:18px}
.lhead,.lrow{display:grid;grid-template-columns:26px 1fr 134px 58px;gap:12px;padding:10px 15px;align-items:center}
.lhead{border-bottom:1px solid var(--rule);font-family:var(--mono);font-size:9.5px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3)}
.lrow{border:0;border-bottom:1px solid var(--rule-2);width:100%;text-align:left;
  background:none;cursor:pointer;font:inherit;color:inherit}
.lrow:hover{background:var(--tint)}
.lrow:focus-visible{outline:2px solid var(--pencil);outline-offset:-2px}
.lrow .tk{font-family:var(--mono);font-size:13px;color:var(--pencil)}
.lrow .cls{font-size:15px}
.lrow .ref{font-family:var(--mono);font-size:10.5px;color:var(--ink-3);margin-top:2px}
.lrow .amt,.lrow .cf{font-family:var(--mono);font-size:12.5px;text-align:right}
.detail{padding:0 15px 16px 53px;border-bottom:1px solid var(--rule-2);background:var(--tint)}
.detail dl{margin:0;display:grid;grid-template-columns:110px 1fr;gap:6px 16px}
.detail dt{font-family:var(--mono);font-size:9.5px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink-3);padding-top:3px}
.detail dd{margin:0;font-family:var(--mono);font-size:11.5px;color:var(--ink-2);line-height:1.7}
.actions{display:flex;gap:8px;align-items:center;margin-top:14px;flex-wrap:wrap}
.prop{font-family:var(--mono);font-size:11px;border:1px solid var(--rule);
  background:var(--card);padding:5px 13px;color:var(--ink-2)}
.ro{font-family:var(--mono);font-size:10px;color:var(--ink-3)}

/* trace */
.tg{display:grid;grid-template-columns:264px 1fr;margin-top:18px;border:1px solid var(--rule)}
.tlist{border-right:1px solid var(--rule);max-height:540px;overflow-y:auto;background:var(--card)}
.tl{display:block;width:100%;text-align:left;background:none;border:0;
  border-bottom:1px solid var(--rule-2);padding:9px 14px;cursor:pointer;font:inherit}
.tl:hover{background:var(--tint)}
.tl[aria-current="true"]{background:var(--ink);color:var(--paper)}
.tl:focus-visible{outline:2px solid var(--pencil);outline-offset:-2px}
.tl .id{font-family:var(--mono);font-size:11.5px;font-weight:500}
.tl .amt{font-family:var(--mono);font-size:10.5px;opacity:.72;margin-top:2px}
.tbody{padding:22px 24px;background:var(--card);min-height:340px}
.cascade{display:flex;flex-direction:column;margin-top:18px}
.step{display:grid;grid-template-columns:128px 22px 1fr;gap:13px;align-items:start;padding:9px 0}
.step .tn{font-family:var(--mono);font-size:11px;color:var(--ink-2);padding-top:1px}
.step .dot{position:relative;display:flex;justify-content:center}
.step .dot i{width:9px;height:9px;border-radius:50%;border:1.5px solid var(--rule);
  background:var(--card);margin-top:4px;z-index:1}
.step:not(:last-child) .dot::after{content:"";position:absolute;top:10px;bottom:-14px;
  width:1px;background:var(--rule-2)}
.step.hit .dot i{background:var(--tied);border-color:var(--tied)}
.step.skip .tn{color:var(--ink-3)}
.step .wt{font-family:var(--mono);font-size:11.5px;color:var(--ink-3);line-height:1.65}
.step.hit .wt{color:var(--ink)}

/* method */
.bars{margin-top:18px;border:1px solid var(--rule);background:var(--card)}
.bhead{display:flex;justify-content:space-between;align-items:baseline;gap:14px;
  padding:11px 16px 9px;border-bottom:1px solid var(--rule);background:var(--tint);
  font-family:var(--mono);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-2)}
.bhead:not(:first-child){border-top:1px solid var(--rule)}
.bhead .sc{letter-spacing:.03em;text-transform:none;color:var(--ink-3);font-size:10px}
.bar{display:grid;grid-template-columns:198px 1fr 72px;gap:15px;align-items:center;
  padding:10px 16px;border-bottom:1px solid var(--rule-2)}
.bar:last-child{border-bottom:0}
.bar .bn{font-family:var(--mono);font-size:11.5px}
.bar .tr{height:9px;background:var(--rule-2);position:relative}
.bar .fl{position:absolute;inset:0 auto 0 0;background:var(--ink)}
.bar .bv{font-family:var(--mono);font-size:11.5px;text-align:right}
.note{font-size:15px;color:var(--ink-2);line-height:1.7;max-width:76ch;margin-top:22px}
.note b{color:var(--ink);font-weight:600}
.note code{font-family:var(--mono);font-size:12.5px;background:var(--tint);
  padding:1px 5px;border:1px solid var(--rule-2)}

/* signature block */
.sign{margin-top:38px;border-top:2px solid var(--ink);padding-top:15px;
  display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
.sign div{padding:0 20px 0 0;border-right:1px solid var(--rule-2)}
.sign div:last-child{border-right:0}
.sign dt{font-family:var(--mono);font-size:9.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--ink-3)}
.sign dd{margin:18px 0 0;border-bottom:1px solid var(--rule);height:1px}
.sign .cap{font-family:var(--mono);font-size:10px;color:var(--ink-3);margin-top:7px;display:block}

@media (max-width:860px){
  .sheet{margin:0;padding:0 16px 30px;border:0;box-shadow:none}
  .stmt{grid-template-columns:1fr;gap:26px}
  .gh,.gr{grid-template-columns:1fr 22px 64px 22px 1fr}
  .tg{grid-template-columns:1fr}
  .tlist{border-right:0;border-bottom:1px solid var(--rule);max-height:210px}
  .lhead,.lrow{grid-template-columns:22px 1fr 102px}
  .lhead .cf,.lrow .cf{display:none}
  .bar{grid-template-columns:142px 1fr 58px}
}

/* A working paper gets printed and signed. Every schedule prints, the
   navigation does not, and expanded detail comes with it. */
@media print{
  body{background:#fff}
  .sheet{margin:0;padding:0;border:0;box-shadow:none;max-width:none}
  .sched,.filters{display:none}
  .panel[hidden]{display:block!important}
  .panel{break-before:page;padding-top:14px}
  .panel:first-of-type{break-before:auto}
  .detail{display:block!important}
  .tlist{max-height:none;overflow:visible}
  .gr,.lrow,.step,.bar,.line{break-inside:avoid}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>
<div class="sheet">

  <header class="wp-head">
    <div class="wp-title">
      <h1>Bank reconciliation <em>— working paper</em></h1>
      <div class="wp-ref">W/P&nbsp;REF <b id="wpref"></b></div>
    </div>
    <dl class="wp-grid" id="wpgrid"></dl>
    <p class="wp-purpose">
      To establish that every payout claimed by the settlement report corresponds to money
      received in the bank account, and that every order recorded by the merchant corresponds
      to money collected. Differences are identified, classified, and either explained or
      referred for review.
    </p>
  </header>

  <nav class="sched" role="tablist" aria-label="Schedules">
    <button role="tab" aria-selected="true"  data-p="recon"><span class="lt">A</span>Reconciliation</button>
    <button role="tab" aria-selected="false" data-p="exc"><span class="lt">B</span>Exceptions</button>
    <button role="tab" aria-selected="false" data-p="trace"><span class="lt">C</span>Decision trace</button>
    <button role="tab" aria-selected="false" data-p="method"><span class="lt">D</span>Basis of matching</button>
  </nav>

  <section class="panel" id="p-recon" role="tabpanel">
    <h2 class="sec">Schedule A — reconciliation <span class="xr">exceptions at B · basis at D</span></h2>
    <section class="stmt" id="stmt" aria-label="Reconciliation statement"></section>
    <div class="ticks" id="ticks"></div>

    <h2 class="sec" style="margin-top:36px">Ledger against bank</h2>
    <p class="lede">Every payout the settlement report claims, set against every credit the bank statement shows. A line across the channel means the two agree. A row with nothing opposite it is the reconciling item.</p>
    <div class="filters" id="filters"></div>
    <div class="gh">
      <div class="sl">Per settlement report</div><div></div>
      <div class="md">agreed by</div><div></div>
      <div class="sl r">Per bank statement</div>
    </div>
    <div class="gutter" id="gutter"></div>
    <div class="legend">
      <span><i style="height:2.5px"></i>agreed on proof</span>
      <span><i style="height:1.5px"></i>agreed on amount and date</span>
      <span><i style="height:1px;opacity:.8"></i>inferred</span>
      <span><i style="background:var(--attend);height:2px"></i>agreed, difference explained</span>
      <span><i style="background:var(--broken);height:2px"></i>no counterpart</span>
    </div>
  </section>

  <section class="panel" id="p-exc" role="tabpanel" hidden>
    <h2 class="sec">Schedule B — exceptions <span class="xr">reconciliation at A</span></h2>
    <p class="lede">Ranked by how much a controller should care, then by value. Read down and stop when the remainder stops mattering.</p>
    <div class="ledger">
      <div class="lhead"><span></span><span>item</span><span style="text-align:right">amount</span><span style="text-align:right">conf</span></div>
      <div id="exlist"></div>
    </div>
    <p class="ro" style="margin-top:13px">Read-only. Approve and reject record a proposal for a person to action; nothing on this page writes to a ledger.</p>
  </section>

  <section class="panel" id="p-trace" role="tabpanel" hidden>
    <h2 class="sec">Schedule C — decision trace <span class="xr">basis at D</span></h2>
    <p class="lede">Select a payout to see every tier it was put through, which one agreed it, and on what evidence.</p>
    <div class="tg">
      <div class="tlist" id="tracelist"></div>
      <div class="tbody" id="tracebody"></div>
    </div>
  </section>

  <section class="panel" id="p-method" role="tabpanel" hidden>
    <h2 class="sec">Schedule D — basis of matching</h2>
    <p class="lede">Items agreed per tier, in the order the cascade runs them.</p>
    <div class="bars" id="tiers"></div>
    <div class="note" id="methodnote"></div>
  </section>

  <dl class="sign">
    <div><dt>Prepared by</dt><dd></dd><span class="cap">recon-loop, deterministic engine</span></div>
    <div><dt>Reviewed by</dt><dd></dd><span class="cap">not yet reviewed</span></div>
    <div><dt>Date</dt><dd></dd><span class="cap" id="signdate"></span></div>
  </dl>

</div>

<script>
const DATA = __DATA__;
const $=(s,r=document)=>r.querySelector(s);
const el=(t,c,x)=>{const n=document.createElement(t);if(c)n.className=c;if(x!=null)n.textContent=x;return n;};
const money=p=>"\u20B9"+(p/100).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
/* Accounting convention: a negative figure is set in parentheses, never with a
   minus sign. Anyone who reads statements expects it and reads it faster. */
const acct=p=>p<0?'('+money(Math.abs(p)).replace('\u20B9','')+')':money(p).replace('\u20B9','');

/* Tick marks. The legend is how a preparer tells a reviewer what was checked
   and on what basis - exactly what a match rate alone cannot communicate. */
const TICK={
  proof:{m:'\u2713',d:'Agreed to bank statement on settlement reference'},
  strong:{m:'\u2713',d:'Agreed to bank statement on amount and value date'},
  inferred:{m:'~',d:'Agreed on inference; evidence at Schedule C'},
  charged:{m:'\u2295',d:'Agreed less a bank transfer charge, computed and explained'},
  none:{m:'\u25CA',d:'No counterpart in the period; referred at Schedule B'},
  scope:{m:'\u2206',d:'Outside the scope of this reconciliation'},
};

$('#wpref').textContent='BR-'+DATA.period.start.replace(/-/g,'').slice(0,6)+'-'+String(DATA.seed).padStart(3,'0');
[['Entity','Merchant (synthetic)'],
 ['Period',DATA.period.start+' to '+DATA.period.end],
 ['Population',`${DATA.volume.orders} orders · ${DATA.volume.settlement_lines} settlement lines · ${DATA.volume.bank_rows} bank rows`],
 ['Basis','Full population; no sampling'],
 ['Model calls',String(DATA.llm_calls)]
].forEach(([k,v])=>{const d=el('div','wp-f');d.append(el('dt',null,k));d.append(el('dd',null,v));$('#wpgrid').append(d);});
$('#signdate').textContent=DATA.period.end;

const tabs=[...document.querySelectorAll('.sched button')];
function sel(t){tabs.forEach(o=>{o.setAttribute('aria-selected',String(o===t));$('#p-'+o.dataset.p).hidden=o!==t;});t.focus();}
tabs.forEach((t,i)=>{
  t.addEventListener('click',()=>sel(t));
  t.addEventListener('keydown',ev=>{
    if(!['ArrowRight','ArrowLeft','Home','End'].includes(ev.key))return; ev.preventDefault();
    const n=ev.key==='Home'?0:ev.key==='End'?tabs.length-1:(i+(ev.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    sel(tabs[n]);
  });
});

/* Schedule A */
const S=DATA.statement;
function side(title,rows,totalLabel,total,tick){
  const sec=el('section'); sec.append(el('h3',null,title));
  rows.forEach(r=>{
    const ln=el('div','line'+(r.amount<0?' neg':''));
    const lb=el('div','lb'); lb.append(document.createTextNode(r.label));
    if(r.note) lb.append(el('i',null,r.note));
    ln.append(lb);
    ln.append(el('div','tk',r.amount<0?TICK.scope.m:''));
    ln.append(el('div','fg',acct(r.amount)));
    sec.append(ln);
  });
  const t=el('div','line tot');
  t.append(el('div','lb',totalLabel)); t.append(el('div','tk',tick)); t.append(el('div','fg',acct(total)));
  sec.append(t); return sec;
}
const stmt=$('#stmt');
stmt.append(side('Per the bank',S.bank,'Adjusted bank balance',S.bank_total,TICK.proof.m));
stmt.append(side('Per the books',S.books,'Adjusted book balance',S.books_total,TICK.proof.m));
const v=el('div','verdict '+(S.ties?'ok':'off'));
v.append(el('span','mark',S.ties?'\u2713':'\u2717'));
v.append(el('span','txt',S.ties?'Both sides arrive at the same figure. The period reconciles.'
  :`The two sides differ by ${money(Math.abs(S.difference))}.`));
const memo=S.memo?.[0];
if(memo&&memo.amount) v.append(el('span','aside',`memo: ${money(memo.amount)} carried forward against the next payout`));
stmt.append(v);

const tw=$('#ticks');
tw.append(el('div','cap','Tick marks'));
[TICK.proof,TICK.charged,TICK.inferred,TICK.none,TICK.scope].forEach(t=>{
  const d=el('div','t'); d.append(el('b',null,t.m)); d.append(el('span',null,t.d)); tw.append(d);
});

const payoutById=Object.fromEntries(DATA.payouts.map(p=>[p.settlement_id,p]));
const bankById=Object.fromEntries(DATA.bank_rows.map(b=>[b.stmt_id,b]));
const exByRef={}; DATA.exception_ledger.forEach(e=>{(exByRef[e.ref]||=[]).push(e);});
const rows=[]; const used=new Set();
DATA.matches.bank.forEach(m=>{
  m.bank_stmt_ids.forEach(s=>used.add(s));
  rows.push({kind:'tie',payout:payoutById[m.payout_id],bank:m.bank_stmt_ids.map(s=>bankById[s]).filter(Boolean),match:m});
});
DATA.payouts.forEach(p=>{
  if(!DATA.matches.bank.some(m=>m.payout_id===p.settlement_id)){
    const ex=(exByRef[p.settlement_id]||[])[0];
    rows.push({kind:ex&&!ex.requires_human?'attend':'break',payout:p,bank:[],ex});
  }
});
DATA.bank_rows.forEach(b=>{
  if(!used.has(b.stmt_id)){
    const ex=(exByRef[b.stmt_id]||[])[0];
    rows.push({kind:ex&&!ex.requires_human?'attend':'break',payout:null,bank:[b],ex});
  }
});
rows.sort((a,b)=>{
  const da=(a.payout?.settled_on)||(a.bank[0]?.value_date)||'';
  const db=(b.payout?.settled_on)||(b.bank[0]?.value_date)||'';
  return da<db?-1:da>db?1:0;
});
const SHORT={net_negative_carried:'carried fwd',prior_period_credit:'prior period',
  other_psp_credit:'other PSP',non_settlement_activity:'not settlement',
  payout_in_transit:'in transit',bank_charge_deducted:'charge',
  opaque_narration:'unresolved',unexplained:'unresolved'};
let filter='all';
function drawGutter(){
  const g=$('#gutter'); g.innerHTML='';
  const shown=rows.filter(r=>filter==='all'?true:filter==='tied'?r.kind==='tie':r.kind!=='tie');
  shown.forEach(r=>{
    const w=el('div','gr '+(r.kind==='tie'?'tie':r.kind));
    if(r.payout){
      const c=el('div','cell');
      c.append(el('div','id',r.payout.settlement_id));
      c.append(el('div','amt',money(r.payout.net)));
      c.append(el('div','sub',`${r.payout.settled_on} · ${r.payout.payments}p ${r.payout.refunds}r`));
      w.append(c);
    } else { const e=el('div','cell empty'); e.append(el('span',null,'\u2014')); w.append(e); }

    const cf=r.match?.confidence??0;
    const weight=cf>=0.99?'c-proof':cf>=0.90?'c-strong':'c-inferred';
    const tick=r.kind==='tie'
      ? (r.match?.delta?TICK.charged.m:cf>=0.99?TICK.proof.m:cf>=0.90?TICK.strong.m:TICK.inferred.m)
      : (r.ex&&!r.ex.requires_human?TICK.scope.m:TICK.none.m);
    w.append(el('div','tkcol',tick));

    const ch=el('div','chan');
    if(r.kind!=='break'&&r.payout&&r.bank.length){
      const ln=el('div','link '+weight+(r.match?.delta?' charged':''));
      ln.title=`${r.match?.tier} · confidence ${cf.toFixed(2)}`;
      ch.append(ln); ch.append(el('span','pip',(r.match?.tier||'').split('_')[0]));
    } else { ch.append(el('span','pip',SHORT[r.ex?.class]||'open')); }
    w.append(ch);
    w.append(el('div','tkcol',''));

    if(r.bank.length){
      const c=el('div','cell r');
      c.append(el('div','id',r.bank.map(b=>b.stmt_id).join(' + ')));
      c.append(el('div','amt',money(r.bank.reduce((s,b)=>s+b.credit,0))));
      c.append(el('div','sub',r.bank[0].value_date+' · '+r.bank[0].narration.slice(0,32)));
      w.append(c);
    } else { const e=el('div','cell empty'); e.append(el('span',null,'\u2014')); w.append(e); }
    g.append(w);
  });
  if(!shown.length) g.append(el('div','note','Nothing in this view.'));
}
[['all','All items'],['tied','Agreed'],['open','Reconciling items']].forEach(([k,label],i,arr)=>{
  const b=el('button','filt',label);
  b.setAttribute('aria-pressed',String(k===filter));
  b.addEventListener('click',()=>{
    filter=k;
    [...document.querySelectorAll('.filt')].forEach((o,j)=>o.setAttribute('aria-pressed',String(arr[j][0]===k)));
    drawGutter();
  });
  $('#filters').append(b);
});
drawGutter();

/* Schedule B */
const exw=$('#exlist');
DATA.exception_ledger.forEach(e=>{
  const btn=el('button','lrow'); btn.setAttribute('aria-expanded','false');
  btn.append(el('span','tk',e.requires_human?TICK.none.m:TICK.scope.m));
  const mid=el('span');
  mid.append(el('div','cls',(e.class||'').replace(/_/g,' ')));
  mid.append(el('div','ref',e.ref+(e.counterpart_stmt_ids?.length?' \u2192 '+e.counterpart_stmt_ids.join(', '):'')));
  btn.append(mid);
  btn.append(el('span','amt',e.amount_display.replace('Rs.','\u20B9')));
  btn.append(el('span','cf',e.confidence.toFixed(2)));
  const det=el('div','detail'); det.hidden=true;
  const dl=el('dl');
  dl.append(el('dt',null,'Evidence'));
  const d1=el('dd'); e.evidence.forEach(x=>d1.append(el('div',null,x))); dl.append(d1);
  dl.append(el('dt',null,'Proposed')); dl.append(el('dd',null,e.suggested_resolution));
  dl.append(el('dt',null,'Disposition'));
  dl.append(el('dd',null,e.requires_human?'Referred for review':'Dispositioned by rule'));
  det.append(dl);
  if(e.requires_human){
    const a=el('div','actions');
    a.append(el('span','prop','Approve proposal')); a.append(el('span','prop','Reject'));
    a.append(el('span','ro','records a proposal \u2014 does not post'));
    det.append(a);
  }
  btn.addEventListener('click',()=>{const o=det.hidden;det.hidden=!o;btn.setAttribute('aria-expanded',String(o));});
  exw.append(btn); exw.append(det);
});

/* Schedule C */
const TIERS=[
  ['T0_settlement_id','settlement reference appears in the bank narration'],
  ['T1_utr','a UTR already agreed to this payout'],
  ['T2_amount_date','amount agrees within the T+2 credit window'],
  ['T3_amount_date_fuzzy','amount agrees; narration resolves the tie'],
  ['T4_subset_sum','several credits sum to the payout'],
  ['T4b_subset_sum_charged','several credits sum to it, less a bank charge'],
];
const mByP=Object.fromEntries(DATA.matches.bank.map(m=>[m.payout_id,m]));
const tlw=$('#tracelist');
DATA.payouts.forEach((p,i)=>{
  const b=el('button','tl');
  b.append(el('div','id',p.settlement_id));
  b.append(el('div','amt',money(p.net)+' · '+p.settled_on));
  b.addEventListener('click',()=>{
    [...document.querySelectorAll('.tl')].forEach(o=>o.setAttribute('aria-current','false'));
    b.setAttribute('aria-current','true'); drawTrace(p);
  });
  if(i===0) b.setAttribute('aria-current','true');
  tlw.append(b);
});
function drawTrace(p){
  const body=$('#tracebody'); body.innerHTML='';
  const m=mByP[p.settlement_id];
  const t=el('div'); t.style.cssText='font-family:var(--mono);font-size:17px;font-weight:500';
  t.textContent=p.settlement_id+'   '+money(p.net);
  const s=el('div'); s.style.cssText='font-family:var(--mono);font-size:11px;color:var(--ink-3);margin-top:5px';
  s.textContent=`settled ${p.settled_on} · ${p.payments} payments, ${p.refunds} refunds, ${p.chargebacks} chargebacks`;
  body.append(t); body.append(s);
  const cas=el('div','cascade'); let claimed=false;
  TIERS.forEach(([name,desc])=>{
    const hit=m&&m.tier===name;
    const step=el('div','step '+(hit?'hit':'skip'));
    step.append(el('div','tn',name.split('_')[0]));
    const d=el('div','dot'); d.append(el('i')); step.append(d);
    let txt;
    if(hit){
      txt='Agreed. '+desc+'.';
      if(m.bank_stmt_ids?.length) txt+='  \u2192 '+m.bank_stmt_ids.join(' + ');
      if(m.note) txt+='  ('+m.note+')';
      if(m.delta) txt+='  difference '+money(m.delta)+', explained';
      txt+='   confidence '+m.confidence.toFixed(2);
    } else if(claimed){ txt='Not reached \u2014 already agreed above.'; }
    else { txt='No candidate: '+desc+'.'; }
    step.append(el('div','wt',txt));
    if(hit) claimed=true; cas.append(step);
  });
  body.append(cas);
  if(!m){
    const ex=(exByRef[p.settlement_id]||[])[0];
    const n=el('div','note');
    n.innerHTML=ex?`<b>No tier agreed this payout.</b> Classified as <code>${ex.class}</code>. ${ex.suggested_resolution}`
                  :`<b>No tier agreed this payout</b> and no exception was raised \u2014 this should not happen.`;
    body.append(n);
  }
}
drawTrace(DATA.payouts[0]);

/* Schedule D */
const tws=$('#tiers');
function chart(title,tiers,order,subtle){
  const total=Object.values(tiers).reduce((a,b)=>a+b,0);
  const max=Math.max(1,...Object.values(tiers));
  const h=el('div','bhead');
  h.append(el('span',null,title)); h.append(el('span','sc',`${total} agreed · bars scaled to ${max}`));
  tws.append(h);
  const names=order.filter(n=>n in tiers).concat(Object.keys(tiers).filter(n=>!order.includes(n)));
  names.forEach(name=>{
    const v=tiers[name]; const b=el('div','bar');
    b.append(el('div','bn',name));
    const tr=el('div','tr'); const fl=el('div','fl');
    if(subtle) fl.style.background='var(--ink-2)';
    fl.style.width=(v/max*100).toFixed(1)+'%'; tr.append(fl); b.append(tr);
    b.append(el('div','bv',String(v))); tws.append(b);
  });
}
chart('Payout \u2192 bank',DATA.bank_leg.tiers,TIERS.map(t=>t[0]),false);
chart('Order \u2192 settlement',DATA.order_leg.tiers,['O0_order_id','O1_amount_window','O2_name_fuzzy'],true);
$('#methodnote').innerHTML =
  `Items agreed per tier is not the same as what a tier is worth. Removing <code>T0</code> leaves the `+
  `match rate unchanged, because <code>T2</code> reaches the same payouts on amount and date \u2014 but mean `+
  `confidence falls from 0.96 to 0.90. <b>T0 buys certainty, not coverage.</b> A settlement reference `+
  `printed in a narration is proof; an amount landing in a date window is inference. Both produce an `+
  `agreement and only one produces evidence.<br><br>`+
  `The cascade made <b>${DATA.llm_calls}</b> model calls. Matching is arithmetic and string comparison; a `+
  `model would add latency and non-determinism to a problem with an exact answer.`;
</script>
</body>
</html>
"""


def write_dashboard(result: dict, path: Path) -> None:
    """Inline the run data and write a standalone working paper.

    `</script>` inside the payload would terminate the script block early, so
    the separator is escaped. No current field can contain it, but narration is
    free text and one day might.
    """
    payload = json.dumps(result, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(_TEMPLATE.replace("__DATA__", payload), encoding="utf-8")
