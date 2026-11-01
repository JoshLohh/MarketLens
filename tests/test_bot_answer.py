"""Offline routing and answer-rendering checks."""

import unittest

from marketlens_bot.answer import coverage_note, rag_answer, render_checked_answer, render_excerpts, route_question
from qa_contract import answer_record, citation, fingerprint


def pack():
    result = {"corpus_version": "sec-qa-pilot-v1", "ticker": "NBIS", "question": "NBIS revenue?", "passages": [
        {"chunk_id": "NBIS-S1:00001", "source_id": "NBIS-S1", "block_start": 1,
         "block_end": 1, "text": "Revenue was USD 10 million."}],
        "sources": {"NBIS-S1": {"ticker": "NBIS", "form": "6-K", "reporting_period": "Q3 2024",
                                 "sec_url": "https://www.sec.gov/example"}}}
    result["evidence_hash"] = fingerprint(result)
    return result


class AnswerTests(unittest.TestCase):
    def test_routing_requires_exactly_one_supported_company(self):
        self.assertEqual(route_question("What did Nebius report?"), "NBIS")
        self.assertEqual(route_question("AST SpaceMobile revenue?"), "ASTS")
        self.assertIsNone(route_question("Compare NBIS with IREN"))
        self.assertIsNone(route_question("What was revenue?"))

    def test_excerpt_includes_verbatim_text_and_source(self):
        text = render_excerpts(pack())
        self.assertIn("Revenue was USD 10 million.", text)
        self.assertIn("NBIS-S1", text)
        self.assertIn("https://www.sec.gov/example", text)

    def test_excerpt_keeps_value_after_table_heading(self):
        evidence = pack()
        evidence["passages"][0]["text"] = "Table heading " + "x" * 500 + "\nTotal revenue | 501,023"
        text = render_excerpts(evidence)
        self.assertIn("Total revenue | 501,023", text)

    def test_changed_citation_is_rejected(self):
        evidence = pack()
        source = citation(evidence, "NBIS-S1:00001", "Revenue was USD 10 million.")
        answer = answer_record("structured", "NBIS", evidence, "answered")
        answer["claims"] = [{"claim_id": "claim-1", "ticker": "NBIS", "company_name": "Nebius Group",
                             "period": "Q3 2024", "accounting_basis": "reported", "text": "Revenue was USD 10 million.",
                             "numbers": [], "citations": [source]}]
        self.assertIn("https://www.sec.gov/example", render_checked_answer(answer, evidence))
        source["sec_url"] = "https://example.invalid/changed"
        with self.assertRaises(ValueError):
            render_checked_answer(answer, evidence)

    def test_sources_are_deduplicated_per_claim_after_validation(self):
        evidence = pack()
        evidence["passages"].append({"chunk_id": "NBIS-S2:00001", "source_id": "NBIS-S2",
                                     "block_start": 1, "block_end": 1, "text": "Separate filing."})
        evidence["sources"]["NBIS-S2"] = {"ticker": "NBIS", "form": "20-F",
                                           "reporting_period": "FY 2024", "sec_url": "https://www.sec.gov/other"}
        evidence["evidence_hash"] = fingerprint({k: v for k, v in evidence.items() if k != "evidence_hash"})
        source = citation(evidence, "NBIS-S1:00001", "Revenue was USD 10 million.")
        other = citation(evidence, "NBIS-S2:00001", "Separate filing.")
        answer = answer_record("structured", "NBIS", evidence, "answered")
        claim = {"claim_id": "claim-1", "ticker": "NBIS", "company_name": "Nebius Group",
                 "period": "Q3 2024", "accounting_basis": "reported", "text": "First claim.",
                 "numbers": [], "citations": [source, source.copy(), other]}
        answer["claims"] = [claim, {**claim, "claim_id": "claim-2", "text": "Second claim.", "citations": [source]}]
        rendered = render_checked_answer(answer, evidence)
        self.assertIn("First claim.\n\nSource:", rendered)
        self.assertIn("https://www.sec.gov/other\n\nSecond claim.\n\nSource:", rendered)
        self.assertEqual(rendered.count("https://www.sec.gov/example"), 2)
        self.assertEqual(rendered.count("https://www.sec.gov/other"), 1)
        source["sec_url"] = "https://example.invalid/changed"
        with self.assertRaises(ValueError):
            render_checked_answer(answer, evidence)

    def test_coverage_date_is_for_selected_company(self):
        manifest = {"documents": [{"ticker": "NBIS", "issuer_publication_date": "2024-10-31"},
                                  {"ticker": "IREN", "issuer_publication_date": "2025-01-01"}]}
        self.assertIn("2024-10-31", coverage_note(manifest, "NBIS"))
        self.assertNotIn("2025-01-01", coverage_note(manifest, "NBIS"))

    def test_coverage_date_falls_back_to_sec_filing_date(self):
        manifest = {"documents": [
            {"ticker": "IREN", "issuer_publication_date": "2024-12-06"},
            {"ticker": "IREN", "issuer_publication_date": None, "sec_filing_date": "2025-08-28"},
        ]}
        note = coverage_note(manifest, "IREN")
        self.assertIn("2025-08-28", note)
        self.assertNotIn("2024-12-06", note)

    def test_fake_provider_abstains_or_rejects_an_invented_quote(self):
        class FakeProvider:
            execution_kind = "offline_fixture"

            def __init__(self, payload):
                self.payload = payload

            def generate(self, _request):
                return {"answer": self.payload, "usage": {"input_tokens": 1, "output_tokens": 1},
                        "estimated_cost_usd": "0", "timing_ms": {}}

        evidence = pack()
        abstention = FakeProvider({"status": "abstained", "reason": "Evidence is insufficient.", "claims": []})
        self.assertEqual(rag_answer(evidence, abstention)["status"], "abstained")
        invalid = FakeProvider({"status": "answered", "reason": "", "claims": [{
            "text": "An unsupported statement.", "ticker": "NBIS", "company_name": "Nebius Group",
            "period": "Q3 2024", "accounting_basis": "reported", "numbers": [],
            "citations": [{"chunk_id": "NBIS-S1:00001", "quote": "invented quotation"}]}]})
        answer = rag_answer(evidence, invalid)
        self.assertEqual(answer["status"], "error")
        self.assertEqual(answer["claims"], [])
        self.assertNotIn("invented quotation", str(answer))


if __name__ == "__main__":
    unittest.main()
