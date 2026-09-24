#!/usr/bin/env python3
"""Annual construction-flow and construction-stage prototype for REIT research.

The prototype deliberately separates three layers:

* ``search_stac`` records point-in-time acquisition metadata from public STAC
  catalogs (Microsoft Planetary Computer by default).  It does not silently
  treat a retrospective land-cover product as a historical vintage.
* ``features_from_bands`` converts a small, already co-registered chip into
  annual spectral features.  Raster downloads are intentionally optional so a
  run can be metadata-only.
* ``label_construction_stages`` turns annual feature rows into conservative
  clearing/foundations/shell/completion labels and ``annual_flow`` aggregates
  them into an investable city-year signal.

The stage rules are a transparent baseline, not a trained classifier.  They
are designed to fail closed (``monitoring``) when a feature is missing or the
pixel coverage is too low.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
import requests


DEFAULT_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
DEFAULT_COLLECTIONS = ["landsat-c2-l2"]
UTC = dt.timezone.utc


def utc_now() -> str:
    """Return a Z-suffixed timestamp suitable for an acquisition manifest."""

    return dt.datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _timestamp(properties: Mapping[str, Any], fields: Iterable[str]) -> tuple[str | None, str | None]:
    for field in fields:
        value = properties.get(field)
        if value:
            return str(value), field
    return None, None


def _feature_record(feature: Mapping[str, Any], aoi_name: str, retrieved_at: str) -> dict[str, Any]:
    properties = dict(feature.get("properties") or {})
    acquired_at, acquisition_field = _timestamp(
        properties, ("datetime", "start_datetime", "end_datetime", "landsat:scene_date")
    )
    published_at, publication_field = _timestamp(
        properties, ("published", "created", "updated", "processing:datetime")
    )
    assets = feature.get("assets") or {}
    # Keep asset metadata, but not signed download URLs.  The URLs can expire
    # and are not needed to reproduce a metadata audit.
    asset_summary = {
        key: {k: asset.get(k) for k in ("title", "type", "roles", "eo:bands") if k in asset}
        for key, asset in assets.items()
    }
    return {
        "item_id": feature.get("id"),
        "collection": (feature.get("collection") or (feature.get("collections") or [None])[0]),
        "aoi": aoi_name,
        "bbox": feature.get("bbox"),
        "acquired_at": acquired_at,
        "acquisition_time_field": acquisition_field,
        "published_at": published_at,
        "publication_time_field": publication_field,
        "cloud_cover_pct": properties.get("eo:cloud_cover"),
        "platform": properties.get("platform"),
        "instruments": properties.get("instruments"),
        "processing_level": properties.get("landsat:processing_level")
        or properties.get("processing:level"),
        "retrieved_at": retrieved_at,
        "assets": asset_summary,
        "properties": properties,
    }


def search_stac(
    aoi_name: str,
    bbox: list[float],
    start: str,
    end: str,
    *,
    stac_url: str = DEFAULT_STAC_URL,
    collections: list[str] | None = None,
    max_cloud_pct: float | None = 40.0,
    limit: int = 20,
    max_pages: int = 5,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search a STAC API and return auditable item metadata.

    ``published_at`` is preserved when the provider supplies it; when absent,
    the record explicitly retains ``None`` rather than substituting retrieval
    time.  This distinction is necessary for point-in-time validation.
    """

    payload: dict[str, Any] = {
        "collections": collections or DEFAULT_COLLECTIONS,
        "bbox": bbox,
        "datetime": f"{start}/{end}",
        "limit": int(limit),
    }
    if max_cloud_pct is not None:
        payload["query"] = {"eo:cloud_cover": {"lt": float(max_cloud_pct)}}
    client = session or requests.Session()
    retrieved_at = utc_now()
    records: list[dict[str, Any]] = []
    page_manifests: list[dict[str, Any]] = []
    next_payload: dict[str, Any] | None = payload
    for _ in range(max(1, int(max_pages))):
        if next_payload is None:
            break
        response = client.post(stac_url, json=next_payload, timeout=60)
        response.raise_for_status()
        body = response.json()
        records.extend(_feature_record(feature, aoi_name, retrieved_at) for feature in body.get("features", []))
        next_link = next((link for link in body.get("links", []) if link.get("rel") == "next"), None)
        page_manifests.append({"http_status": response.status_code, "returned_items": len(body.get("features", [])), "request": next_payload})
        next_payload = (next_link or {}).get("body") if next_link else None
    manifest = {
        "endpoint": stac_url,
        "request": payload,
        "retrieved_at": retrieved_at,
        "pages": page_manifests,
        "returned_items": len(records),
        "next_link": next((page.get("request", {}).get("token") for page in page_manifests[1:]), None),
    }
    return records, manifest


