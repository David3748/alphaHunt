# Alpha Hunt trade explorer

This static single-page explorer reads generated JSON at runtime; it needs no server database.

Run `npm run data:refresh` from `explorer/` after research artifacts change. `npm run build` refreshes automatically.

The adapter reads current confirmation cases, syntheses, and safety backtest. When `lab_runs/sealed_safety/results.json` appears, it uses sealed summary metrics. Sealed `cases.jsonl` and `syntheses.jsonl` replace confirmation records when both exist. A top-level sealed `trades` array takes precedence.

Preferred sealed trade fields: `case_id`, `ticker`, `company`, `signal_date`, `entry_date`, `exit_date`, `downside_probability_pct`, `stock_return`, `benchmark_return`, `excess_return`, `thesis`, `catalyst`, `invalidation`, `evidence`, and `references`. Common aliases and absent optional fields are handled safely.
