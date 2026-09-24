/* data injected at build time — edit docs/data/*.json, then `python3 build.py` */
const SPY    = DATA.spy_weekly;
const CRASH  = DATA.crash;
const FORWARD= DATA.forward;
const BOARD  = DATA.board;
const CASES  = DATA.cases;
const PRCT   = DATA.prct;
const PERF   = DATA.perf;
/* ---- embedded data ---- */

/* ---- tiny svg helpers ---- */
const NS = "http://www.w3.org/2000/svg";
function el(tag, attrs){ const e=document.createElementNS(NS,tag);
  for(const k in attrs) e.setAttribute(k,attrs[k]); return e; }
function linePath(pts, x, y){ let d=""; pts.forEach((p,i)=>{ d+=(i?"L":"M")+x(p[0]).toFixed(1)+" "+y(p[1]).toFixed(1);}); return d; }

function frame(svg, w, h, pad){
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const g = el("g",{}); svg.appendChild(g);
  return {g, iw:w-pad.l-pad.r, ih:h-pad.t-pad.b,
          ox:pad.l, oy:h-pad.b};
}

/* Figure 1: crash detector */
(function(){
  const svg=document.getElementById("chart-crash"); if(!svg||!SPY.length) return;
  const W=760,H=260,P={l:44,r:12,t:14,b:26};
  const f=frame(svg,W,H,P);
  const xs=SPY.map(d=>new Date(d[0]).getTime());
  const x=t=>f.ox+( (t-new Date(SPY[0][0]).getTime())/(xs[xs.length-1]-xs[0]) )*f.iw;
  const ymin=Math.min(...SPY.map(d=>d[1])), ymax=Math.max(...SPY.map(d=>d[1]));
  const yl=v=>f.oy-(v-ymin)/(ymax-ymin)*f.ih;
  // crash shading Mar-Aug 2020
  f.g.appendChild(el("rect",{x:x(new Date("2020-02-20").getTime()),y:f.oy-f.ih,
    width:x(new Date("2020-08-31").getTime())-x(new Date("2020-02-20").getTime()),
    height:f.ih, fill:"#FF79C6", opacity:"0.07"}));
  f.g.appendChild(el("path",{d:linePath(SPY.map(d=>[d[0],d[1]]),
    t=>x(new Date(t).getTime()), v=>yl(v)), stroke:"#3987e5","stroke-width":"1.4",fill:"none"}));
  const maxC=Math.max(...CRASH.map(d=>d[1]));
  const x0=new Date(CRASH[0][0]+"-01").getTime(), x1=new Date("2025-12-31").getTime();
  const bw=f.iw/((new Date("2025-12-01")-new Date("2009-07-01"))/(1000*3600*24*30.5));
  CRASH.forEach(d=>{
    const cx=x(new Date(d[0]+"-01").getTime());
    const bh=(d[1]/maxC)*f.ih*0.9;
    f.g.appendChild(el("rect",{x:cx-bw/2, y:f.oy-bh, width:Math.max(bw-1,1), height:bh,
      fill:"#FF79C6", opacity:"0.5"}));
  });
  [100,300,500].forEach(v=>{ if(v<=ymax){
    f.g.appendChild(el("text",{x:f.ox-6,y:yl(v)+4,fill:"#9ca3af","font-size":"10","text-anchor":"end"})).textContent="$"+v;}});
  [2010,2015,2020,2025].forEach(yr=>{
    const tx=x(new Date(Date.UTC(yr,0,1)).getTime());
    f.g.appendChild(el("line",{x1:tx,y1:f.oy,x2:tx,y2:f.oy+4,stroke:"#374151","stroke-width":"1"}));
    const t=el("text",{x:tx,y:H-8,fill:"#9ca3af","font-size":"9.5","text-anchor":"middle"});
    t.textContent=yr; f.g.appendChild(t);
  });
  const lab=el("text",{x:x(new Date("2020-05-15").getTime()),y:f.oy-f.ih+16,fill:"#FF79C6","font-size":"11"});
  lab.textContent="May 2020: 344 events"; f.g.appendChild(lab);
})();