def select_annual_items(records: pd.DataFrame) -> pd.DataFrame:
    """Select one lowest-cloud item per AOI/year without changing timestamps."""

    if records.empty:
        return records.copy()
    result = records.copy()
    result["acquired_at"] = pd.to_datetime(result["acquired_at"], utc=True, errors="coerce")
    result["year"] = result["acquired_at"].dt.year
    result["cloud_cover_pct"] = pd.to_numeric(result["cloud_cover_pct"], errors="coerce").fillna(100.0)
    return (
        result.sort_values(["aoi", "year", "cloud_cover_pct", "acquired_at"])
        .drop_duplicates(["aoi", "year"], keep="first")
        .sort_values(["aoi", "year"])
        .reset_index(drop=True)
    )


def _ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        result = numerator / denominator
    return np.where(np.isfinite(result), result, np.nan)


def features_from_bands(
    red: np.ndarray,
    nir: np.ndarray,
    swir1: np.ndarray,
    *,
    swir2: np.ndarray | None = None,
    valid_mask: np.ndarray | None = None,
    aoi_area_km2: float = 1.0,
    year: int | None = None,
    aoi: str | None = None,
) -> dict[str, Any]:
    """Derive robust annual spectral fractions from a small chip.

    Inputs are surface-reflectance arrays on a common grid.  Fractions are
    proportions of valid pixels, making them comparable across AOIs.  Thresholds
    are deliberately explicit and should be calibrated against labelled chips.
    """

    arrays = [np.asarray(x, dtype=float) for x in (red, nir, swir1)]
    if swir2 is not None:
        arrays.append(np.asarray(swir2, dtype=float))
    shape = arrays[0].shape
    if any(x.shape != shape for x in arrays):
        raise ValueError("all spectral bands must have the same shape")
    mask = np.ones(shape, dtype=bool) if valid_mask is None else np.asarray(valid_mask, dtype=bool)
    if mask.shape != shape:
        raise ValueError("valid_mask must have the same shape as spectral bands")
    mask &= np.all([np.isfinite(x) for x in arrays], axis=0)
    count = int(mask.sum())
    if count == 0:
        raise ValueError("no valid pixels remain after masking")
    red_v, nir_v, swir_v = (x[mask] for x in arrays[:3])
    ndvi = _ratio(nir_v - red_v, nir_v + red_v)
    ndbi = _ratio(swir_v - nir_v, swir_v + nir_v)
    # Bare soil is intentionally a proxy: a spectral classifier cannot prove
    # a foundation without labels or higher-resolution imagery.
    bare = (ndvi < 0.20) & (ndbi > 0.05)
    impervious = (ndvi < 0.35) & (ndbi > 0.0)
    vegetation = ndvi > 0.45
    roof = (ndbi > 0.12) & (ndvi < 0.20)
    return {
        "aoi": aoi,
        "year": year,
        "valid_pixel_fraction": float(count / mask.size),
        "ndvi_median": float(np.nanmedian(ndvi)),
        "ndbi_median": float(np.nanmedian(ndbi)),
        "vegetation_fraction": float(vegetation.mean()),
        "bare_soil_fraction": float(bare.mean()),
        "impervious_fraction": float(impervious.mean()),
        "roof_fraction": float(roof.mean()),
        "built_up_fraction": float(impervious.mean()),
        "built_up_area_km2": float(impervious.mean() * aoi_area_km2),
        "feature_method": "Landsat/Sentinel surface-reflectance indices; thresholds v1",
    }


REQUIRED_FEATURES = (
    "vegetation_fraction",
    "bare_soil_fraction",
    "impervious_fraction",
    "roof_fraction",
    "built_up_fraction",
)


