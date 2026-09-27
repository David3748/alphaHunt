"""Diagnose already-tested fixed ridge forecasts; do not fit corrected models."""
from pathlib import Path
import sys,json,hashlib
import numpy as np
import pandas as pd
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.path.insert(0,str(ROOT))
from src import satellite_south_africa_maize as m
rows=[];summaries={}
for target in ['primary_yield','secondary_production']:
 for regime in ['fao_lag','completed_harvest_stress']:
  panel=pd.read_csv(OUT/f'{target}_{regime}_panel.csv',parse_dates=['label_available','forecast_at','sst_available','ground_available','prior_label_available','target_start'])
  predictions=pd.read_csv(OUT/f'{target}_{regime}_predictions.csv')
  panel['usable']=np.isfinite(panel[m.BASE+m.SAT]).all(axis=1)&(panel.sst_available<panel.forecast_at)&(panel.ground_available<panel.forecast_at)&(panel.prior_label_available<panel.forecast_at)&(panel.forecast_at<panel.target_start)
  for _,r in predictions[predictions.eligible].iterrows():
   current=panel[panel.harvest_year.eq(r.harvest_year)].iloc[0]
   train=panel[panel.usable&np.isfinite(panel.actual_value)&(panel.harvest_year<r.harvest_year)&(panel.label_available<current.forecast_at)]
   assert len(train)==r.n_train
   for name,columns in [('baseline',m.BASE),('satellite',m.BASE+m.SAT),('trend',['harvest_year'])]:
    y=train.actual_value.to_numpy(float)
    if name=='trend':
     coefficient=float(np.polyfit(train.harvest_year-1980,y,1)[0]);penalty=0.
    else:
     x=train[columns].to_numpy(float);scale=x.std(axis=0);scale=np.where(scale>0,scale,1.);z=(x-x.mean(axis=0))/scale
     beta=np.linalg.solve(z.T@z+5*np.eye(len(columns)),z.T@(y-y.mean()))
     coefficient=float(beta[0]/scale[0]);penalty=5.
     assert np.isclose(m.ridge(train,current,columns),r[name])
    rows.append(dict(target=target,release_regime=regime,harvest_year=int(r.harvest_year),model=name,n_train=len(train),time_coefficient_per_year=coefficient,trend_penalty=penalty,prediction_error=float(r[name]-r.actual_value)))
diagnostics=pd.DataFrame(rows);diagnostics.to_csv(OUT/'frozen_bias_diagnostics.csv',index=False)
for (target,regime,model),g in diagnostics.groupby(['target','release_regime','model']):
 summaries.setdefault(target,{}).setdefault(regime,{})[model]=dict(n_years=len(g),mean_forecast_bias=float(g.prediction_error.mean()),mean_time_coefficient_per_year=float(g.time_coefficient_per_year.mean()),median_time_coefficient_per_year=float(g.time_coefficient_per_year.median()),last_time_coefficient_per_year=float(g.sort_values('harvest_year').time_coefficient_per_year.iloc[-1]),fraction_underpredicted=float((g.prediction_error<0).mean()))
result=dict(diagnostics=summaries,interpretation='Conditional time coefficients have different covariates; smaller coefficients do not alone prove a causal shrinkage explanation. Errorbias is descriptive onthealreadyusedholdout. No corrected model fit ornewverification claim.',proposed_correction='Unpenalized interceptandtime U; standardizedotherpredictors Z usingtrainingmoments; M=I−UU+; beta=(Ztranspose MZ+alphaI)^−1Ztranspose My; gamma=least_squares(U,y−Zbeta). Forecast Unew gamma+Znew beta. Freeze independent replication beforeestimating thismodel.',code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(OUT/'frozen_bias_summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
