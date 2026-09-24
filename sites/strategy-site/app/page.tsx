"use client";

import { useEffect, useMemo, useState } from "react";

type Trade = { ticker:string; company:string; cutoff:string; entry_date:string; exit_date:string; net_trade_return:number; thesis:string; catalyst:string; invalidation:string };
type LiveSignal = { ticker:string; company:string; filed:string; score?:number; threshold?:number; p_plus20?:number; expected_excess?:number; p_positive?:number; downside?:number; decision:string; thesis:string; catalyst:string; invalidation:string; status:string; method?:string; expression?:string };
type CurvePoint = { d:string; nav:number; spy:number };
type Timeseries = { generated_at:string; start:string; end:string; series:CurvePoint[]; metrics:{cagr?:number;sharpe_over_13week_tbill?:number;max_drawdown?:number;exposure_matched_information_ratio?:number;trade_win_rate?:number;trades?:number} };
type SpanStat = { n:number; beat:number; pct:number|null };
type Spans = { generated_at:string; universe_all_eligible:SpanStat; universe_by_entry_year:Record<string,SpanStat>; p_plus20_executed:SpanStat; p_plus20_by_entry_year:Record<string,SpanStat> };
type BookTrade = { ticker:string; company:string; accepted:string; cutoff:string; score:number; threshold_at_signal?:number|null; prior_score_count?:number|null; thesis:string; catalyst:string; invalidation:string; status:"open"|"closed"|"pending_entry"; entry_date?:string; entry_price?:number; last_date?:string; last_price?:number; holding_days?:number; target_exit?:string; stock_return?:number; spy_return?:number; excess_return?:number };
type LedgerTrade = { ticker:string; company:string; cutoff:string; accepted?:string; score:number; threshold_at_signal?:number|null; prior_score_count?:number|null; entry_date:string; entry_price:number; exit_date:string; exit_price:number; stock_return:number; spy_return:number|null; excess_return:number|null; holding_days:number; thesis:string; catalyst:string; invalidation:string };

const strategies = [
 {id:"causal_blend",name:"Causal blend",label:"Lead candidate",why:"Balances three upside forecasts against downside risk, standardized only on prior observations.",formula:"¼[z(P+20) + z(expected alpha) + z(P>0) − z(downside)]",cagr:35.1,sharpe:1.34,ir:1.32,dd:-24.1,hist:0.97,fwd:1.97,trades:269,win:71.4,p:.002,color:"green"},
 {id:"p_plus20",name:"P(+20%)",label:"Highest conviction",why:"Ranks on the model’s probability of beating SPY by at least 20% over 90 days.",formula:"mean replicate P(excess return ≥ 20%)",cagr:45.6,sharpe:1.46,ir:1.39,dd:-26.9,hist:1.07,fwd:1.89,trades:270,win:73.3,p:.002,color:"blue"},
 {id:"upside_x_drawdown",name:"Upside × drawdown",label:"Aggressive",why:"Requires upside conviction and rewards deeper dislocation. Returns rise, but so does crash risk.",formula:"P(+20%) × |drawdown from 1-year high|",cagr:45.3,sharpe:1.38,ir:1.31,dd:-38.6,hist:1.18,fwd:1.22,trades:277,win:68.2,p:.002,color:"orange"},
 {id:"safety",name:"Safety-first",label:"Original · rejected",why:"Selects the lowest downside-tail forecasts. Better than random, but too defensive in recovery regimes.",formula:"− mean downside-tail probability",cagr:14.5,sharpe:.73,ir:.44,dd:-31.8,hist:.67,fwd:.14,trades:319,win:61.8,p:.006,color:"gray"},
 {id:"deep_drawdown",name:"Deep drawdown",label:"Mechanical control",why:"Buys the most distressed eligible names without LLM judgment. It failed catastrophically forward.",formula:"|drawdown from 1-year high|",cagr:-2.6,sharpe:.11,ir:-.04,dd:-92,hist:.22,fwd:-.38,trades:391,win:41.2,p:.112,color:"red"},
];