def label_construction_stages(features: pd.DataFrame, *, min_valid_fraction: float = 0.70) -> pd.DataFrame:
    """Label annual observations as clearing, foundations, shell or completion.

    Labels use only the current and previous observation for an AOI, so a
    future endpoint cannot select a historical cohort.  ``monitoring`` means
    no conservative stage rule fired; it is not a claim that construction did
    not occur.
    """

    missing = [column for column in REQUIRED_FEATURES if column not in features.columns]
    if missing:
        raise ValueError(f"missing stage features: {missing}")
    frame = features.copy()
    if "date" in frame:
        date_values = frame["date"]
    elif "year" in frame:
        date_values = frame["year"].astype(str) + "-12-31"
    else:
        raise ValueError("each feature row needs a valid date or year")
    frame["date"] = pd.to_datetime(date_values, utc=True, errors="coerce")
    if frame["date"].isna().any():
        raise ValueError("each feature row needs a valid date or year")
    frame = frame.sort_values(["aoi", "date"]).reset_index(drop=True)
    labels: list[dict[str, Any]] = []
    for aoi, group in frame.groupby("aoi", sort=False, dropna=False):
        previous: Mapping[str, float] | None = None
        for _, row in group.iterrows():
            valid = float(row.get("valid_pixel_fraction", 1.0))
            current = {column: float(row[column]) for column in REQUIRED_FEATURES}
            if previous is None or valid < min_valid_fraction:
                stage, confidence, evidence = "monitoring", 0.0, "baseline_or_insufficient_coverage"
            else:
                delta = {key: current[key] - previous[key] for key in REQUIRED_FEATURES}
                if delta["vegetation_fraction"] <= -0.20 and delta["bare_soil_fraction"] >= 0.15 and delta["built_up_fraction"] < 0.05:
                    stage, confidence, evidence = "clearing", 0.80, "vegetation_drop_and_bare_soil_rise"
                elif current["bare_soil_fraction"] >= 0.25 and delta["built_up_fraction"] >= 0.03 and current["roof_fraction"] < 0.20:
                    stage, confidence, evidence = "foundations", 0.70, "bare_soil_with_early_impervious_gain"
                elif current["roof_fraction"] >= 0.55 or (current["roof_fraction"] >= 0.45 and current["impervious_fraction"] >= 0.55):
                    stage, confidence, evidence = "completion", 0.75, "roof_or_built_fraction_mature"
                elif delta["built_up_fraction"] >= 0.05 and (current["impervious_fraction"] >= 0.30 or current["roof_fraction"] >= 0.20):
                    stage, confidence, evidence = "shell", 0.65, "impervious_gain_before_mature_roof"
                else:
                    stage, confidence, evidence = "monitoring", 0.0, "no_conservative_rule_fired"
            labels.append({
                "aoi": aoi,
                "date": row["date"].isoformat(),
                "year": int(row["date"].year),
                "stage": stage,
                "stage_confidence": confidence,
                "stage_evidence": evidence,
                "valid_pixel_fraction": valid,
                **current,
            })
            if "built_up_area_km2" in row.index:
                labels[-1]["built_up_area_km2"] = float(row["built_up_area_km2"])
            previous = current if valid >= min_valid_fraction else previous
    return pd.DataFrame(labels)