/* Case charts: WIX & DT */
function caseChart(id, sym){
  const c=(CASES||{})[sym]; const svg=document.getElementById(id); if(!svg||!c) return;
  const W=760,H=200,P={l:44,r:12,t:12,b:22};
  const f=frame(svg,W,H,P);
  const s=c.series, xs=s.map(d=>new Date(d[0]).getTime());
  const xmin=xs[0],xmax=xs[xs.length-1];
  const x=t=>f.ox+((t-xmin)/(xmax-xmin))*f.iw;
  const vals=s.map(d=>d[1]); const ymin=Math.min(...vals),ymax=Math.max(...vals);
  const y=v=>f.oy-(v-ymin)/(ymax-ymin)*f.ih;
  const halo={"paint-order":"stroke","stroke":"#111827","stroke-width":"3","stroke-linejoin":"round"};
  const span=ymax-ymin;
  const step=[1,2,2.5,5,10,20,25,50].find(st=>span/st<=4.5)||100;
  for(let v=Math.ceil(ymin/step)*step;v<=ymax+1e-9;v+=step){
    f.g.appendChild(el("line",{x1:f.ox,y1:y(v),x2:W-P.r,y2:y(v),stroke:"#1f2937","stroke-width":"1"}));
    const t=el("text",{x:f.ox-6,y:y(v)+3.5,fill:"#9ca3af","font-size":"9.5","text-anchor":"end"});
    t.textContent="$"+v; f.g.appendChild(t);
  }
  f.g.appendChild(el("rect",{x:x(new Date(c.entry+"T21:00").getTime()), y:f.oy-f.ih,
    width:x(new Date(c.exit+"T21:00").getTime())-x(new Date(c.entry+"T21:00").getTime()),
    height:f.ih, fill:"#B39DFF", opacity:"0.10"}));
  f.g.appendChild(el("path",{d:linePath(s.map(d=>[d[0],d[1]]),t=>x(new Date(t).getTime()),v=>y(v)),
    stroke:"#e5e7eb","stroke-width":"1.4",fill:"none"}));
  const MO=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  [[c.entry,"entry"],[c.exit,"exit"]].forEach(([d,lbl])=>{
    const dt=new Date(d+"T21:00"), xx=x(dt.getTime());
    f.g.appendChild(el("line",{x1:xx,y1:f.oy,x2:xx,y2:f.oy-f.ih,stroke:"#B39DFF","stroke-width":"1","stroke-dasharray":"3 3"}));
    const t=el("text",{x:xx,y:f.oy-4,fill:"#B39DFF","font-size":"10","text-anchor":"middle",...halo});
    t.textContent=lbl+" "+MO[dt.getMonth()]+" "+dt.getDate(); f.g.appendChild(t);
  });
  const first=el("text",{x:f.ox,y:f.oy-f.ih+10,fill:"#9ca3af","font-size":"10",...halo});
  first.textContent=sym+" close ($)"; f.g.appendChild(first);
}
caseChart("chart-wix","WIX"); caseChart("chart-dt","DT");