const pct=(v:number,d=1)=>`${v>=0?"+":""}${v.toFixed(d)}%`;
const mult=(v:number)=>`×${v.toFixed(1)}`;
type YearRow={y:string;r:number;s:number;partial:boolean};

function YearBars({rows,selected,onSelect}:{rows:YearRow[];selected:string|null;onSelect:(y:string|null)=>void}){
 if(!rows.length)return null;
 const up=(v:number)=>`${Math.min(100,Math.max(0,v/2))}%`;
 const down=(v:number)=>`${Math.min(100,Math.abs(v)/20)}%`;
 return <div className="yr-wrap"><p className="eyebrow">CALENDAR-YEAR TOTAL RETURN · P(+20%) VERSUS SPY · CLICK A YEAR FOR ITS TRADES</p>
  <div className="yr-chart">{rows.map(({y,r,s,partial})=><button type="button" onClick={()=>onSelect(selected===y?null:y)} className={`yr-col ${selected===y?"sel":""}`} key={y} aria-pressed={selected===y} title={`${y}: P(+20%) ${pct(r)} · SPY ${pct(s)} — click for trades`}>
   <span className={`yr-val ${r<0?"loss":""}`}>{pct(r,0)}</span>
   <div className="yr-pos"><i className="yr-p20" style={{height:up(r)}}/><i className="yr-spy" style={{height:up(s)}}/></div>
   <div className="yr-neg"><i className="yr-p20" style={{height:down(r)}}/><i className="yr-spy" style={{height:down(s)}}/></div>
   <b className="yr-name">{y.slice(2)}{partial?"*":""}</b>
  </button>)}</div>
  <div className="legend legend-light"><span><i className="p20"/>P(+20%) portfolio</span><span><i className="uni"/>SPY buy-and-hold</span></div>
  <p className="curve-note">* Partial year (2009 starts July 31; 2026 ends June 22). Flat 2009–2010 is the causal warmup: no trades until 50 prior scores exist. Bars scale to +200%; the strip below each axis covers down to −20%.</p>
 </div>;
}

type PanelTrade = { ticker:string; company:string; cutoff:string; entry_date?:string; exit_date?:string; score:number; threshold_at_signal?:number|null; prior_score_count?:number|null; stock_return?:number; spy_return?:number|null; excess_return?:number|null; holding_days?:number; thesis:string; catalyst:string; invalidation:string };

function TradeRow({t}:{t:PanelTrade}){
 const ex=typeof t.excess_return==="number"?t.excess_return:null;
 return <details key={`${t.ticker}-${t.entry_date ?? t.cutoff}`}><summary><b>{t.ticker}</b><span>{t.company}</span><time>{t.entry_date ?? t.cutoff}{t.exit_date?` → ${t.exit_date}`:""}</time>{ex!=null?<em className={ex>=0?"gain":"loss"}>{pct(ex*100)} vs SPY</em>:<em>—</em>}</summary><div className="trade-body"><p><b>Thesis</b>{t.thesis}</p><p><b>Catalyst</b>{t.catalyst}</p><p><b>Invalidation</b>{t.invalidation}</p><small>Score {typeof t.score==="number"?t.score.toFixed(3):"—"} ≥ causal threshold {typeof t.threshold_at_signal==="number"?t.threshold_at_signal.toFixed(3):"—"} · stock {typeof t.stock_return==="number"?pct(t.stock_return*100):"—"} vs SPY {typeof t.spy_return==="number"?pct(t.spy_return*100):"—"}{t.holding_days?` · held ${t.holding_days}d`:""}</small></div></details>;
}

const CURVE_W=980,CURVE_H=430,CURVE_M={l:48,r:120,t:16,b:30};

