from __future__ import annotations

import hashlib
import json
import unittest

from marketlens_bot.perf_benchmark import (
    BenchmarkError,
    decode_queries,
    memory_limit_bytes,
    nearest_rank_percentile,
    pair_schedule,
    parse_memory_bytes,
    synthetic_updates,
)


class PerformanceBenchmarkTests(unittest.TestCase):
    def test_query_projection_rejects_answer_labels_and_unpinned_bytes(self):
        rows = [{"case_id": f"case-{index}", "ticker": "IREN", "question": "revenue?"}
                for index in range(21)]
        raw = json.dumps(rows, separators=(",", ":")).encode()
        digest = hashlib.sha256(raw).hexdigest()
        decoded, actual_digest = decode_queries(raw, expected_sha256=digest)
        self.assertEqual(len(decoded), 21)
        self.assertEqual(actual_digest, digest)

        rows[0]["expected_answer"] = "not permitted"
        altered = json.dumps(rows, separators=(",", ":")).encode()
        with self.assertRaises(BenchmarkError):
            decode_queries(altered, expected_sha256=hashlib.sha256(altered).hexdigest())
        with self.assertRaises(BenchmarkError):
            decode_queries(raw, expected_sha256="0" * 64)

    def test_nearest_rank_p95_is_stable_for_30_samples(self):
        self.assertEqual(nearest_rank_percentile(list(range(1, 31)), 95), 29)

    def test_docker_memory_parser_reads_used_value_and_binary_units(self):
        self.assertEqual(parse_memory_bytes("24.5MiB / 7.654GiB"), int(24.5 * 1024**2))
        self.assertEqual(parse_memory_bytes("2GB / 8GB"), 2 * 10**9)

    def test_memory_limit_uses_smaller_of_two_gib_or_quarter_ram(self):
        self.assertEqual(memory_limit_bytes(8 * 1024**3), 2 * 1024**3)
        self.assertEqual(memory_limit_bytes(4 * 1024**3), 1024**3)

    def test_pair_schedule_gives_each_case_equal_first_position_samples(self):
        rows = [{"case_id": f"case-{index}"} for index in range(21)]
        pairs = list(pair_schedule(rows, 3))
        self.assertEqual(len(pairs), 63)
        for row in rows:
            self.assertEqual(sum(first["case_id"] == row["case_id"] for first, _ in pairs), 3)
        for first, second in pairs:
            index = int(first["case_id"].split("-")[1])
            self.assertEqual(second["case_id"], rows[(index + 1) % 21]["case_id"])

    def test_synthetic_updates_are_private_fifo_messages_with_ticker_routing(self):
        rows = [{"case_id": f"case-{index}", "ticker": "IREN", "question": f"question {index}"}
                for index in range(21)]
        updates = synthetic_updates(rows, 1)
        self.assertEqual(len(updates), 42)
        self.assertEqual([item["update_id"] for item in updates], list(range(1, 43)))
        self.assertEqual(updates[0]["message"]["text"], "IREN: question 0")
        self.assertEqual(updates[1]["message"]["text"], "IREN: question 1")
        self.assertEqual(updates[0]["message"]["chat"], {"id": 1, "type": "private"})
        self.assertIs(updates[0]["message"]["from"]["is_bot"], False)


if __name__ == "__main__":
    unittest.main()