/* PRCT chart */
(function(){
  fetch_cache=null;
})();
if(typeof PRCT!=="undefined" && document.getElementById("chart-prct")){
  const svg=document.getElementById("chart-prct");
  const W=760,H=220,P={l:44,r:70,t:12,b:22};
  const f=frame(svg,W,H,P);
  const xs=PRCT.map(d=>new Date(d[0]).getTime());
  const xmin=xs[0],xmax=xs[xs.length-1];
  const x=t=>f.ox+((t-xmin)/(xmax-xmin))*f.iw;
  const vals=PRCT.map(d=>d[1]); const ymin=Math.min(...vals),ymax=Math.max(...vals);
  const y=v=>f.oy-(v-ymin)/(ymax-ymin)*f.ih;
  const halo={"paint-order":"stroke","stroke":"#111827","stroke-width":"3","stroke-linejoin":"round"};
  const span=ymax-ymin;
  const step=[1,2,2.5,5,10,20,25,50].find(st=>span/st<=4.5)||100;
  for(let v=Math.ceil(ymin/step)*step;v<=ymax+1e-9;v+=step){
    f.g.appendChild(el("line",{x1:f.ox,y1:y(v),x2:W-P.r,y2:y(v),stroke:"#1f2937","stroke-width":"1"}));
    const t=el("text",{x:f.ox-6,y:y(v)+3.5,fill:"#9ca3af","font-size":"9.5","text-anchor":"end"});
    t.textContent="$"+v; f.g.appendChild(t);
  }
  for(let yr=new Date(xmin).getUTCFullYear()+1;yr<=new Date(xmax).getUTCFullYear();yr++){
    const tx=x(Date.UTC(yr,0,1));
    f.g.appendChild(el("line",{x1:tx,y1:f.oy,x2:tx,y2:f.oy+4,stroke:"#374151","stroke-width":"1"}));
    const t=el("text",{x:tx,y:H-8,fill:"#9ca3af","font-size":"9.5","text-anchor":"middle"});
    t.textContent=yr; f.g.appendChild(t);
  }
  f.g.appendChild(el("path",{d:linePath(PRCT.map(d=>[d[0],d[1]]),t=>x(new Date(t).getTime()),v=>y(v)),
    stroke:"#3987e5","stroke-width":"1.4",fill:"none"}));
  const ipo=el("text",{x:x(xs[0])+4,y:y(vals[0])-8,fill:"#9ca3af","font-size":"10",...halo});
  ipo.textContent="IPO $"+vals[0];f.g.appendChild(ipo);
  const pk=PRCT.reduce((a,b)=>b[1]>a[1]?b:a);
  const pkt=el("text",{x:Math.min(Math.max(x(new Date(pk[0]).getTime()),f.ox+32),W-P.r-32),y:y(pk[1])-6,
    fill:"#9ca3af","font-size":"10","text-anchor":"middle",...halo});
  pkt.textContent="peak $"+Math.round(pk[1]);f.g.appendChild(pkt);
  const last=PRCT[PRCT.length-1];
  const lt=el("text",{x:x(xmax)+6,y:y(last[1])+3.5,fill:"#34d399","font-size":"10","font-weight":"600",...halo});
  lt.textContent="now $"+last[1];f.g.appendChild(lt);
}

/* Forward trades bar chart */
(function(){
  const svg=document.getElementById("chart-forward"); if(!svg||!FORWARD.length) return;
  const rows=[...FORWARD].sort((a,b)=>a.e-b.e);
  const W=760,rowH=13,P={l:52,r:56,t:8,b:8};
  const H=P.t+P.b+rows.length*rowH;
  const f=frame(svg,W,H,{l:P.l,r:P.r,t:P.t,b:P.b});
  const mx=Math.max(...rows.map(r=>Math.abs(r.e)),10);
  const xc=v=>f.ox+f.iw/2+(v/mx)*(f.iw/2);
  f.g.appendChild(el("line",{x1:f.ox+f.iw/2,y1:0,x2:f.ox+f.iw/2,y2:H-P.b,stroke:"#374151","stroke-width":"1"}));
  rows.forEach((r,i)=>{
    const yy=P.t+i*rowH;
    const bar=el("rect",{x:r.e>=0?xc(0):xc(r.e), y:yy+2, width:Math.abs(xc(r.e)-xc(0)), height:rowH-4,
      rx:2, fill:r.e>=0?"#34d399":"#f87171", opacity:"0.85"});
    f.g.appendChild(bar);
    const tk=el("text",{x:f.ox-6,y:yy+rowH-3,fill:"#9ca3af","font-size":"9.5","text-anchor":"end"});
    tk.textContent=r.t; f.g.appendChild(tk);
    const vv=el("text",{x:(r.e>=0?xc(r.e):xc(0))+(r.e>=0?4:-4),y:yy+rowH-3,fill:"#9ca3af","font-size":"9.5",
      "text-anchor":r.e>=0?"start":"end"});
    vv.textContent=(r.e>0?"+":"")+(Math.abs(r.e)<1?r.e.toFixed(1):r.e.toFixed(0))+"%"; f.g.appendChild(vv);
  });
  const tg=rows.findIndex(r=>r.t==="TGEN");
  if(tg>=0){const note=el("text",{x:f.ox+f.iw/2-10,y:P.t+tg*rowH+rowH-2,fill:"#34d399","font-size":"9.5","text-anchor":"end"});
    note.textContent="TGEN (uxd) outlier"; f.g.appendChild(note);}
})();

