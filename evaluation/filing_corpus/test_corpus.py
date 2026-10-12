"""Contract checks for visible text and all-or-nothing corpus publishing."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import corpus


class CorpusTests(unittest.TestCase):
    def test_table_rows_keep_numeric_cells_with_labels(self) -> None:
        raw = b"""<html><body><table><tr><td><p>Cash and cash equivalents</p></td>
        <td><p>564,988</p></td><td><p>85,622</p></td></tr></table></body></html>"""
        blocks = corpus.extract_blocks(raw)
        self.assertTrue(any("Cash and cash equivalents" in block and "564,988" in block for block in blocks))

    def test_extraction_omits_head_ixbrl_and_hidden_tables(self) -> None:
        raw = b"""<html><head><meta charset='utf-8'><title>not evidence</title></head>
        <body><p>Visible filing evidence</p>
        <ix:hidden><div><span>secret fact</span></div></ix:hidden>
        <div style='display:none'><table><tr><td>hidden total</td></tr></table></div>
        <p>Reported total 42 USD</p></body></html>"""
        text = "\n".join(corpus.extract_blocks(raw))
        self.assertIn("Visible filing evidence", text)
        self.assertIn("Reported total 42 USD", text)
        for excluded in ("not evidence", "secret fact", "hidden total"):
            self.assertNotIn(excluded, text)

    def test_s1_s2_hash_audit_checks_all_six_without_publishing_a_release(self) -> None:
        seed = json.loads(corpus.SEED.read_text())
        selected = [
            item for item in seed["documents"]
            if item["source_id"].endswith(("-S1", "-S2"))
        ]
        markers = [marker for item in seed["documents"] for marker in item["validation_markers"]]
        raw = ("<html><body>" + "".join(f"<p>{marker}</p>" for marker in markers) + "</body></html>").encode()
        urls: list[str] = []

        class FakeClient:
            def get(self, url: str, max_bytes: int) -> bytes:
                urls.append(url)
                return raw

        def fake_filings(_client: FakeClient, cik: str, _needed: set[str]) -> dict:
            return {item["accession"]: {
                "form": item["form"],
                "acceptanceDateTime": item["sec_metadata"]["acceptance"],
                "filingDate": item["sec_metadata"]["filing_date"],
                "reportDate": item["sec_metadata"]["report_date"],
            } for item in selected if item["cik"] == cik}

        with patch.object(corpus, "SecClient", return_value=FakeClient()), patch.object(
            corpus, "filings_for_cik", side_effect=fake_filings
        ):
            reports = corpus.audit_s1_s2()

        self.assertEqual([report["source_id"] for report in reports], [
            "NBIS-S1", "NBIS-S2", "IREN-S1", "IREN-S2", "ASTS-S1", "ASTS-S2",
        ])
        self.assertEqual(len(urls), 6)
        for report in reports:
            self.assertTrue(report["identity_timing_match"])
            self.assertTrue(report["content_markers_match"])
            self.assertEqual(report["current_raw_bytes"], len(raw))
            self.assertEqual(report["current_raw_sha256"], corpus.sha256(raw))
        self.assertFalse(reports[2]["hash_match"])

    def test_single_source_audit_checks_markers_even_when_hash_changed(self) -> None:
        seed = json.loads(corpus.SEED.read_text())
        item = next(doc for doc in seed["documents"] if doc["source_id"] == "ASTS-S3")
        raw = ("<html><body>" + "<p>Additional visible filing text</p>" + "".join(
            f"<p>{marker}</p>" for marker in item["validation_markers"]
        ) + "<p>Another visible filing section</p></body></html>").encode()

        class FakeClient:
            def get(self, url: str, max_bytes: int) -> bytes:
                return raw

        def fake_filings(_client: FakeClient, cik: str, _needed: set[str]) -> dict:
            return {item["accession"]: {
                "form": item["form"],
                "acceptanceDateTime": item["sec_metadata"]["acceptance"],
                "filingDate": item["sec_metadata"]["filing_date"],
                "reportDate": item["sec_metadata"]["report_date"],
            }}

        with patch.object(corpus, "SecClient", return_value=FakeClient()), patch.object(
            corpus, "filings_for_cik", side_effect=fake_filings
        ):
            report = corpus.audit_sources(["ASTS-S3"])[0]

        self.assertEqual(report["source_id"], "ASTS-S3")
        self.assertTrue(report["identity_timing_match"])
        self.assertTrue(report["content_markers_match"])
        self.assertFalse(report["hash_match"])

    def test_failed_build_discards_partial_staging_and_preserves_prior_release(self) -> None:
        seed = json.loads(corpus.SEED.read_text())
        raw = b"<html><body>" + b"".join(
            f"<p>Visible source marker {number}</p>".encode() for number in range(6)
        ) + b"</body></html>"
        for item in seed["documents"]:
            item.pop("audit_raw_sha256", None)
            item.pop("expected_raw_sha256", None)
            item["validation_markers"] = ["visible source marker"]
        seed["documents"][-1]["validation_markers"] = ["deliberately absent"]
        # The current pin must win over the older audit hash for this source.
        seed["documents"][0]["audit_raw_sha256"] = corpus.sha256(b"historical audit response")
        seed["documents"][0]["expected_raw_sha256"] = corpus.sha256(raw)

        class FakeClient:
            def get(self, url: str, max_bytes: int) -> bytes:
                return raw

        def fake_filings(_client: FakeClient, cik: str, _needed: set[str]) -> dict:
            return {item["accession"]: {
                "form": item["form"],
                "acceptanceDateTime": item["sec_metadata"]["acceptance"],
                "filingDate": item["sec_metadata"]["filing_date"],
                "reportDate": item["sec_metadata"]["report_date"],
            } for item in seed["documents"] if item["cik"] == cik}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed_path = root / "sources.json"
            seed_path.write_text(json.dumps(seed))
            local = root / "local"
            older = local / "releases" / "previous-v1"
            older.mkdir(parents=True)
            (older / "keep.txt").write_text("prior release")
            with patch.object(corpus, "SecClient", return_value=FakeClient()), patch.object(
                corpus, "filings_for_cik", side_effect=fake_filings
            ):
                with self.assertRaisesRegex(ValueError, "Source markers missing"):
                    corpus.build(seed_path, local)
            self.assertEqual((older / "keep.txt").read_text(), "prior release")
            self.assertFalse((local / "releases" / seed["corpus_version"]).exists())
            self.assertEqual(list((local / "staging").iterdir()), [])

            seed["documents"][-1]["validation_markers"] = ["visible source marker"]
            seed["documents"][0]["expected_raw_sha256"] = corpus.sha256(b"unrecognized response")
            seed_path.write_text(json.dumps(seed))
            with patch.object(corpus, "SecClient", return_value=FakeClient()), patch.object(
                corpus, "filings_for_cik", side_effect=fake_filings
            ):
                with self.assertRaisesRegex(ValueError, "SEC raw hash mismatch"):
                    corpus.build(seed_path, local)
            self.assertEqual(list((local / "staging").iterdir()), [])

            seed["documents"][0]["expected_raw_sha256"] = corpus.sha256(raw)
            seed_path.write_text(json.dumps(seed))
            with patch.object(corpus, "SecClient", return_value=FakeClient()), patch.object(
                corpus, "filings_for_cik", side_effect=fake_filings
            ):
                release = corpus.build(seed_path, local)
            with patch.object(corpus, "SEED", seed_path):
                self.assertEqual(len(corpus.verify(release)["documents"]), 12)
                manifest_path = release / "manifest.json"
                manifest = json.loads(manifest_path.read_text())
                manifest["documents"][0]["sec_url"] = "https://www.sec.gov/wrong"
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "Manifest hash mismatch"):
                    corpus.verify(release)
                (release / "manifest.sha256").write_text(corpus.sha256(manifest_path.read_bytes()))
                with self.assertRaisesRegex(ValueError, "Manifest SEC URL mismatch"):
                    corpus.verify(release)
                manifest["documents"][0]["sec_url"] = corpus.sec_document_url(seed["documents"][0])
                manifest["documents"][0]["acceptance_datetime_raw"] = "2026-01-01T00:00:00.000Z"
                manifest_path.write_text(json.dumps(manifest))
                (release / "manifest.sha256").write_text(corpus.sha256(manifest_path.read_bytes()))
                with self.assertRaisesRegex(ValueError, "Manifest SEC timing mismatch"):
                    corpus.verify(release)


if __name__ == "__main__":
    unittest.main()
