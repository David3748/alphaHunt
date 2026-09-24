import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fr337


# 6 fixture titles: 4 ladder rungs + 2 misc exclusions (deadline + enforcement)
FIXTURES = {
    "receipt": "Notice of Receipt of Complaint; Solicitation of Comments Relating to the Public Interest",
    "institution": ("Certain Mobile Devices With Hardware and Software for Exchanging "
                    "Electronic Content; Institution of Investigation"),
    "final": ("Certain Glass Substrates for Liquid Crystal Displays, Products Containing the Same, "
              "and Methods for Manufacturing the Same II; Notice of the Commission's Final "
              "Determination Finding a Violation of Section 337; Issuance of a Limited "
              "Exclusion Order and Cease and Desist Order; Termination of the Investigation"),
    "remedy": ("Certain Shapewear Garments; Notice of a Commission Determination Not To Review "
               "an Initial Determination Terminating the Investigation Based on Consent Order; "
               "Termination of Investigation"),
    "misc_deadline": "Notice of Extension of the Deadline for Submissions on the Public Interest",
    "misc_enforcement": "Notice of Institution of Formal Enforcement Proceeding",
}


class Fr337Test(unittest.TestCase):
    def test_ladder_receipt(self):
        et, ladder = fr337.classify_title(FIXTURES["receipt"])
        self.assertEqual(et, "receipt")
        self.assertEqual(ladder, "complaint")

    def test_ladder_institution(self):
        et, ladder = fr337.classify_title(FIXTURES["institution"])
        self.assertEqual(et, "institution")
        self.assertEqual(ladder, "institution")

    def test_ladder_final(self):
        et, ladder = fr337.classify_title(FIXTURES["final"])
        self.assertEqual(et, "final_determination")
        self.assertEqual(ladder, "final")

    def test_ladder_remedy_consent(self):
        et, ladder = fr337.classify_title(FIXTURES["remedy"])
        self.assertEqual(et, "remedy")
        self.assertEqual(ladder, "final")

    def test_misc_exclusions_stay_misc(self):
        for key in ("misc_deadline", "misc_enforcement"):
            et, ladder = fr337.classify_title(FIXTURES[key])
            self.assertEqual(et, "misc", key)
            self.assertEqual(ladder, "misc", key)
        # Public-interest remedy request without receipt language is misc too
        et, _ = fr337.classify_title(
            "Request for Written Submissions on Remedy, the Public Interest, and Bonding")
        self.assertEqual(et, "misc")

    def test_amended_receipt_and_final_no_violation(self):
        et, ladder = fr337.classify_title(
            "Notice of Receipt of Amended Complaint; Solicitation of Comments "
            "Relating to the Public Interest")
        self.assertEqual((et, ladder), ("receipt", "complaint"))
        et, ladder = fr337.classify_title(
            "Certain Disposable Vaporizer Devices; Notice of Final Commission "
            "Determination of No Violation; Termination of Investigation")
        self.assertEqual((et, ladder), ("final_determination", "final"))

    def test_id_maps_to_id_rung(self):
        et, ladder = fr337.classify_title(
            "Certain Motorized Self-Balancing Vehicles; Notice of a Commission Determination "
            "To Review in Part a Final Initial Determination Finding a Violation of Section 337; "
            "Request for Written Submissions on the Issues Under Review")
        self.assertEqual(ladder, "id")

    def test_pagination_stub(self):
        pages = [
            {"count": 3, "total_pages": 2,
             "next_page_url": "http://x/?page=2",
             "results": [{"document_number": "a", "title": FIXTURES["receipt"],
                          "publication_date": "2026-08-01"},
                         {"document_number": "b", "title": FIXTURES["institution"],
                          "publication_date": "2026-08-02"}]},
            {"count": 3, "total_pages": 2, "next_page_url": None,
             "results": [{"document_number": "c", "title": FIXTURES["final"],
                          "publication_date": "2026-08-03"}]},
        ]

        def stub(page, per_page):
            return pages[page - 1]

        docs, meta = fr337.sweep(max_docs=10, per_page=2, fetcher=stub, sleep_s=0)
        self.assertEqual([d["document_number"] for d in docs], ["a", "b", "c"])
        self.assertIn("pulled_at", meta)

    def test_pit_timestamp_prefers_public_inspection(self):
        pi_doc = {"publication_date": "2026-09-03",
                  "filed_at": "2026-09-02T12:45:00Z",
                  "title": "x"}
        self.assertEqual(fr337.pit_timestamp(pi_doc), "2026-09-02T12:45:00Z")
        pub_doc = {"publication_date": "2026-09-01", "title": "x"}
        self.assertEqual(fr337.pit_timestamp(pub_doc), "2026-09-01")

    def test_investigation_no_and_alias_hook(self):
        doc = {"document_number": "2026-17864", "title": FIXTURES["institution"],
               "abstract": "blah [Investigation No. 337-TA-1520] blah",
               "publication_date": "2026-09-01"}
        ev = fr337.classify_doc(doc, alias_index={"apple": ("AAPL", "0000320193")})
        self.assertEqual(ev["investigation_no"], "337-TA-1520")
        self.assertEqual(ev["ladder_stage"], "institution")
        # hook shape: unmatched stub names keep ticker None, matched resolve
        resolved = fr337.resolve_names(["Apple Inc."], {"apple": ("AAPL", "0000320193")})
        self.assertEqual(resolved[0]["ticker"], "AAPL")


if __name__ == "__main__":
    unittest.main()
