from dataclasses import replace
from pathlib import Path

import pandas as pd

from src.reit_exposure_history import (
    FilingRecord,
    SecClient,
    asof_exposure,
    extract_property_tables,
    exposure_weights,
    map_metro,
)


FIXTURES = Path(__file__).parent / "fixtures" / "reit_exposure_history"


def filing(accepted: str, accession: str = "0000000000-23-000001") -> FilingRecord:
    return FilingRecord(
        issuer="Prologis", cik="0001045609", form="10-K", accession_number=accession,
        filing_date=accepted[:10], report_date="2022-12-31", accepted_at=accepted,
        primary_document="fixture.html", source_url="https://www.sec.gov/fixture.html",
    )


def test_extracts_property_location_area_and_metro():
    html = (FIXTURES / "prologis_2022.html").read_text()
    rows = extract_property_tables(html, filing("2023-02-10T21:00:00Z"), {"Atlanta": "Atlanta", "Dallas": "Dallas-Fort Worth"})
    assert len(rows) == 2
    assert rows[0].metro == "Atlanta"
    assert rows[0].area_value == 1250000
    assert rows[0].accepted_at == "2023-02-10T21:00:00+00:00"
    assert rows[0].source_excerpt


def test_asof_uses_acceptance_not_report_date_and_blocks_lookahead():
    old = extract_property_tables((FIXTURES / "prologis_2022.html").read_text(), filing("2023-02-10T21:00:00Z", "old"), {"Atlanta": "Atlanta", "Dallas": "Dallas-Fort Worth"})
    new_filing = replace(filing("2024-02-10T21:00:00Z", "new"), report_date="2023-12-31")
    new = extract_property_tables((FIXTURES / "prologis_2023.html").read_text(), new_filing, {"Atlanta": "Atlanta", "Dallas": "Dallas-Fort Worth"})
    before = asof_exposure(old + new, "2024-02-01T23:59:59Z")
    assert set(before["accession_number"]) == {"old"}
    after = asof_exposure(old + new, "2024-02-11T00:00:00Z")
    assert set(after["accession_number"]) == {"new"}


def test_unresolved_location_is_visible_in_weights():
    rows = extract_property_tables((FIXTURES / "prologis_2023.html").read_text(), filing("2024-02-10T21:00:00Z"), {"Atlanta": "Atlanta", "Dallas": "Dallas-Fort Worth"})
    weights = exposure_weights(pd.DataFrame([r.__dict__ for r in rows]))
    assert "UNRESOLVED" in set(weights["metro"])
    assert abs(weights["weight"].sum() - 1.0) < 1e-9


def test_map_metro_does_not_guess_without_dictionary():
    assert map_metro("Atlanta, GA", {}) is None
    assert map_metro("Atlanta, GA", {"Atlanta": "Atlanta MSA"}) == "Atlanta MSA"


def test_sec_user_agent_is_required():
    try:
        SecClient("not-an-email")
    except ValueError:
        return
    raise AssertionError("SecClient accepted a non-contactable User-Agent")


def test_sec_user_agent_rejects_placeholder_domain():
    try:
        SecClient("alphaHunt research research@example.com")
    except ValueError:
        return
    raise AssertionError("SecClient accepted a reserved placeholder email")


def test_mention_only_rows_cannot_become_equal_weight_exposure():
    frame = pd.DataFrame({
        "issuer": ["Example REIT", "Example REIT"],
        "metro": ["Atlanta", "Dallas-Fort Worth"],
        "area_value": [None, None],
    })
    try:
        exposure_weights(frame)
    except ValueError:
        return
    raise AssertionError("Mention-only rows were converted into portfolio weights")