/* Figure 2: pipeline flow */
(function(){
  const svg=document.getElementById("chart-pipeline"); if(!svg) return;
  const W=760,H=256;
  svg.setAttribute("viewBox",`0 0 ${W} ${H}`);
  const g=el("g",{}); svg.appendChild(g);
  const stages=[
    {n:"① Enumerate",d:"SEC DERA\n350,456 filings",c:"#3987e5"},
    {n:"② Resolve",d:"filing-native tickers\n25,343 regimes",c:"#3987e5"},
    {n:"③ Preprice",d:"370d history + gates\n→ 9,885 eligible",c:"#3987e5"},
    {n:"④ Build cases",d:"frozen evidence packs\n~163k chars each",c:"#3987e5"},
    {n:"⑤ Extract ×5",d:"lens excerpts\n49,425 calls",c:"#B39DFF"},
    {n:"⑥ Synthesize ×2",d:"independent forecasts\n19,770 calls",c:"#B39DFF"},
    {n:"⑦ Evaluate",d:"outcomes, placebos\ngates, portfolio sim",c:"#FF79C6"},
    {n:"⑧ Archive",d:"zstd + checksums\n→ cold storage",c:"#9ca3af"},
  ];
  const bw=150,bh=74,mx=16,gx=(W-2*mx-4*bw)/3;
  stages.forEach((s,i)=>{
    const row=i<4?0:1;
    const col=i%4;
    const x=mx+col*(bw+gx), y=row*(bh+58)+10;
    g.appendChild(el("rect",{x,y,width:bw,height:bh,rx:10,
      fill:"#111827",stroke:s.c,"stroke-width":"1.2",opacity:".92"}));
    const t1=el("text",{x:x+bw/2,y:y+22,fill:s.c,"font-size":"11.5","font-weight":"700","text-anchor":"middle"});
    t1.textContent=s.n; g.appendChild(t1);
    s.d.split("\n").forEach((line,j)=>{
      const t2=el("text",{x:x+bw/2,y:y+40+j*14,fill:"#9ca3af","font-size":"9.5","text-anchor":"middle"});
      t2.textContent=line; g.appendChild(t2);
    });
    // arrow to next in row
    if(col<3){
      const ax=x+bw+3;
      g.appendChild(el("line",{x1:ax,y1:y+bh/2,x2:ax+gx-8,y2:y+bh/2,stroke:"#374151","stroke-width":"1.2"}));
      g.appendChild(el("path",{d:`M${ax+gx-8} ${y+bh/2-3.5} L${ax+gx-2} ${y+bh/2} L${ax+gx-8} ${y+bh/2+3.5} Z`,fill:"#374151"}));
    }
  });
  // wrap arrow from stage4 down to stage5
  const x3=mx+3*(bw+gx)+bw/2, x5=mx+bw/2;
  g.appendChild(el("path",{d:`M${x3} ${10+bh+12} L${x3} ${10+bh+34} L${x5} ${10+bh+34} L${x5} ${10+bh+56}`,
    stroke:"#374151","stroke-width":"1.2",fill:"none"}));
  g.appendChild(el("path",{d:`M${x5-3.5} ${10+bh+52} L${x5} ${10+bh+58} L${x5+3.5} ${10+bh+52} Z`,fill:"#374151"}));
  const band=el("text",{x:W/2,y:H-10,fill:"#9ca3af","font-size":"10","text-anchor":"middle"});
  band.textContent="data engineering (no model)          →          model layer (5 extractors + 2 syntheses per event)          →          grading & preservation";
  g.appendChild(band);
})();

