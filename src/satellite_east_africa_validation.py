#!/usr/bin/env python3
"""Frozen satellite-enhanced September SST forecast of East African short rains.

Scientific current-archive hindcast; target is regional rainfall, not production,
insured losses or market returns. Default execution uses checksum-verified inputs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'results/satellite_validation/east_africa'
MODELS = ('baseline', 'satellite', 'climatology', 'recent_climatology', 'persistence')
BASELINES = tuple(name for name in MODELS if name != 'satellite')
FEATURES = ['year', 'prior_season_mm', 'august_ground_mm', 'september_ground_mm', 'early_october_ground_mm']
SATELLITE = ['iod_sst_difference_c', 'nino34_sst_c']
ALPHA = 5.0


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_panel(rain: pd.DataFrame, ground: pd.DataFrame, sst: pd.DataFrame) -> pd.DataFrame:
    rain, ground, sst = rain.copy(), ground.copy(), sst.copy()
    for frame in (rain, ground, sst):
        frame['date'] = pd.to_datetime(frame.date)
        if frame.date.duplicated().any() or not frame.date.dt.day.eq(1).all():
            raise ValueError('Duplicate or non-month-start sources')
    if not ground.date.dt.month.isin((8, 9, 10)).all() or not sst.date.dt.month.eq(9).all():
        raise ValueError('Only August/September/early-October ground and September SST permitted')
    expected_days = ground.date.dt.month.map({8: 31, 9: 30, 10: 9})
    if not (ground.n_days == expected_days).all():
        raise ValueError('Ground controls require complete August/September and exactly October1-9')
    rain = rain.set_index('date')
    ground = ground.set_index('date')
    sst = sst.set_index('date')
    def season(year):
        months = [rain.precip_mm.get(pd.Timestamp(year, month, 1), np.nan) for month in (11, 12)]
        return float(sum(months)) if np.isfinite(months).all() else np.nan
    rows = []
    for year in range(1982, 2026):
        row = {'year': year, 'forecast_at': pd.Timestamp(year, 10, 20, 12),
               'target_start': pd.Timestamp(year, 11, 1), 'target_end': pd.Timestamp(year, 12, 31, 23, 59),
               'assumed_label_available_at': pd.Timestamp(year, 12, 31) + pd.Timedelta(days=90),
               'prior_label_available_at': pd.Timestamp(year - 1, 12, 31) + pd.Timedelta(days=90),
               'sst_source_month': pd.Timestamp(year, 9, 1),
               'sst_assumed_available_at': pd.Timestamp(year, 9, 30) + pd.Timedelta(days=20),
               'ground_source_end': pd.Timestamp(year, 10, 9, 23, 59),
               'ground_assumed_available_at': pd.Timestamp(year, 10, 9, 23, 59) + pd.Timedelta(days=10),
               'actual_mm': season(year), 'prior_season_mm': season(year - 1),
               'august_ground_mm': ground.precip_mm.get(pd.Timestamp(year, 8, 1), np.nan),
               'september_ground_mm': ground.precip_mm.get(pd.Timestamp(year, 9, 1), np.nan),
               'early_october_ground_mm': ground.precip_mm.get(pd.Timestamp(year, 10, 1), np.nan)}
        for column in SATELLITE:
            row[column] = sst[column].get(pd.Timestamp(year, 9, 1), np.nan)
        rows.append(row)
    panel = pd.DataFrame(rows)
    panel['features_complete'] = np.isfinite(panel[FEATURES + SATELLITE]).all(axis=1)
    return panel


def ridge_predict(train: pd.DataFrame, target: pd.DataFrame, model: str,
                  include_early_october: bool = True) -> tuple[float, np.ndarray]:
    if model not in ('baseline', 'satellite'):
        raise ValueError('Unknown regression model')
    names = (FEATURES if include_early_october else FEATURES[:-1]) + (SATELLITE if model == 'satellite' else [])
    x = train[names].to_numpy(dtype=float)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.0)
    x = (x - mean) / scale
    current = (target[names].to_numpy(dtype=float) - mean) / scale
    y = train.actual_mm.to_numpy(dtype=float)
    ymean = y.mean()
    beta = np.linalg.solve(x.T @ x + ALPHA * np.eye(x.shape[1]), x.T @ (y - ymean))
    return max(0.0, float((current @ beta)[0] + ymean)), beta


def predict(panel: pd.DataFrame, include_early_october: bool = True) -> pd.DataFrame:
    rows = []
    for _, row in panel.loc[panel.year.between(1999, 2025)].iterrows():
        if not (row.sst_assumed_available_at < row.forecast_at
                and row.ground_assumed_available_at < row.forecast_at
                and row.ground_source_end < row.forecast_at
                and row.prior_label_available_at < row.forecast_at
                and row.forecast_at < row.target_start < row.target_end < row.assumed_label_available_at):
            raise ValueError('Source/issue/target/label timing violation')
        train = panel.loc[panel.features_complete & np.isfinite(panel.actual_mm)
                          & (panel.year < row.year) & (panel.assumed_label_available_at < row.forecast_at)
                          & (panel.sst_assumed_available_at < row.forecast_at)
                          & (panel.ground_assumed_available_at < row.forecast_at)].sort_values('year')
        record = row.to_dict()
        record.update({'eligible': False, 'n_train': len(train), 'abstain_reason': ''})
        if not row.features_complete or len(train) < 17:
            record['abstain_reason'] = 'Incomplete predictors or fewer than17 training years'
        else:
            record.update({'eligible': True, 'latest_training_year': int(train.year.max()),
                           'latest_training_label_available': train.assumed_label_available_at.max(),
                           'climatology': float(train.actual_mm.mean()),
                           'recent_climatology': float(train.tail(10).actual_mm.mean()),
                           'persistence': float(row.prior_season_mm)})
            for model in ('baseline', 'satellite'):
                record[model], beta = ridge_predict(train, row.to_frame().T, model, include_early_october)
                if model == 'satellite':
                    record['iod_standardized_coefficient'] = float(beta[-2])
                    record['nino34_standardized_coefficient'] = float(beta[-1])
        rows.append(record)
    return pd.DataFrame(rows)


def score(predictions: pd.DataFrame, draws: int = 10000) -> dict:
    predictions = predictions.sort_values('year')
    if predictions.year.duplicated().any():
        raise ValueError('Duplicate forecast year')
    known = np.isfinite(predictions.actual_mm)
    valid = predictions.loc[predictions.eligible & known]
    if valid.empty:
        return {'status': 'no_observed_eligible_forecasts', 'forecast_gate_passed': False,
                'n_test_years': 0, 'n_pending_outcomes': int((predictions.eligible & ~known).sum())}
    if not np.isfinite(valid[list(MODELS)]).all().all():
        raise ValueError('Eligible forecasts must be finite')
    errors = valid[list(MODELS)].to_numpy() - valid.actual_mm.to_numpy()[:, None]
    squared = errors ** 2
    metrics = {name: {'rmse_mm': float(np.sqrt(squared[:, i].mean())), 'mae_mm': float(np.abs(errors[:, i]).mean())}
               for i, name in enumerate(MODELS)}
    # Resample calendar positions, retaining missing-year gaps inside blocks.
    calendar = np.full((int(valid.year.max() - valid.year.min() + 1), len(MODELS)), np.nan)
    calendar[valid.year.to_numpy(dtype=int) - int(valid.year.min())] = squared
    rng = np.random.default_rng(20260927)
    boot = []
    while len(boot) < draws:
        starts = rng.integers(0, len(calendar), size=(len(calendar) + 4) // 5)
        indices = ((starts[:, None] + np.arange(5)) % len(calendar)).ravel()[:len(calendar)]
        sample = calendar[indices]
        if np.isfinite(sample).any():
            boot.append(np.sqrt(np.nanmean(sample, axis=0)))
    boot = np.asarray(boot)
    sat = MODELS.index('satellite')
    comparisons = {}
    for baseline in BASELINES:
        j = MODELS.index(baseline)
        comparisons[baseline] = {'rmse_reduction_fraction': 1 - metrics['satellite']['rmse_mm'] / metrics[baseline]['rmse_mm'],
                                 'mae_reduction_fraction': 1 - metrics['satellite']['mae_mm'] / metrics[baseline]['mae_mm'],
                                 'rmse_gain_ci95_mm': np.quantile(boot[:, j] - boot[:, sat], [.025, .975]).tolist(),
                                 'years_lower_squared_error': int((squared[:, sat] < squared[:, j]).sum())}
    best = min(BASELINES, key=lambda name: metrics[name]['rmse_mm'])
    gate = comparisons[best]['rmse_reduction_fraction'] >= .05 and all(
        comparison['mae_reduction_fraction'] > 0 and comparison['rmse_gain_ci95_mm'][0] > 0
        for comparison in comparisons.values())
    return {'status': 'scientific_forecast_gate_passed' if gate else 'forecast_gate_failed',
            'forecast_gate_passed': bool(gate), 'n_test_years': len(valid),
            'test_years': valid.year.astype(int).tolist(), 'n_abstentions': int((~predictions.eligible).sum()),
            'n_pending_outcomes': int((predictions.eligible & ~known).sum()),
            'metrics': metrics, 'comparisons': comparisons, 'strongest_baseline': best,
            'original_vintage_operational_verification': False, 'satellite_only_incremental_attribution': False,
            'official_forecast_superiority_tested': False, 'economic_output_forecast_verified': False,
            'trading_alpha_verified': False, 'prospective_verification': False,
            'cross_candidate_multiple_testing_adjusted': False}


def run(out: Path = DEFAULT) -> dict:
    manifest = sum((json.loads((out / name).read_text()) for name in
                    ('source_manifest.json', 'october_source_manifest.json', 'gpcc_source_manifest.json')), [])
    for source in manifest:
        if digest(out / source['path']) != source['sha256']:
            raise ValueError(f"Source checksum mismatch: {source['path']}")
    ground = pd.concat([pd.read_csv(out / 'inputs/cpc_augsep_monthly.csv'),
                        pd.read_csv(out / 'inputs/cpc_october_controls.csv')], ignore_index=True)
    panel = build_panel(pd.read_csv(out / 'inputs/gpcc_v2022_monthly.csv'), ground,
                        pd.read_csv(out / 'inputs/september_sst.csv'))
    predictions = predict(panel)
    summary = score(predictions)
    summary.update({'protocol': json.loads((out / 'protocol.json').read_text()),
                    'prefit_information_set_amendment': json.loads((out / 'prefit_information_set_amendment.json').read_text()),
                    'prefit_gpcc_source_repair': json.loads((out / 'prefit_gpcc_source_repair.json').read_text()),
                    'source_code_sha256': digest(Path(__file__)), 'source_manifest_sha256': digest(out / 'source_manifest.json'),
                    'october_source_manifest_sha256': digest(out / 'october_source_manifest.json'),
                    'gpcc_source_manifest_sha256': digest(out / 'gpcc_source_manifest.json'),
                    'protocol_sha256': digest(out / 'protocol.json'),
                    'interpretation': 'Current-archive October20 forecast of November-December gauge rainfall in a fixed Kenya-centered land rectangle. Satellite-enhanced OISST vs same local gauge-rainfall and history controls; not isolated satellite attribution, original-vintage certification, production or market alpha.'})
    panel.to_csv(out / 'panel.csv', index=False)
    predictions.to_csv(out / 'predictions.csv', index=False)
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    secondary_predictions = predict(panel, include_early_october=False)
    secondary_summary = score(secondary_predictions)
    secondary_summary['interpretation'] = 'Original August/September-only controls, retained as a secondary diagnostic. Primary gate uses the stronger early-October information set; this result cannot replace a failed primary gate.'
    secondary_predictions.to_csv(out / 'original_augsep_predictions.csv', index=False)
    (out / 'original_augsep_summary.json').write_text(json.dumps(secondary_summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT)
    result = run(parser.parse_args().out)
    print(json.dumps({key: result[key] for key in ('status', 'n_test_years', 'metrics', 'comparisons')}, indent=2))