function EquityCurve({series}:{series:CurvePoint[]}){
 const geom=useMemo(()=>{
  const W=CURVE_W,H=CURVE_H,M=CURVE_M;
  if(series.length<2)return null;
  const lo=Math.log10(Math.min(...series.map(p=>Math.min(p.nav,p.spy)))*.93);
  const hi=Math.log10(Math.max(...series.map(p=>Math.max(p.nav,p.spy)))*1.07);
  const X=(i:number)=>M.l+(W-M.l-M.r)*i/(series.length-1);
  const Y=(v:number)=>M.t+(H-M.t-M.b)*(1-(Math.log10(v)-lo)/(hi-lo));
  const navPath=series.map((p,i)=>`${i?"L":"M"}${X(i).toFixed(1)},${Y(p.nav).toFixed(1)}`).join("");
  const spyPath=series.map((p,i)=>`${i?"L":"M"}${X(i).toFixed(1)},${Y(p.spy).toFixed(1)}`).join("");
  const area=`${navPath}L${X(series.length-1).toFixed(1)},${Y(series[series.length-1].spy).toFixed(1)} ${series.slice().reverse().map((p,i)=>`L${X(series.length-1-i).toFixed(1)},${Y(p.spy).toFixed(1)}`).join("")}Z`;
  const ticks=[1,2,5,10,20,50].filter(v=>Math.log10(v)>=lo&&Math.log10(v)<=hi);
  const years:string[]=[];
  series.forEach(p=>{const y=p.d.slice(0,4);if(p.d.slice(5)<="01-08"&&!years.includes(y))years.push(y)});
  const yearTicks=years.filter(y=>+y%2===0).map(y=>({y,x:X(series.findIndex(p=>p.d.startsWith(`${y}-01`)))}));
  return {navPath,spyPath,area,ticks,yearTicks,Y,X,last:series[series.length-1],first:series[0]};
 },[series]);
 if(!geom)return <div className="chart-empty">Loading equity curve…</div>;
 return <svg viewBox={`0 0 ${CURVE_W} ${CURVE_H}`} role="img" aria-label="P(+20%) portfolio growth versus SPY, log scale">
  {geom.ticks.map(v=><g key={v}><line x1={CURVE_M.l} x2={CURVE_W-CURVE_M.r} y1={geom.Y(v)} y2={geom.Y(v)} stroke="var(--line)" strokeDasharray="2 4"/><text x={CURVE_M.l-8} y={geom.Y(v)+4} textAnchor="end" className="tick">{mult(v)}</text></g>)}
  <path d={geom.area} fill="var(--lime)" opacity=".45"/>
  <path d={geom.spyPath} fill="none" stroke="#8a948c" strokeWidth="1.6" strokeDasharray="5 4"/>
  <path d={geom.navPath} fill="none" stroke="var(--green)" strokeWidth="2.4"/>
  {geom.yearTicks.map(t=><text key={t.y} x={t.x} y={CURVE_H-8} textAnchor="middle" className="tick">{t.y}</text>)}
  <text x={CURVE_W-CURVE_M.r+10} y={geom.Y(geom.last.nav)+4} className="endlab endnav">{mult(geom.last.nav)} P(+20%)</text>
  <text x={CURVE_W-CURVE_M.r+10} y={geom.Y(geom.last.spy)+4} className="endlab endspy">×{(geom.last.spy).toFixed(1)} SPY</text>
 </svg>;
}