/* Figure 3: funnel */
(function(){
  const svg=document.getElementById("chart-funnel"); if(!svg) return;
  const rows=[
    ["enumerated filings",350456,"#e5e7eb"],
    ["no usable price history",-148890,"#374151"],
    ["not drawn down ≥40%",-92331,"#374151"],
    ["non-US / unknown exchange",-40839,"#374151"],
    ["ADV < $1M",-35411,"#374151"],
    ["insufficient pre-signal history",-9913,"#374151"],
    ["180-day cooldown dedupe",-6113,"#374151"],
    ["unresolved identifier",-5435,"#374151"],
    ["price < $1",-1639,"#374151"],
    ["ELIGIBLE EVENTS",9885,"#34d399"],
  ];
  const W=760,rowH=24,P={l:210,r:70};
  const H=P.t0=P.t=8; const totalH=rows.length*rowH+16; svg.setAttribute("viewBox",`0 0 ${W} ${totalH}`);
  const g=el("g",{}); svg.appendChild(g);
  let remaining=0; rows.forEach(r=>{ if(r[1]<0) remaining+= -r[1];});
  const iw=W-P.l-P.r;
  rows.forEach((r,i)=>{
    const y=i*rowH+8;
    const isElig=r[1]>0&&i===rows.length-1;
    const w=isElig? Math.max(60, r[1]/350456*iw) : Math.min(iw, (-r[1])/148890*260+30);
    g.appendChild(el("rect",{x:P.l,y,width:w,height:rowH-7,rx:3,
      fill:r[2],opacity:i===0||isElig?"0.85":"0.55"}));
    const lab=el("text",{x:P.l-8,y:y+rowH/2+1,fill:i===rows.length-1?"#34d399":"#9ca3af",
      "font-size":i===rows.length-1?"11":"10","text-anchor":"end","font-weight":i===rows.length-1?"700":"400"});
    lab.textContent=r[0]; g.appendChild(lab);
    const val=el("text",{x:P.l+w+6,y:y+rowH/2+1,fill:i===rows.length-1?"#34d399":"#6b7280",
      "font-size":"10","font-weight":r[1]>0?"600":"400"});
    val.textContent=Math.abs(r[1]).toLocaleString(); g.appendChild(val);
  });
})();

/* Figure 4: causality timeline */
(function(){
  const svg=document.getElementById("chart-clock"); if(!svg) return;
  const W=760,H=150; svg.setAttribute("viewBox",`0 0 ${W} ${H}`);
  const g=el("g",{}); svg.appendChild(g);
  const y=64;
  g.appendChild(el("line",{x1:30,y1:y,x2:W-30,y2:y,stroke:"#374151","stroke-width":"1.5"}));
  const marks=[
    {x:.06,l:"acceptance ts",d:"EDGAR receipt,\nto the second",c:"#e5e7eb"},
    {x:.26,l:"manifest freeze",d:"pack locked;\nlater docs rejected",c:"#B39DFF"},
    {x:.46,l:"entry",d:"next tradable\nclose",c:"#FF79C6"},
    {x:.72,l:"exit",d:"90 calendar\ndays later",c:"#3987e5"},
    {x:.93,l:"grade",d:"placebo test vs\n500 shuffles",c:"#34d399"},
  ];
  marks.forEach(m=>{
    const mx=m.x*(W-80)+40;
    g.appendChild(el("circle",{cx:mx,cy:y,r:5,fill:m.c}));
    g.appendChild(el("line",{x1:mx,y1:y-14,x2:mx,y2:y-4,stroke:m.c,"stroke-width":1}));
    const t=el("text",{x:mx,y:y-20,fill:m.c,"font-size":"10.5","text-anchor":"middle","font-weight":"600"});
    t.textContent=m.l; g.appendChild(t);
    m.d.split("\n").forEach((ln,j)=>{
      const t2=el("text",{x:mx,y:y+22+j*13,fill:"#9ca3af","font-size":"9.5","text-anchor":"middle"});
      t2.textContent=ln; g.appendChild(t2);
    });
  });
  // forbidden zone arrow (rightward peeking)
  g.appendChild(el("line",{x1:W*.46,y1:y-44,x2:W*.93,y2:y-44,stroke:"#a33a30","stroke-width":1,"stroke-dasharray":"4 4",opacity:".7"}));
  const fz=el("text",{x:W*.69,y:y-50,fill:"#a33a30","font-size":"9.5","text-anchor":"middle"});
  fz.textContent="everything here was invisible at freeze time"; g.appendChild(fz);
})();

