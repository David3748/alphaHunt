import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import sec_enrich as se


class FakeHTTP:
    def json(self, url):
        return {
            "name": "Test",
            "filings": {"recent": {
                "form": ["10-K"], "filingDate": ["2024-01-02"],
                "accessionNumber": ["0000000001-24-000001"],
                "primaryDocument": ["annual.htm"],
                "acceptanceDateTime": ["2024-01-02T21:03:04.000Z"],
            }, "files": []},
        }


class SecEnrichTest(unittest.TestCase):
    def test_filing_index_retains_acceptance_timestamp(self):
        index = se.filing_index("1", FakeHTTP())
        row = index["0000000001-24-000001"]
        self.assertEqual(row["acceptanceDateTime"], "2024-01-02T21:03:04.000Z")
        self.assertEqual(row["primaryDocument"], "annual.htm")

    def test_date_fallback_is_end_of_day_utc(self):
        value = se.source_fallback_time({"date": "2024-01-02"})
        self.assertEqual(value.tzinfo, dt.timezone.utc)
        self.assertEqual(value.hour, 23)


if __name__ == "__main__":
    unittest.main()