def annual_flow(stage_labels: pd.DataFrame) -> pd.DataFrame:
    """Aggregate stage labels to an annual city/AOI construction-flow table."""

    if stage_labels.empty:
        return pd.DataFrame(columns=["aoi", "year", "construction_flow_km2", "stage_count", "stage_mix"])
    frame = stage_labels.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame["year"] = frame["date"].dt.year
    if "built_up_area_km2" in frame:
        frame["built_up_area_km2"] = pd.to_numeric(frame["built_up_area_km2"], errors="coerce")
        frame["built_up_area_km2"] = frame["built_up_area_km2"].fillna(frame["built_up_fraction"])
    else:
        frame["built_up_area_km2"] = frame["built_up_fraction"]
    frame = frame.sort_values(["aoi", "date"])
    frame["positive_area_delta_km2"] = frame.groupby("aoi")["built_up_area_km2"].diff().clip(lower=0).fillna(0.0)
    construction = frame[frame["stage"].isin(["clearing", "foundations", "shell", "completion"])].copy()
    grouped = construction.groupby(["aoi", "year"], observed=True)
    result = grouped.agg(
        construction_flow_km2=("positive_area_delta_km2", "sum"),
        stage_count=("stage", "size"),
        mean_stage_confidence=("stage_confidence", "mean"),
        latest_built_up_fraction=("built_up_fraction", "last"),
    ).reset_index()
    mix = construction.groupby(["aoi", "year"])["stage"].agg(lambda x: ",".join(sorted(set(x))))
    result["stage_mix"] = [mix.loc[(row.aoi, row.year)] for row in result.itertuples()]
    result["construction_flow_intensity"] = result["construction_flow_km2"] / result["latest_built_up_fraction"].replace(0, np.nan)
    return result.sort_values(["aoi", "year"]).reset_index(drop=True)


def _pilot(config: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_records: list[dict[str, Any]] = []
    requests_manifest: list[dict[str, Any]] = []
    for aoi in config["aois"]:
        for year in config["years"]:
            start = f"{year}-01-01"
            end = f"{year}-12-31"
            records, request_manifest = search_stac(
                aoi["name"], aoi["bbox"], start, end,
                stac_url=config.get("stac_search_url", DEFAULT_STAC_URL),
                collections=config.get("collections", DEFAULT_COLLECTIONS),
                max_cloud_pct=config.get("max_cloud_pct", 40),
                limit=config.get("stac_limit", 10),
                max_pages=config.get("stac_max_pages", 5),
            )
            all_records.extend(records)
            request_manifest["aoi"] = aoi["name"]
            request_manifest["year"] = year
            requests_manifest.append(request_manifest)
    raw = pd.DataFrame(all_records)
    selected = select_annual_items(raw)
    if not raw.empty:
        raw.to_json(output_dir / "stac_pilot_items.json", orient="records", indent=2, date_format="iso")
        selected.to_json(output_dir / "annual_observations.json", orient="records", indent=2, date_format="iso")
    fixture = output_dir / "stage_fixture_features.csv"
    if fixture.exists():
        labels = label_construction_stages(pd.read_csv(fixture), min_valid_fraction=float(config.get("stage_rules", {}).get("minimum_valid_pixel_fraction", 0.70)))
        labels.to_csv(output_dir / "stage_labels.csv", index=False)
        annual_flow(labels).to_csv(output_dir / "annual_flow.csv", index=False)
    manifest = {
        "prototype": "reit_annual_construction_v1",
        "generated_at": utc_now(),
        "requests": requests_manifest,
        "all_items": len(raw),
        "selected_annual_items": len(selected),
        "selected_item_ids": selected["item_id"].tolist() if not selected.empty else [],
        "source_notes": "Metadata-only pilot; no raster bytes downloaded. published_at remains null when provider omits it.",
        "point_in_time_checks": {
            "selected_missing_publication_timestamp": int(selected["published_at"].isna().sum()) if "published_at" in selected else 0,
            "selected_acquisition_after_publication": int(
                ((pd.to_datetime(selected["acquired_at"], utc=True, errors="coerce") > pd.to_datetime(selected["published_at"], utc=True, errors="coerce"))).sum()
            ) if "published_at" in selected else 0,
            "status": "blocked_for_vintage_claim_if_missing_publication_timestamp_nonzero",
        },
    }
    (output_dir / "pilot_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    manifest["output_files"] = {
        path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "pilot_manifest.json"
    }
    (output_dir / "pilot_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/reit_annual_construction.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("research/reit_annual_construction"))
    parser.add_argument("--pilot", action="store_true", help="query public STAC metadata for configured AOIs/years")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.pilot:
        manifest = _pilot(config, args.output_dir)
        print(json.dumps({"all_items": manifest["all_items"], "selected_annual_items": manifest["selected_annual_items"]}, indent=2))
    else:
        print("No raster download requested. Use --pilot for an auditable STAC metadata pilot.")


if __name__ == "__main__":
    main()
