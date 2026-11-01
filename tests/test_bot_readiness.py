"""Offline checks for the retrieval readiness record and query boundary."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from marketlens_bot.__main__ import require_parity, save_parity
from marketlens_bot.weaviate_client import COLLECTION, WeaviateClient, WeaviateError
from marketlens_bot.weaviate_index import IndexError
from marketlens_bot.pilot import check_parity, frozen_benchmark_hash


class FakeClient(WeaviateClient):
    def __init__(self):
        self.requests = []

    def _request(self, path, *, data=None):
        self.requests.append((path, data))
        return {"data": {"Get": {COLLECTION: [{"_additional": {"id": "one", "distance": 0.25}}]}}}


class ReadinessTests(unittest.TestCase):
    def test_altered_benchmark_cannot_pass_parity_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            benchmark = Path(directory) / "benchmark.jsonl"
            benchmark.write_text("{}\n")
            with patch("marketlens_bot.pilot.BENCHMARK", benchmark):
                with self.assertRaises(IndexError):
                    frozen_benchmark_hash()

    def test_parity_record_requires_all_cases_and_exact_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaises(IndexError):
                require_parity(path, {"cache": "first"})
            save_parity(path, {"cache": "first"}, 21)
            require_parity(path, {"cache": "first"})
            with self.assertRaises(IndexError):
                require_parity(path, {"cache": "changed"})
            save_parity(path, {"cache": "first"}, 20)
            with self.assertRaises(IndexError):
                require_parity(path, {"cache": "first"})

    def test_vector_query_rejects_uncovered_ticker_and_wrong_dimensions(self):
        client = FakeClient()
        with self.assertRaises(WeaviateError):
            client.search_windows("OTHER", [0.0] * 384, 1)
        with self.assertRaises(WeaviateError):
            client.search_windows("NBIS", [0.0], 1)
        self.assertEqual(client.requests, [])
        self.assertEqual(client.search_windows("NBIS", [0.0] * 384, 1),
                         [{"id": "one", "distance": 0.25}])
        self.assertIn('valueText: "NBIS"', client.requests[0][1]["query"])

    def test_parity_compares_only_dense_candidates_used_by_fusion(self):
        class Encoder:
            @staticmethod
            def encode_query(_question):
                return []

        class LocalIndex:
            encoder = Encoder()

            def __init__(self):
                self.limits = []

            def dense_search(self, _ticker, _question, limit):
                self.limits.append(limit)
                return [{"chunk_id": "candidate", "score": 0.5}]

        class CandidateIndex:
            expected = {}

            def __init__(self):
                self.limits = []

            @staticmethod
            def window_scores(_ticker, _question):
                return {}

            def dense_search(self, _ticker, _question, limit):
                self.limits.append(limit)
                return [{"chunk_id": "candidate", "score": 0.5}]

        rows = [{"split": "development", "case_id": f"CASE-{n}",
                 "ticker": "IREN", "question": "IREN filing question"} for n in range(21)]
        with tempfile.TemporaryDirectory() as directory:
            benchmark = Path(directory) / "benchmark.jsonl"
            benchmark.write_text("".join(json.dumps(row) + "\n" for row in rows))
            local, candidate = LocalIndex(), CandidateIndex()
            with patch("marketlens_bot.pilot.BENCHMARK", benchmark), \
                    patch("marketlens_bot.pilot.frozen_benchmark_hash"), \
                    patch("marketlens_bot.pilot.query_result",
                          return_value={"ranked": [{"chunk_id": "candidate"}],
                                        "pack": {"evidence_hash": "fixed"}}):
                self.assertEqual(check_parity({}, [{"ticker": "IREN"}], local, candidate,
                                               split="development"), 21)
        expected = [50] * 21
        self.assertEqual(local.limits, expected)
        self.assertEqual(candidate.limits, expected)


if __name__ == "__main__":
    unittest.main()
