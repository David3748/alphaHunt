#!/usr/bin/env python3
"""Build a survivorship-reduced safety corpus over a configurable SEC period."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import html
import io
import json
import re
import statistics
import sys
import urllib.error
import zipfile
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ox_lab as ox
import long_lab


FORMS = {"10-K", "10-Q", "20-F", "40-F"}
YEARS = (2019, 2020)
QUARTERS = (1, 2, 3, 4)
DATASET_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{year}q{quarter}.zip"


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def enumerate_filings(run_dir: Path, years: tuple[int, ...] = YEARS) -> dict:
    output = run_dir / "filings.jsonl"
    if output.exists():
        rows = ox.load_jsonl(output)
        return {"filings": len(rows), "cached": True}
    http = ox.CachedHTTP(run_dir / "cache" / "sec_bulk", min_interval=0.13)
    rows = []
    for year in years:
        for quarter in QUARTERS:
            # The DERA release calendar lags quarter close; treat an absent
            # zip as "not published yet" rather than a pipeline failure.
            try:
                raw = http.get(DATASET_URL.format(year=year, quarter=quarter), timeout=180)
            except RuntimeError as exc:
                if "HTTP Error 404" in str(exc) or "404" in str(exc):
                    print(f"skipped {year}q{quarter}: dataset not published", file=sys.stderr)
                    continue
                raise
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                reader = csv.DictReader(
                    archive.read("sub.txt").decode("latin1").splitlines(), delimiter="\t")
                for source in reader:
                    form = source.get("form")
                    filed = source.get("filed") or ""
                    if form not in FORMS or source.get("detail") != "1" or not filed.startswith(str(year)):
                        continue
                    accession = source["adsh"]
                    instance = source.get("instance") or ""
                    cik = str(int(source["cik"]))
                    if not instance:
                        continue
                    rows.append({
                        "accession": accession, "cik": cik, "company": source.get("name"),
                        "sic": source.get("sic"), "form": form,
                        "period": source.get("period"), "filed": filed,
                        "accepted": source.get("accepted"), "instance": instance,
                        "afs": source.get("afs"), "countryinc": source.get("countryinc"),
                        "url": (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                                f"{accession.replace('-', '')}/{instance}"),
                    })
            print(f"enumerated {year}q{quarter}: cumulative={len(rows)}", file=sys.stderr)
    unique = {row["accession"]: row for row in rows}
    rows = sorted(unique.values(), key=lambda row: (row["filed"], row["accepted"] or "", row["accession"]))
    for row in rows:
        append_jsonl(output, row)
    result = {"filings": len(rows), "ciks": len({row['cik'] for row in rows}),
              "start": rows[0]["filed"], "end": rows[-1]["filed"], "cached": False}
    (run_dir / "enumeration.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def clean_fact(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def facts(raw: bytes, concept: str) -> list[str]:
    text = raw.decode("utf-8", errors="replace")
    patterns = [
        rf"<[^>]*:?{concept}\b[^>]*>(.*?)</[^>]*:?{concept}>",
        rf"<ix:nonNumeric\b(?=[^>]*\bname=[\"'][^\"']*:?{concept}[\"'])[^>]*>(.*?)</ix:nonNumeric>",
    ]
    values = []
    for pattern in patterns:
        for match in re.findall(pattern, text, re.I | re.S):
            value = clean_fact(match)
            if value and value.lower() not in {"n/a", "none", "not applicable"} and value not in values:
                values.append(value)
    return values


def instance_hint(value: str) -> str:
    value = value.lower().rsplit("/", 1)[-1]
    value = re.split(r"[-_]", value)[0]
    value = re.sub(r"\d.*", "", value)
    value = re.sub(r"(?:form|xbrl|instance|htm|xml)$", "", value)
    return value or "?"


def resolve_one(row: dict, http: ox.CachedHTTP) -> dict:
    try:
        raw = http.get(row["url"], timeout=90)
        result = {"accession": row["accession"], "cik": row["cik"],
                "instance_hint": instance_hint(row["instance"]),
                "symbols": facts(raw, "TradingSymbol"),
                "exchanges": facts(raw, "SecurityExchangeName"),
                "bytes": len(raw), "error": None}
        # Parsed facts are the durable artifact. Avoid retaining thousands of
        # redundant multi-megabyte XBRL instances on a constrained local disk.
        http._path(row["url"]).unlink(missing_ok=True)
        return result
    except Exception as exc:
        return {"accession": row["accession"], "cik": row["cik"],
                "instance_hint": instance_hint(row["instance"]),
                "symbols": [], "exchanges": [], "bytes": 0,
                "error": f"{type(exc).__name__}: {exc}"}


def resolve_symbols(run_dir: Path, concurrency: int = 8, limit: int | None = None) -> dict:
    filings = ox.load_jsonl(run_dir / "filings.jsonl")
    # One filing-native fact fetch per issuer/filename-stem regime. Stable stems
    # collapse repetitive quarters; a changed stem triggers a new resolution.
    representatives = {}
    for row in filings:
        representatives.setdefault((row["cik"], instance_hint(row["instance"])), row)
    filings = list(representatives.values())
    if limit:
        filings = filings[:limit]
    output = run_dir / "symbols.jsonl"
    existing = {row["accession"]: row for row in ox.load_jsonl(output)}
    pending = [row for row in filings if row["accession"] not in existing]
    http = ox.CachedHTTP(run_dir / "cache" / "sec_instance", min_interval=0.13)
    completed = failures = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(resolve_one, row, http): row for row in pending}
        for future in cf.as_completed(futures):
            result = future.result()
            append_jsonl(output, result)
            completed += 1
            failures += bool(result["error"])
            if completed % 250 == 0:
                print(f"resolved {completed}/{len(pending)} failures={failures}", file=sys.stderr)
    rows = ox.load_jsonl(output)
    result = {"target": len(filings), "resolved_rows": len(rows),
              "with_symbol": sum(bool(row.get("symbols")) for row in rows),
              "with_exchange": sum(bool(row.get("exchanges")) for row in rows),
              "failures": sum(bool(row.get("error")) for row in rows)}
    (run_dir / "symbol_status.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def normalized_symbol(row: dict) -> tuple[str | None, str]:
    values = row.get("symbols") or []
    source = "dei_fact"
    if not values:
        values, source = [row.get("instance_hint")], "instance_stem"
    for value in values:
        value = str(value or "").strip().upper().replace(".", "-")
        if re.fullmatch(r"[A-Z][A-Z0-9-]{0,9}", value):
            return value, source
    return None, "unresolved"


def preprice(run_dir: Path, concurrency: int = 12) -> dict:
    output = run_dir / "preprice.jsonl"
    if output.exists():
        rows = ox.load_jsonl(output)
        return {"rows": len(rows), "eligible": sum(row.get("status") == "eligible" for row in rows),
                "cached": True}
    filings = ox.load_jsonl(run_dir / "filings.jsonl")
    resolutions = {(row["cik"], row["instance_hint"]): row
                   for row in ox.load_jsonl(run_dir / "symbols.jsonl")}
    groups = defaultdict(list)
    unresolved = []
    for filing in filings:
        key = (filing["cik"], instance_hint(filing["instance"]))
        resolution = resolutions.get(key)
        if not resolution:
            unresolved.append({**filing, "status": "unresolved_identifier", "symbol": None})
            continue
        symbol, symbol_source = normalized_symbol(resolution)
        if not symbol:
            unresolved.append({**filing, "status": "unresolved_identifier", "symbol": None})
            continue
        groups[symbol].append((filing, symbol_source, resolution.get("exchanges") or []))
    http = ox.CachedHTTP(run_dir / "cache" / "preprice", min_interval=0.08)
    filing_days = [dt.date.fromisoformat(
        f"{row['filed'][:4]}-{row['filed'][4:6]}-{row['filed'][6:8]}") for row in filings]
    if not filing_days:
        raise RuntimeError("no filings available for pre-signal pricing")
    start = min(filing_days) - dt.timedelta(days=400)
    end = max(filing_days) + dt.timedelta(days=2)

    def work(symbol):
        try:
            return symbol, *long_lab.chart_series(symbol, start, end, http), None
        except Exception as exc:
            return symbol, [], {}, f"{type(exc).__name__}: {exc}"

    results = list(unresolved)
    completed = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(work, symbol): symbol for symbol in groups}
        for future in cf.as_completed(futures):
            symbol, prices, meta, error = future.result()
            exchange = str(meta.get("exchangeName") or meta.get("fullExchangeName") or "").upper()
            for filing, symbol_source, filing_exchanges in groups[symbol]:
                cutoff = dt.date.fromisoformat(
                    f"{filing['filed'][:4]}-{filing['filed'][4:6]}-{filing['filed'][6:8]}")
                history = [row for row in prices
                           if cutoff - dt.timedelta(days=370) <= row["date"] < cutoff]
                record = {**filing, "symbol": symbol, "symbol_source": symbol_source,
                          "filing_exchanges": filing_exchanges, "vendor_exchange": exchange,
                          "price_error": error}
                if error or not prices:
                    record["status"] = "no_price_history"
                elif exchange not in long_lab.LISTED_EXCHANGES:
                    record["status"] = "non_us_listed_or_unknown_exchange"
                elif len(history) < 120:
                    record["status"] = "insufficient_pre_signal_history"
                else:
                    peak, last = max(row["close"] for row in history), history[-1]["close"]
                    recent = history[-30:]
                    adv = statistics.mean(row["close"] * row["volume"] for row in recent)
                    drawdown = last / peak - 1 if peak else None
                    record.update({"pre_signal_close": last, "pre_signal_date": history[-1]["date"].isoformat(),
                                   "pre_signal_observations": len(history),
                                   "drawdown_from_370d_high": drawdown,
                                   "avg_dollar_volume_30d": adv})
                    if last < 1:
                        record["status"] = "price_below_1"
                    elif adv < 1_000_000:
                        record["status"] = "adv_below_1m"
                    elif drawdown > -0.40:
                        record["status"] = "drawdown_above_minus40pct"
                    else:
                        record["status"] = "market_eligible"
                results.append(record)
            completed += 1
            if completed % 250 == 0:
                print(f"preprice symbols {completed}/{len(groups)}", file=sys.stderr)
    # Apply the locked 180-day cooldown only after all pre-signal checks exist.
    last_eligible = {}
    for row in sorted(results, key=lambda value: (value["filed"], value["accepted"] or "", value["accession"])):
        if row.get("status") != "market_eligible":
            continue
        day = dt.date.fromisoformat(f"{row['filed'][:4]}-{row['filed'][4:6]}-{row['filed'][6:8]}")
        previous = last_eligible.get(row["cik"])
        if previous and (day - previous).days < 180:
            row["status"] = "cooldown_180d"
        else:
            row["status"] = "eligible"
            last_eligible[row["cik"]] = day
    results.sort(key=lambda row: (row["filed"], row["accepted"] or "", row["accession"]))
    for row in results:
        append_jsonl(output, row)
    counts = dict(sorted(Counter(row["status"] for row in results).items()))
    summary = {"rows": len(results), "symbols_queried": len(groups),
               "eligible": counts.get("eligible", 0), "status_counts": counts, "cached": False}
    (run_dir / "preprice_status.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def build_case(row: dict, http: ox.CachedHTTP,
               experiment: str = "sealed_safety_2019_2020_v1") -> tuple[dict | None, dict | None]:
    try:
        sec_name, filings = ox.submission_rows(row["cik"], http, include_archives=True)
        anchor = next((item for item in filings if item["accessionNumber"] == row["accession"]), None)
        if not anchor:
            return None, {"accession": row["accession"], "cik": row["cik"],
                          "error": "accession missing from submissions history"}
        cutoff = dt.date.fromisoformat(
            f"{row['filed'][:4]}-{row['filed'][4:6]}-{row['filed'][6:8]}")
        prior_start = cutoff - dt.timedelta(days=180)
        prior = [item for item in filings
                 if prior_start <= dt.date.fromisoformat(item["date"]) <= cutoff
                 and item["accessionNumber"] != row["accession"]
                 and item["form"] in ox.CONTEXT_FORMS]
        prior.sort(key=lambda item: (item["date"], item["accessionNumber"]), reverse=True)
        # The anchor filing is complete and is the only uniformly available
        # evidence item across active and inactive issuers. The protocol allows
        # up to four priors; use zero here to preserve exhaustive coverage and
        # keep the sealed run within the temporary model/data window.
        snapshot_rows = [anchor]
        pack = ox.make_pack(snapshot_rows, http,
                            [row["symbol"], row["cik"], row.get("company") or "", sec_name],
                            include_exhibits=False, per_filing_chars=220_000,
                            total_chars=700_000)
        if len(pack) < 2_000:
            return None, {"accession": row["accession"], "cik": row["cik"],
                          "error": f"evidence pack too short: {len(pack)}"}
        case_id = hashlib.sha256(
            f"{experiment}|{row['cik']}|{row['accession']}".encode()).hexdigest()[:20]
        sources = []
        for item in snapshot_rows:
            source = {"form": item["form"], "date": item["date"],
                      "accession": item["accessionNumber"],
                      "primaryDocument": item.get("primaryDocument")}
            if item.get("acceptanceDateTime"):
                source["acceptanceDateTime"] = item["acceptanceDateTime"]
            sources.append(source)
        return {
            "experiment": experiment, "case_id": case_id,
            "ticker": row["symbol"], "company": row.get("company"), "cik": row["cik"],
            "cutoff": cutoff.isoformat(), "accepted": row.get("accepted"),
            "anchor_form": row["form"], "anchor_accession": row["accession"],
            "snapshot_sources": sources, "snapshot_text": pack,
            "market_at_cutoff": {
                "pre_cutoff_close": row["pre_signal_close"],
                "pre_cutoff_date": row["pre_signal_date"],
                "drawdown_from_1y_high": row["drawdown_from_370d_high"],
                "avg_dollar_volume_30d": row["avg_dollar_volume_30d"],
                "historical_symbol_source": row["symbol_source"],
                "vendor_exchange": row["vendor_exchange"],
            },
            "selection_note": "Exhaustive SEC structured-filing population; no future-price eligibility filter.",
        }, None
    except Exception as exc:
        return None, {"accession": row["accession"], "cik": row["cik"],
                      "error": f"{type(exc).__name__}: {exc}"}


def build_cases(run_dir: Path, source_dir: Path, concurrency: int = 6,
                experiment: str = "sealed_safety_2019_2020_v1") -> dict:
    eligible = [row for row in ox.load_jsonl(run_dir / "preprice.jsonl")
                if row.get("status") == "eligible"]
    output, failures_path = source_dir / "cases.jsonl", run_dir / "case_build_failures.jsonl"
    existing = {row["anchor_accession"] for row in ox.load_jsonl(output)}
    failed = {row["accession"] for row in ox.load_jsonl(failures_path)}
    pending = [row for row in eligible if row["accession"] not in existing | failed]
    http = ox.CachedHTTP(run_dir / "cache" / "case_sec", min_interval=0.13)
    completed = failures = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(build_case, row, http, experiment): row for row in pending}
        for future in cf.as_completed(futures):
            case, failure = future.result()
            if case:
                append_jsonl(output, case)
                completed += 1
            else:
                append_jsonl(failures_path, failure)
                failures += 1
            if (completed + failures) % 25 == 0:
                print(f"case packs {completed + failures}/{len(pending)} ok={completed} failures={failures}",
                      file=sys.stderr)
    result = {"eligible": len(eligible), "cases": len(ox.load_jsonl(output)),
              "failures": len(ox.load_jsonl(failures_path))}
    (run_dir / "case_build_status.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def status(run_dir: Path) -> dict:
    result = {}
    for name in ("filings", "symbols", "preprice", "cases", "extractions", "syntheses", "outcomes"):
        result[name] = len(ox.load_jsonl(run_dir / f"{name}.jsonl"))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=Path("lab_runs/sealed_safety"))
    parser.add_argument("--start-year", type=int, default=YEARS[0])
    parser.add_argument("--end-year", type=int, default=YEARS[-1])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("enumerate")
    resolve = sub.add_parser("resolve-symbols")
    resolve.add_argument("--concurrency", type=int, default=8)
    resolve.add_argument("--limit", type=int, default=None)
    price = sub.add_parser("preprice")
    price.add_argument("--concurrency", type=int, default=12)
    build = sub.add_parser("build-cases")
    build.add_argument("--source-dir", type=Path, default=Path("lab_runs/sealed_safety_source"))
    build.add_argument("--concurrency", type=int, default=6)
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.start_year > args.end_year:
        parser.error("--start-year must not exceed --end-year")
    years = tuple(range(args.start_year, args.end_year + 1))
    experiment = f"sealed_safety_{args.start_year}_{args.end_year}_v1"
    args.run_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "enumerate":
        result = enumerate_filings(args.run_dir, years)
    elif args.command == "resolve-symbols":
        result = resolve_symbols(args.run_dir, args.concurrency, args.limit)
    elif args.command == "preprice":
        result = preprice(args.run_dir, args.concurrency)
    elif args.command == "build-cases":
        result = build_cases(args.run_dir, args.source_dir, args.concurrency, experiment)
    else:
        result = status(args.run_dir)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