export default function Home(){
 const [active,setActive]=useState("causal_blend"); const [trades,setTrades]=useState<Trade[]>([]); const [signals,setSignals]=useState<LiveSignal[]>([]); const [liveStatus,setLiveStatus]=useState("Loading live scan…");
 const [curve,setCurve]=useState<Timeseries|null>(null); const [spans,setSpans]=useState<Spans|null>(null); const [book,setBook]=useState<BookTrade[]>([]); const [asOf,setAsOf]=useState(""); const [ledger,setLedger]=useState<LedgerTrade[]>([]); const [selYear,setSelYear]=useState<string|null>(null);
 useEffect(()=>{
  fetch("/data/results.json").then(r=>r.json()).then(d=>setTrades((d.trades||[]).slice().reverse())).catch(()=>{});
  fetch("/data/current_recommendations.json",{cache:"no-store"}).then(r=>r.json()).then(d=>{setSignals(d.signals||[]);setLiveStatus(d.status||"No current scan")}).catch(()=>setLiveStatus("Live scan is queued"));
  fetch("/data/timeseries.json",{cache:"no-store"}).then(r=>r.json()).then(setCurve).catch(()=>{});
  fetch("/data/spans.json",{cache:"no-store"}).then(r=>r.json()).then(setSpans).catch(()=>{});
  fetch("/data/p_plus20_current.json",{cache:"no-store"}).then(r=>r.json()).then(d=>{setBook(d.trades||[]);setAsOf(d.as_of||"")}).catch(()=>{});
  fetch("/data/p_plus20_trades.json",{cache:"no-store"}).then(r=>r.json()).then(d=>setLedger(d.trades||[])).catch(()=>{});
 },[]);
  const s=strategies.find(x=>x.id===active)!; const recent=useMemo(()=>trades.slice(0,8),[trades]);
  const openBook=useMemo(()=>book.filter(t=>t.status!=="closed"),[book]);
  const closedBook=useMemo(()=>book.filter(t=>t.status==="closed"),[book]);
  const primaryBook=useMemo(()=>openBook.length?openBook:closedBook.slice(0,8),[openBook,closedBook]);
 const years=useMemo(()=>spans?[...new Set([...Object.keys(spans.universe_by_entry_year),...Object.keys(spans.p_plus20_by_entry_year)])].sort():[],[spans]);
 const yearly=useMemo<YearRow[]>(()=>{
  if(!curve||curve.series.length<2)return [];
  const last:Record<string,CurvePoint>={};
  curve.series.forEach(p=>{last[p.d.slice(0,4)]=p});
  const ys=Object.keys(last).sort();
  return ys.map((y,i)=>{const prev=i?last[ys[i-1]]:curve.series[0];const p=last[y];
   return {y,r:(p.nav/prev.nav-1)*100,s:(p.spy/prev.spy-1)*100,
     partial:y===curve.start.slice(0,4)||y===curve.end.slice(0,4)}});
 },[curve]);
 const tradesByYear=useMemo(()=>{const m:Record<string,LedgerTrade[]>={};ledger.forEach(t=>{(m[t.entry_date.slice(0,4)]??=[]).push(t)});return m},[ledger]);
 const active2026=useMemo(()=>ledger.filter(t=>t.exit_date>="2026-01-01").sort((a,b)=>b.entry_date.localeCompare(a.entry_date)),[ledger]);
 const panel=useMemo(()=>{
  if(!selYear)return null;
  const stats=(list:PanelTrade[])=>{const ex=list.map(t=>t.excess_return).filter((v):v is number=>typeof v==="number");
   return {n:list.length,beat:ex.filter(v=>v>0).length,meanEx:ex.length?ex.reduce((a,b)=>a+b,0)/ex.length:null}};
  if(selYear==="2026"){const s=stats(active2026);
   return {title:`2026 span · positions held during 2026`,note:"The sealed corpus produced no new entries after 2025-11-14; every position exited by 2026-02-12 and the rule has been flat since.",trades:active2026,stats:s};}
  const list=(tradesByYear[selYear]||[]).slice().sort((a,b)=>b.entry_date.localeCompare(a.entry_date));
  return {title:`${selYear} entry cohort · ${list.length} trades`,note:list.length?"":"No trades — the causal threshold needs 50 prior scores, so the rule was still warming up.",trades:list,stats:stats(list)};
 },[selYear,tradesByYear,active2026]);
 return <main>
  <header className="topbar"><div className="brand"><span>α</span> alphaHunt</div><nav><a href="#strategies">Strategies</a><a href="#performance">Backtest</a><a href="#curve">Vs SPY</a><a href="#trades">Trades</a><a href="#current">Research</a><a href="#book">P(+20%) book</a></nav><div className="asof">2009–2025 locked test · 2026 live extension</div></header>
  <section className="hero"><div><p className="eyebrow">POINT-IN-TIME LONG RESEARCH</p><h1>Evidence first.<br/>Price second.</h1><p className="lede">A bitemporal research system testing whether filing-grounded LLM forecasts identify mispriced long opportunities—without letting future information leak backward.</p></div><div className="verdict"><span className="verdict-label">LEADING STRATEGY</span><strong>Causal blend</strong><p>Best balance of clean holdout alpha and drawdown control. Secondary result—not yet a production trading system.</p><div className="metric-row"><div><b>1.34</b><span>Sharpe</span></div><div><b>1.32</b><span>Matched IR</span></div><div><b>−24.1%</b><span>Max DD</span></div></div></div></section>
  <section id="current" className="live"><div className="live-head"><div><p className="eyebrow"><span className="pulse"/> CURRENT RESEARCH</p><h2>{liveStatus}</h2></div><p>Fresh primary-source ideas are labeled separately from model-qualified causal-blend signals. Historical winners are never recycled as current ideas.</p></div>{signals.length?<div className="signal-grid">{signals.map(x=><article className="signal" key={`${x.ticker}-${x.filed}`}><div className="signal-top"><div><b>{x.ticker}</b><span>{x.company}</span></div><em>{x.status}</em></div>{typeof x.score==="number"&&typeof x.threshold==="number"?<div className="scoreline"><strong>{x.score.toFixed(2)}</strong><span>blend score · threshold {x.threshold.toFixed(2)}</span></div>:<div className="scoreline"><strong>{x.method||"Primary-source"}</strong><span>research lane · as of {x.filed}</span></div>}<p>{x.thesis}</p>{typeof x.p_plus20==="number"?<dl><div><dt>P(+20%)</dt><dd>{x.p_plus20}%</dd></div><div><dt>Expected alpha</dt><dd>{x.expected_excess}%</dd></div><div><dt>Downside</dt><dd>{x.downside}%</dd></div></dl>:<dl><div><dt>Expression</dt><dd>{x.expression||x.decision}</dd></div><div><dt>Decision</dt><dd>{x.decision}</dd></div></dl>}<details><summary>Evidence and risk</summary><p><b>Catalyst:</b> {x.catalyst}</p><p><b>Invalidation:</b> {x.invalidation}</p></details></article>)}</div>:<div className="empty-live"><span>2026</span><p>The model-qualified live continuation is ingesting recent SEC filings. No recommendation is shown until the complete evidence and eligibility checks pass.</p></div>}</section>
  <section id="strategies" className="section"><div className="section-head"><div><p className="eyebrow">FIVE RULES · ONE EXECUTION POLICY</p><h2>What each strategy believes</h2></div><p>Click a rule to inspect it. Every test uses the next eligible close, ten 10%-NAV slots, a 90-day hold, 25 bp per side, T-bill cash, and an exposure-matched SPY benchmark.</p></div><div className="tabs" role="tablist">{strategies.map(x=><button key={x.id} onClick={()=>setActive(x.id)} className={active===x.id?"active":""}>{x.name}</button>)}</div><div className="strategy-detail"><div><span className={`tag ${s.color}`}>{s.label}</span><h3>{s.name}</h3><p>{s.why}</p><code>{s.formula}</code></div><div className="detail-metrics"><div><b>{pct(s.cagr)}</b><span>CAGR</span></div><div><b>{s.sharpe.toFixed(2)}</b><span>Sharpe</span></div><div><b>{s.ir.toFixed(2)}</b><span>Matched IR</span></div><div><b>{pct(s.dd)}</b><span>Max DD</span></div><div><b>{s.trades}</b><span>Trades</span></div><div><b>{s.win.toFixed(1)}%</b><span>Win rate</span></div></div></div></section>
  <section id="performance" className="section dark"><div className="section-head"><div><p className="eyebrow">CLEAN HOLDOUT COMPARISON</p><h2>Did it travel through time?</h2></div><p>2019–2020 was used to generate the secondary hypotheses and is excluded here. Historical and forward bars are information ratios versus exposure-matched SPY.</p></div><div className="bar-chart">{strategies.map(x=><div className="bar-row" key={x.id}><b>{x.name}</b><div className="bars"><div className="bar hist" style={{width:`${Math.max(0,x.hist)/2*100}%`}}><span>{x.hist.toFixed(2)}</span></div><div className={`bar fwd ${x.fwd<0?"negative":""}`} style={{width:`${Math.abs(x.fwd)/2*100}%`}}><span>{x.fwd.toFixed(2)}</span></div></div></div>)}</div><div className="legend"><span><i className="hist"/>2009–2018 historical holdout</span><span><i className="fwd"/>2021–2025 forward holdout</span></div></section>
  <section id="curve" className="section"><div className="section-head"><div><p className="eyebrow">GROWTH OF ONE DOLLAR · LOG SCALE</p><h2>P(+20%) versus SPY over time</h2></div><p>The same frozen execution policy—next-close entry, ten 10%-NAV slots, 90-day holds, 25 bp per side, T-bill cash. The shaded band is cumulative excess over buying SPY on every entry date.</p></div><div className="curve-grid"><div className="curve-chart">{curve?<EquityCurve series={curve.series}/>:<div className="chart-empty">Loading equity curve…</div>}{curve&&<p className="curve-note">Simulated {curve.start.slice(0,4)}–{curve.end.slice(0,4)} · conservative bound · generated {new Date(curve.generated_at).toLocaleDateString()}</p>}</div>  <div className="curve-stats">
   <div><span className="statlab">P(+20%) multiple</span><b className="big green">{curve?mult(curve.series[curve.series.length-1].nav):"—"}</b><small>from $1 · {curve?curve.start.slice(0,4):""}–{curve?curve.end.slice(0,4):""}</small></div>
   <div><span className="statlab">SPY buy-and-hold</span><b className="big">{curve?mult(curve.series[curve.series.length-1].spy):"—"}</b><small>same window</small></div>
   <div><span className="statlab">90-day spans beating SPY</span><b className="big green">{spans?.p_plus20_executed.pct!=null?spans.p_plus20_executed.pct.toFixed(1)+"%":"—"}</b><small>{spans?`${spans.p_plus20_executed.beat} of ${spans.p_plus20_executed.n} executed P(+20%) trades`:""}</small></div>
   <div><span className="statlab">All eligible spans</span><b className="big">{spans?.universe_all_eligible.pct!=null?spans.universe_all_eligible.pct.toFixed(1)+"%":"—"}</b><small>{spans?`${spans.universe_all_eligible.beat.toLocaleString()} of ${spans.universe_all_eligible.n.toLocaleString()} scored events beat SPY over identical 90-day windows`:""}</small></div>
   {curve?.metrics&&<div className="mini-metric"><div><b>{pct(100*(curve.metrics.cagr||0))}</b><span>CAGR</span></div><div><b>{(curve.metrics.sharpe_over_13week_tbill||0).toFixed(2)}</b><span>Sharpe</span></div><div><b>{(curve.metrics.exposure_matched_information_ratio||0).toFixed(2)}</b><span>Matched IR</span></div></div>}
  </div></div>
  {spans&&<div className="beat-bars"><p className="eyebrow">SHARE OF 90-DAY SPANS BEATING SPY · BY ENTRY YEAR</p>{years.map(y=>{const u=spans.universe_by_entry_year[y];const p=spans.p_plus20_by_entry_year[y];return <div className="year-row" key={y}><b>{y}</b><div className="year-track"><div className="year-bar uni" style={{width:`${u?.pct||0}%`}}/><div className="year-bar p20" style={{width:`${p?.pct||0}%`}}/><em>{u?.pct!=null?u.pct.toFixed(0)+"%":"—"}</em><em className="p20lab">{p?.pct!=null?p.pct.toFixed(0)+"%":"—"}</em></div></div>})}<div className="legend legend-light"><span><i className="uni"/>All eligible events</span><span><i className="p20"/>P(+20%) trades</span></div></div>}
  <YearBars rows={yearly} selected={selYear} onSelect={setSelYear}/>
  {panel&&<div className="yr-panel"><div className="yr-panel-head"><b>{panel.title}</b>{panel.stats.n>0&&<span>{panel.stats.beat}/{panel.stats.n} beat SPY · mean excess {panel.stats.meanEx!=null?pct(panel.stats.meanEx*100):"—"}</span>}</div>{panel.note&&<p className="yr-panel-note">{panel.note}</p>}{panel.trades.length>0&&<div className="book-list">{panel.trades.map(t=><TradeRow key={`${t.ticker}-${t.entry_date}`} t={t}/>)}</div>}</div>}
  </section>
  <section id="book" className="section trades"><div className="section-head"><div><p className="eyebrow">P(+20%) · CURRENT BOOK{asOf?` · AS OF ${asOf}`:""}</p><h2>What the highest-conviction rule holds now</h2></div><p>{openBook.length?"Causally selected signals whose 90-day holds extend past the data snapshot, shown with live marks against SPY over the same span.":"No positions are open: every P(+20%) entry completed its 90-day hold before the sealed corpus ends. The most recent cohort is shown with realized marks versus SPY over identical windows."} Research simulation—not brokerage advice.</p></div>
  {active2026.length>0&&<div className="yr26"><p className="eyebrow">2026 IN REVIEW · POSITIONS THE RULE HELD THIS YEAR</p><div className="yr26-stats">
    <div><b>{active2026.length}</b><span>positions live in 2026</span></div>
    <div><b>{active2026.filter(t=>(t.excess_return??-1)>0).length}/{active2026.filter(t=>typeof t.excess_return==="number").length}</b><span>beat SPY over identical spans</span></div>
    <div><b>{pct(100*active2026.reduce((a,t)=>a+(t.excess_return??0),0)/active2026.filter(t=>typeof t.excess_return==="number").length)}</b><span>mean excess return</span></div>
    <div><b>{pct(100*Math.max(...active2026.map(t=>t.excess_return??-9)))}</b><span>best · {pct(100*Math.min(...active2026.map(t=>t.excess_return??9)))} worst</span></div>
   </div><p className="yr26-note">Entered {active2026[active2026.length-1]?.entry_date} → {active2026[0]?.entry_date}; all exited by {active2026.reduce((a,t)=>a>t.exit_date?a:t.exit_date,"")}. No new entries after 2025-11-14 — the sealed corpus ends with the rule flat since February 2026.</p>
   <div className="book-list">{active2026.map(t=><TradeRow key={`${t.ticker}-${t.entry_date}`} t={t}/>)}</div></div>}
  {primaryBook.length?<div className="book-list">{primaryBook.map(t=><details key={`${t.ticker}-${t.accepted}`}><summary><b>{t.ticker}</b><span>{t.company}</span><em className={`chip ${t.status}`}>{t.status.replace("_"," ")}</em>{typeof t.excess_return==="number"?<em className={(t.excess_return||0)>=0?"gain":"loss"}>{pct(t.excess_return*100)} vs SPY</em>:<em>—</em>}<time>{t.entry_date||t.cutoff}</time></summary><div className="trade-body"><p><b>Thesis</b>{t.thesis}</p><p><b>Catalyst</b>{t.catalyst}</p><p><b>Invalidation</b>{t.invalidation}</p><small>Filed {t.accepted.split(" ")[0]} · entered {t.entry_date||"next close"} at {typeof t.entry_price==="number"?t.entry_price.toFixed(2):"—"} · last {typeof t.last_price==="number"?t.last_price.toFixed(2):"—"} ({t.last_date}) · stock {typeof t.stock_return==="number"?pct(t.stock_return*100):"—"} vs SPY {typeof t.spy_return==="number"?pct(t.spy_return*100):"—"} · score {typeof t.score==="number"?t.score.toFixed(3):"—"} ≥ causal threshold {typeof t.threshold_at_signal==="number"?t.threshold_at_signal.toFixed(3):"—"} from {t.prior_score_count} prior scores · target exit {t.target_exit}{typeof t.holding_days==="number"?` · day ${t.holding_days} of 90`:""}</small></div></details>)}</div>:<div className="empty-book"><p>No P(+20%) trades in the current snapshot yet.</p></div>}
  {closedBook.length>8&&<details className="recent-closed"><summary>Older completed P(+20%) trades ({closedBook.length-8})</summary><div className="book-list">{closedBook.slice(8).map(t=><details key={`${t.ticker}-${t.accepted}`}><summary><b>{t.ticker}</b><span>{t.company}</span><em className="chip closed">closed</em><em className={(t.excess_return||0)>=0?"gain":"loss"}>{pct((t.excess_return||0)*100)} vs SPY</em><time>{t.entry_date}</time></summary><div className="trade-body"><p><b>Thesis</b>{t.thesis}</p><p><b>Catalyst</b>{t.catalyst}</p><p><b>Invalidation</b>{t.invalidation}</p><small>Held {t.entry_date} → {t.target_exit} · score {typeof t.score==="number"?t.score.toFixed(3):"—"} ≥ threshold {typeof t.threshold_at_signal==="number"?t.threshold_at_signal.toFixed(3):"—"}</small></div></details>)}</div></details>}
  </section>
  <section className="section"><div className="section-head"><div><p className="eyebrow">RETURN IS NOT THE VERDICT</p><h2>Risk, evidence, and falsification</h2></div><p>The original safety test failed despite positive returns. Passing a placebo is necessary, but drawdown, concentration, execution, and temporal robustness remain separate gates.</p></div><div className="risk-grid"><div className="risk-plot"><div className="axis y">CAGR</div><div className="axis x">Maximum drawdown →</div>{strategies.map(x=><button aria-label={`${x.name}: ${x.cagr}% CAGR and ${x.dd}% drawdown`} onClick={()=>setActive(x.id)} className={`dot ${x.color}`} style={{left:`${Math.max(2,100-Math.abs(x.dd))}%`,bottom:`${Math.max(4,(x.cagr+5)/55*86)}%`}} key={x.id}><span>{x.name}</span></button>)}</div><div className="gates"><h3>What the evidence says</h3><div><b>0.002</b><p>Minimum attainable p-value with 500 blocked placebos. All three LLM secondary rules reached it.</p></div><div><b>−92%</b><p>Drawdown for the mechanical deep-distress control. The LLM is doing more than buying the cheapest wreckage.</p></div><div><b>Secondary</b><p>The winning formulas were proposed after inspecting 2019–2020. Clean cohorts help, but do not erase model-memory or research-selection risk.</p></div></div></div></section>
  <section id="trades" className="section trades"><div className="section-head"><div><p className="eyebrow">AUDIT THE DECISIONS</p><h2>Historical trade evidence</h2></div><p>These rows are from the original safety portfolio because its executed trade ledger is fully materialized. They are examples—not current recommendations.</p></div><div className="trade-list">{recent.map(t=><details key={`${t.ticker}-${t.cutoff}`}><summary><b>{t.ticker}</b><span>{t.company}</span><time>{t.entry_date}</time><em className={(t.net_trade_return||0)>=0?"gain":"loss"}>{pct((t.net_trade_return||0)*100)}</em></summary><div className="trade-body"><p><b>Thesis</b>{t.thesis}</p><p><b>Catalyst</b>{t.catalyst}</p><p><b>Invalidation</b>{t.invalidation}</p><small>Held {t.entry_date} → {t.exit_date}</small></div></details>)}</div></section>
  <section className="method"><p className="eyebrow">HOW TO READ THIS</p><div><h2>Research, not an instruction to trade.</h2><p>Backtests include transaction costs and causal thresholds but remain simulations. Live candidates require independent verification, liquidity review, portfolio fit, and acceptance that loss—including total loss—is possible.</p></div><div><h3>Bitemporal by design</h3><p>Every document, claim, score, and outcome carries when it was true and when the system learned it. Later corrections never overwrite the historical information set.</p></div></section>
  <footer><div className="brand"><span>α</span> alphaHunt</div><p>Built from 350,456 filings · 9,885 eligible cases · 69,195 model analyses · 9,885 outcomes</p><a href="#current">Back to live signals ↑</a></footer>
 </main>;
}