/* Figure 5: strategy vs SPY vs bonds (log scale) */
(function(){
  const svg=document.getElementById("chart-perf"); if(!svg||typeof PERF==="undefined") return;
  const W=760,H=340,P={l:52,r:68,t:14,b:26};
  const f=frame(svg,W,H,P); const g=f.g;
  const dates=PERF.dates, t0=new Date(dates[0]).getTime(), t1=new Date(dates[dates.length-1]).getTime();
  const x=t=>f.ox+((new Date(t).getTime()-t0)/(t1-t0))*f.iw;
  const all=Object.values(PERF.series).flat();
  const vmin=Math.min(...all), vmax=Math.max(...all);
  const lv=v=>Math.log10(v);
  const y=v=>f.oy-((lv(v)-lv(vmin))/(lv(vmax)-lv(vmin)))*f.ih;
  // decade gridlines at 1x,2x,5x multiples
  [0.25,0.5,1,2,5,10,20,40].forEach(mv=>{
    if(mv<vmin||mv>vmax) return;
    g.appendChild(el("line",{x1:f.ox,y1:y(mv),x2:W-P.r,y2:y(mv),stroke:"#1f2937","stroke-width":"1"}));
    const t=el("text",{x:f.ox-6,y:y(mv)+3.5,fill:"#9ca3af","font-size":"9.5","text-anchor":"end"});
    t.textContent="$"+mv; g.appendChild(t);
  });
  ["2010","2013","2016","2019","2022","2025"].forEach(yr=>{
    const tx=x(yr+"-01-01");
    g.appendChild(el("line",{x1:tx,y1:f.oy,x2:tx,y2:f.oy-f.ih,stroke:"#1f2937","stroke-width":"1"}));
    const t=el("text",{x:tx,y:H-8,fill:"#9ca3af","font-size":"9.5","text-anchor":"middle"});t.textContent=yr;g.appendChild(t);
  });
  const defs=[["spy","#3987e5","SPY"],["ief","#34d399","IEF"],["safety","#6b7280","deployed"],["p20","#B39DFF","p(+20%)"]];
  defs.forEach(([key,color])=>{
    const vals=PERF.series[key];
    let d="";
    vals.forEach((v,i)=>{ d+=(i?"L":"M")+x(dates[i]).toFixed(1)+" "+y(v).toFixed(1); });
    g.appendChild(el("path",{d,stroke:color,"stroke-width":key==="p20"?2:1.3,
      fill:"none","stroke-dasharray":key==="safety"?"4 3":"none",opacity:key==="safety"?".85":"1"}));
  });
  // end labels (deployed ~$10.6 sits ~1px from SPY ~$10.9 on this scale, so it goes inside, above its line end)
  const halo={"paint-order":"stroke","stroke":"#111827","stroke-width":"3","stroke-linejoin":"round"};
  [["p20",v=>"$"+v.toFixed(0),"#B39DFF"],["spy",v=>"SPY $"+v.toFixed(1),"#3987e5"],
   ["ief",v=>"bonds $"+v.toFixed(2),"#34d399"]].forEach(([key,fmt,color])=>{
    const vals=PERF.series[key];
    const lastV=vals[vals.length-1];
    const t=el("text",{x:x(t1)+5,y:y(lastV)+3.5,fill:color,"font-size":"10","font-weight":"600",...halo});
    t.textContent=fmt(lastV); g.appendChild(t);
  });
  const sv=PERF.series.safety, slv=sv[sv.length-1];
  const st=el("text",{x:x(t1)-8,y:y(slv)-11,fill:"#9ca3af","font-size":"9.5","text-anchor":"end",...halo});
  st.textContent="deployed $"+slv.toFixed(1); g.appendChild(st);
})();

/* Board table */
(function(){
  const body=document.getElementById("board-body"); if(!body) return;
  BOARD.forEach((r,i)=>{
    const tr=document.createElement("tr");
    tr.innerHTML=`<td class="num">${i+1}</td><td class="mono">${r.t}</td><td>${r.d}</td>`+
      `<td class="num">${(+r.s).toFixed(1)}</td>`+
      `<td class="num">${r.ddp!=null?r.ddp+"%":""}</td>`;
    body.appendChild(tr);
  });
})();