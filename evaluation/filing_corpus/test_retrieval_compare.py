"""Retrieval contract counterexamples, never mock-vector quality measurements."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import hybrid_retrieval as hybrid
import retrieval_compare as compare
import rag_experiment as rag


def chunk(cid, text="Revenue 12", ticker="NBIS", **extra):
    return {"chunk_id": cid, "source_id": cid.split(":")[0], "ticker": ticker,
            "text": text, "block_start": 0, "block_end": 0, **extra}


def example():
    row = chunk("s:00001", "| Revenue | 12 |")
    header = chunk("s:00000", "Q3 2024 USD thousand")
    source = {"ticker": "NBIS", "accession": "filing-2024", "issuer_publication_date": None}
    alt = {"source_id": "s", "source_metadata": deepcopy(source), "spans": [
        {"chunk_id": row["chunk_id"], "quote": row["text"], "roles": ["row"]},
        {"chunk_id": header["chunk_id"], "quote": header["text"], "roles": ["period", "units"]}]}
    req = {"requirement_id": "revenue", "kind": "financial_fact", "review_status": "PASS",
           "critical": True, "alternatives": [alt]}
    case = {"case_id": "sample", "kind": "financial", "expected_source_ids": ["s"], "requirements": [req]}
    return case, [header, row], {"s": source}


def scored(cid, states, kind="financial"):
    return {"case_id": cid, "kind": kind, "packed": {
        "all_required": "PASS" if all(s == "PASS" for s in states) else "UNKNOWN" if "UNKNOWN" in states else "FAIL",
        "requirements": [{"requirement_id": str(i), "critical": True, "status": s} for i, s in enumerate(states)]}}


class EvidenceTests(unittest.TestCase):
    def test_reference_run_hash_is_checked_before_parity(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.json"
            data = {"cases": []}
            data["run_hash"] = compare.fingerprint(data)
            path.write_text(json.dumps(data))
            self.assertEqual(compare.load_reference_run(path), data)
            data["changed"] = True
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                compare.load_reference_run(path)

    def test_ranked_references_retain_locators_without_raw_contact_text(self):
        original = chunk("s:00001", "Contact: private@example.invalid", score=1.2, text_sha256="abc",
                         lexical_rank=2, lexical_protected=True)
        saved = compare.ranked_references([original])[0]
        self.assertEqual(saved["chunk_id"], original["chunk_id"])
        self.assertEqual(saved["lexical_rank"], 2)
        self.assertTrue(saved["lexical_protected"])
        self.assertNotIn("text", saved)
        self.assertNotIn("Contact", json.dumps(saved))

    def test_source_presence_cannot_substitute_for_relevant_row(self):
        case, passages, sources = example()
        result = compare.assess_evidence(case, [chunk("s:00002", "Unrelated source prose")], sources)
        self.assertEqual(result["all_required"], "FAIL")
        self.assertEqual(result["financial_rows"], "FAIL")

    def test_row_found_but_missing_or_wrong_period_header_is_incomplete(self):
        case, passages, sources = example()
        result = compare.assess_evidence(case, passages[1:], sources)
        self.assertEqual(result["financial_rows"], "PASS")
        self.assertEqual(result["financial_rows_headers"], "FAIL")
        self.assertEqual(result["all_required"], "FAIL")
        passages[0]["text"] = "Q3 2023 USD thousand"
        self.assertEqual(compare.assess_evidence(case, passages, sources)["financial_rows_headers"], "FAIL")

    def test_company_vintage_and_explicit_unknown_metadata_are_not_interchangeable(self):
        case, passages, sources = example()
        self.assertEqual(compare.assess_evidence(case, passages, sources)["all_required"], "PASS")
        for field, wrong in (("ticker", "IREN"), ("accession", "other-filing"), ("issuer_publication_date", "2024-11-14")):
            changed = deepcopy(sources)
            changed["s"][field] = wrong
            self.assertEqual(compare.assess_evidence(case, passages, changed)["all_required"], "FAIL")
        del sources["s"]["issuer_publication_date"]
        self.assertEqual(compare.assess_evidence(case, passages, sources)["all_required"], "FAIL")

    def test_complete_equivalent_source_is_credited_separately_from_literal_source_recall(self):
        case, passages, sources = example()
        alternative = deepcopy(case["requirements"][0]["alternatives"][0])
        alternative["source_id"] = "equivalent"
        alternative["source_metadata"]["accession"] = "equivalent-filing"
        for span in alternative["spans"]:
            span["chunk_id"] = span["chunk_id"].replace("s:", "equivalent:")
        case["requirements"][0]["alternatives"].append(alternative)
        equivalent = [dict(p, chunk_id=p["chunk_id"].replace("s:", "equivalent:"), source_id="equivalent") for p in passages]
        metadata = {"equivalent": alternative["source_metadata"]}
        manifest = {"documents": [{"source_id": "equivalent", **alternative["source_metadata"]}]}
        result = compare.score_case(case, {"ranked": equivalent, "pack": {"passages": equivalent, "sources": metadata}}, manifest)
        self.assertFalse(result["literal_sources"]["raw_any"])
        self.assertEqual(result["packed"]["all_required"], "PASS")

    def test_split_row_requires_every_fragment_and_exact_spacing(self):
        case, passages, sources = example()
        alt = case["requirements"][0]["alternatives"][0]
        alt["spans"][0]["quote"] = "Revenue"
        alt["spans"].append({"chunk_id": "s:00002", "quote": "12 BTC", "roles": ["row"]})
        self.assertEqual(compare.assess_evidence(case, passages, sources)["financial_rows"], "FAIL")
        passages.append(chunk("s:00002", "12  BTC"))
        self.assertEqual(compare.assess_evidence(case, passages, sources)["financial_rows"], "FAIL")
        passages[-1]["text"] = "12 BTC"
        self.assertEqual(compare.assess_evidence(case, passages, sources)["financial_rows"], "PASS")

    def test_contact_omission_can_move_offsets_without_losing_an_exact_span(self):
        case, passages, sources = example()
        for span in case["requirements"][0]["alternatives"][0]["spans"]:
            span.update(start=100, end=200)
        self.assertEqual(compare.assess_evidence(case, passages, sources)["all_required"], "PASS")

    def test_unknown_labels_cannot_count_as_covered(self):
        case, passages, sources = example()
        case["requirements"][0]["review_status"] = "UNKNOWN"
        result = compare.assess_evidence(case, passages, sources)
        self.assertEqual(result["all_required"], "UNKNOWN")
        self.assertEqual(result["financial_rows_headers"], "UNKNOWN")

    def test_abstention_scope_requires_source_presence_but_asserts_no_absence(self):
        case, passages, sources = example()
        case["requirements"][0].update(kind="scope_source")
        case["requirements"][0]["alternatives"][0]["spans"] = []
        result = compare.assess_evidence(case, [chunk("s:00002", "Some source text")], sources)
        self.assertEqual(result["all_required"], "PASS")
        self.assertEqual(result["financial_rows"], "NOT_APPLICABLE")
        self.assertEqual(compare.assess_evidence(case, [], sources)["all_required"], "FAIL")

    def test_gate_requires_real_new_completeness_and_rejects_partial_critical_losses(self):
        old = [scored("a", ["FAIL"]), scored("b", ["FAIL"]), scored("c", ["PASS", "FAIL"])]
        new = [scored("a", ["PASS"]), scored("b", ["PASS"]), scored("c", ["PASS", "FAIL"])]
        self.assertTrue(compare.improvement_gate(old, new)["meaningful_evidence_gain"])
        self.assertFalse(compare.improvement_gate(old, new)["paid_generation_authorized"])
        new[2] = scored("c", ["FAIL", "FAIL"])
        self.assertFalse(compare.improvement_gate(old, new)["meaningful_evidence_gain"])

    def test_gate_handles_sole_remaining_gap_unknown_and_scope_regression(self):
        old = [scored("a", ["FAIL"]), scored("b", ["PASS"]), scored("scope", ["PASS"], "abstention_scope")]
        new = [scored("a", ["PASS"]), scored("b", ["PASS"]), scored("scope", ["PASS"], "abstention_scope")]
        self.assertTrue(compare.improvement_gate(old, new)["meaningful_evidence_gain"])
        new[-1] = scored("scope", ["FAIL"], "abstention_scope")
        self.assertFalse(compare.improvement_gate(old, new)["meaningful_evidence_gain"])
        old[0] = scored("a", ["UNKNOWN"])
        self.assertTrue(compare.improvement_gate(old, new)["unknown"])

    def test_gate_explains_one_gain_when_two_baseline_gaps_remain(self):
        old = [scored("a", ["FAIL"]), scored("b", ["FAIL"])]
        new = [scored("a", ["PASS"]), scored("b", ["FAIL"])]
        gate = compare.improvement_gate(old, new)
        self.assertFalse(gate["meaningful_evidence_gain"])
        self.assertFalse(gate["paid_generation_authorized"])
        self.assertEqual(gate["reference"], "lexical_plus_table")
        self.assertEqual(gate["required_newly_complete"], 2)
        self.assertEqual(gate["failed_conditions"], ["insufficient_newly_complete"])
        self.assertIn("1 newly complete answerable cases versus 2 required", gate["next"])


class RankingTests(unittest.TestCase):
    def test_cli_rejects_packing_policy_for_actions_that_must_use_baseline(self):
        commands = (["baseline"], ["tune"], ["validate-labels"],
                    ["worker", "--arm", "lexical", "--queries", "unused.json"])
        for policy in set(rag.PACKING_VERSIONS) - {"baseline"}:
            for command in commands:
                with self.subTest(command=command, policy=policy), redirect_stderr(StringIO()), \
                        patch.object(compare.sys, "argv", ["retrieval_compare.py", *command,
                                                           "--packing-policy", policy]), \
                        self.assertRaises(SystemExit) as raised:
                    compare.main()
                self.assertEqual(raised.exception.code, 2)

    def test_cli_rejects_lexical_tail_outside_compare_and_hybrid_worker(self):
        commands = (["baseline", "--lexical-tail-depth", "15"],
                    ["worker", "--arm", "lexical", "--queries", "unused.json",
                     "--packing-policy", "context-anchored-tail-v1", "--lexical-tail-depth", "15"])
        for command in commands:
            with self.subTest(command=command), redirect_stderr(StringIO()), \
                    patch.object(compare.sys, "argv", ["retrieval_compare.py", *command]), \
                    self.assertRaises(SystemExit) as raised:
                compare.main()
            self.assertEqual(raised.exception.code, 2)

    def test_cli_rejects_embedding_windows_for_actions_that_ignore_them(self):
        commands = (["baseline"], ["tune"], ["validate-labels"],
                    ["worker", "--arm", "lexical", "--queries", "unused.json"])
        for command in commands:
            with self.subTest(command=command), redirect_stderr(StringIO()), \
                    patch.object(compare.sys, "argv", ["retrieval_compare.py", *command, "--passage-window-tokens", "128"]), \
                    self.assertRaises(SystemExit) as raised:
                compare.main()
            self.assertEqual(raised.exception.code, 2)

    def test_cli_rejects_lexical_floor_for_actions_that_ignore_it(self):
        for action in ("baseline", "tune", "validate-labels"):
            for options in (["--lexical-floor", "3"], ["--floor-policy", "query-coverage-v1"]):
                with self.subTest(action=action, options=options), redirect_stderr(StringIO()), \
                        patch.object(compare.sys, "argv", ["retrieval_compare.py", action, *options]), \
                        self.assertRaises(SystemExit) as raised:
                    compare.main()
                self.assertEqual(raised.exception.code, 2)

    def test_cli_rejects_conditional_policy_without_floor_and_on_lexical_worker(self):
        commands = (["compare", "--floor-policy", "query-coverage-v1"],
                    ["worker", "--arm", "lexical", "--queries", "unused.json", "--lexical-floor", "3"])
        for command in commands:
            with self.subTest(command=command), redirect_stderr(StringIO()), \
                    patch.object(compare.sys, "argv", ["retrieval_compare.py", *command]), \
                    self.assertRaises(SystemExit) as raised:
                compare.main()
            self.assertEqual(raised.exception.code, 2)

    def test_query_result_passes_lexical_floor_only_to_hybrid_search(self):
        originals = [chunk(f"s:{i:05d}", text_sha256=f"source-hash-{i}") for i in range(1, 16)]
        record = {**originals[0], "score": 1.0, "lexical_rank": 1, "lexical_protected": True}
        class HybridIndexStub:
            def __init__(self):
                self.options = None
                self.lexical = SimpleNamespace(search=lambda ticker, question, limit: originals[:limit])
            def search_with_diagnostics(self, ticker, question, limit, **options):
                self.options = options
                return {"ranked": [record], "lexical_floor_decision": {"effective_floor": 1}}

        index = HybridIndexStub()
        query = {"case_id": "test", "ticker": "NBIS", "question": "revenue"}
        for policy in set(rag.PACKING_VERSIONS) - {"baseline"}:
            tail_depth = 15 if policy == "context-anchored-tail-v1" else 10
            expected_tail = originals[10:] if tail_depth == 15 else []
            with self.subTest(policy=policy), \
                    patch.object(compare, "build_evidence", return_value={"passages": []}) as pack_evidence, \
                    patch.object(compare, "generation_input", return_value={}):
                result = compare.query_result(index, {}, originals, query, weights=(0.75, 0.25),
                                              lexical_floor=3, floor_policy="query-coverage-v1",
                                              packing_policy=policy, lexical_tail_depth=tail_depth)
                self.assertEqual(index.options, {"weights": (0.75, 0.25), "lexical_floor": 3,
                                                 "floor_policy": "query-coverage-v1"})
                self.assertEqual(result["lexical_floor_decision"], {"effective_floor": 1})
                expected_kwargs = {"packing_policy": policy}
                if tail_depth == 15:
                    expected_kwargs["lexical_tail"] = expected_tail
                self.assertEqual(pack_evidence.call_args.kwargs, expected_kwargs)

    def test_lexical_tail_depth_requires_its_hybrid_packing_policy(self):
        query = {"case_id": "test", "ticker": "NBIS", "question": "revenue"}
        with self.assertRaisesRegex(ValueError, "Depth 15"):
            compare.query_result(SimpleNamespace(), {}, [], query, lexical_tail_depth=15)

    def test_query_result_keeps_floor_diagnostics_when_no_passages_are_returned(self):
        class EmptyIndex:
            def search_with_diagnostics(self, *args, **options):
                return {"ranked": [], "lexical_floor_decision": {"effective_floor": 0, "reason": "no_lexical_hits"}}
        with patch.object(compare, "build_evidence", return_value={"passages": []}), \
                patch.object(compare, "generation_input", return_value={}):
            result = compare.query_result(EmptyIndex(), {}, [],
                                          {"case_id": "empty", "ticker": "NBIS", "question": "cash reserves"},
                                          weights=(0.75, 0.25), lexical_floor=3, floor_policy="query-coverage-v1")
        self.assertEqual(result["lexical_floor_decision"]["reason"], "no_lexical_hits")
        self.assertEqual(result["ranked"], [])

    def test_retriever_cannot_change_source_text_company_or_duplicate_ids(self):
        original = chunk("s:00001", text_sha256="source-hash")
        for result in ([dict(original, text="invented row")], [dict(original, ticker="IREN")], [original, original]):
            class BrokenIndex:
                def search(self, ticker, question, limit):
                    return result
            with self.assertRaisesRegex(ValueError, "Retriever changed"):
                compare.query_result(BrokenIndex(), {}, [original], {"case_id": "test", "ticker": "NBIS", "question": "revenue"})

    def test_token_windows_cover_tail_without_gaps_or_silent_truncation(self):
        self.assertEqual(hybrid.token_windows(255, 254), [(0, 254), (190, 255)])
        windows = hybrid.token_windows(1000, 254)
        self.assertEqual(set().union(*(set(range(a, b)) for a, b in windows)), set(range(1000)))
        for args in ((0, 254), (1, 64), (1, 254, 254)):
            with self.assertRaises(ValueError):
                hybrid.token_windows(*args)

    def test_fusion_is_equal_rank_based_deterministic_and_deduplicated(self):
        a, b, c = [chunk("s:" + n) for n in ("00001", "00002", "00003")]
        result = hybrid.fuse([a, b], [c, b])
        self.assertEqual([p["chunk_id"] for p in result], [b["chunk_id"], a["chunk_id"], c["chunk_id"]])
        self.assertEqual(result[0]["lexical_rank"], 2)
        self.assertEqual(result[0]["dense_rank"], 2)
        with self.assertRaises(ValueError):
            hybrid.fuse([a, a], [])
        with self.assertRaises(ValueError):
            hybrid.fuse([a], [dict(a, text="changed")])

    def test_weighted_fusion_uses_normalized_one_based_reciprocal_ranks(self):
        a, b, c = [chunk("s:" + n) for n in ("00001", "00002", "00003")]
        default = hybrid.fuse([a, b], [c, b])
        explicit_equal = hybrid.fuse([a, b], [c, b], lexical_weight=0.5, dense_weight=0.5)
        self.assertEqual(default, explicit_equal)

        result = hybrid.fuse([a, b], [c, b], lexical_weight=0.75, dense_weight=0.25)
        by_id = {item["chunk_id"]: item for item in result}
        self.assertAlmostEqual(by_id[a["chunk_id"]]["score"], 0.75 / 61)
        self.assertAlmostEqual(by_id[b["chunk_id"]]["score"], 1 / 62)
        self.assertAlmostEqual(by_id[c["chunk_id"]]["score"], 0.25 / 61)

    def test_weighted_fusion_missing_arm_contributes_zero_and_ties_are_stable(self):
        lexical = chunk("s:00001")
        dense = chunk("s:00002")
        first = hybrid.fuse([lexical], [dense], lexical_weight=0.75, dense_weight=0.25)
        second = hybrid.fuse([lexical], [dense], lexical_weight=0.75, dense_weight=0.25)
        self.assertEqual([row["chunk_id"] for row in first], [row["chunk_id"] for row in second])
        self.assertEqual(first[0]["chunk_id"], lexical["chunk_id"])
        self.assertAlmostEqual(first[0]["score"], 0.75 / 61)
        self.assertAlmostEqual(first[1]["score"], 0.25 / 61)

    def test_fusion_rejects_invalid_weights(self):
        a = chunk("s:00001")
        for lexical_weight, dense_weight in ((0.6, 0.3), (-0.1, 1.1), (float("nan"), 0), (True, 0)):
            with self.subTest(weights=(lexical_weight, dense_weight)):
                with self.assertRaises(ValueError):
                    hybrid.fuse([a], [], lexical_weight=lexical_weight, dense_weight=dense_weight)

    def test_lexical_floor_keeps_top_lexical_hits_and_fuses_remaining_slots(self):
        lexical = [chunk("s:" + n) for n in ("00001", "00002", "00003", "00004")]
        dense = [chunk("s:00005"), lexical[3]]
        full = hybrid.fuse(lexical, dense, lexical_weight=0.25, dense_weight=0.75)
        protected = hybrid.fuse(lexical, dense, lexical_weight=0.25, dense_weight=0.75, lexical_floor=3)
        self.assertEqual([p["chunk_id"] for p in protected[:3]], [p["chunk_id"] for p in lexical[:3]])
        self.assertEqual([p["chunk_id"] for p in protected[3:]],
                         [p["chunk_id"] for p in full if p["chunk_id"] not in {x["chunk_id"] for x in lexical[:3]}][:7])
        self.assertTrue(all(p.get("lexical_protected") for p in protected[:3]))
        self.assertFalse(any(p.get("lexical_protected") for p in protected[3:]))

    def test_lexical_floor_rejects_invalid_values(self):
        a = chunk("s:00001")
        for value in (True, -1, 11, 1.0):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    hybrid.fuse([a], [], lexical_floor=value)


    def test_no_network_guard_fails_before_any_connection(self):
        with hybrid.no_network():
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                socket.create_connection(("example.invalid", 443))

    def test_cache_identity_changes_with_order_text_company_and_encoder_settings(self):
        a, b = chunk("s:00001"), chunk("s:00002")
        baseline = hybrid.cache_identity([a, b], {"revision": "one"})
        for chunks, config in (([b, a], {"revision": "one"}), ([dict(a, text="changed"), b], {"revision": "one"}),
                               ([dict(a, ticker="IREN"), b], {"revision": "one"}), ([a, b], {"revision": "two"})):
            self.assertNotEqual(baseline, hybrid.cache_identity(chunks, config))


class PackingPolicyTests(unittest.TestCase):
    @staticmethod
    def fixture():
        chunks = [chunk(f"s:{i:05d}", f"Passage {i}. " + chr(65 + i) * 550,
                        block_start=i, block_end=i) for i in range(8)]
        manifest = {"corpus_version": "fixture-v1", "chunks_sha256": "fixture-hash",
                    "documents": [{"source_id": "s", "ticker": "NBIS"}]}
        ranked = [chunks[i] for i in (0, 2, 4, 6)]
        return manifest, chunks, ranked

    def test_context_priority_retains_a_neighbor_under_the_same_budget_and_raw_ranking(self):
        manifest, chunks, ranked = self.fixture()
        with patch.object(rag, "MAX_EVIDENCE_BYTES", 4000):
            baseline = rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain the service")
            candidate = rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain the service",
                                           packing_policy="context-first-v1")
            base_ids = {p["chunk_id"] for p in baseline["passages"]}
            candidate_ids = {p["chunk_id"] for p in candidate["passages"]}
            self.assertIn("s:00006", base_ids)
            self.assertNotIn("s:00001", base_ids)
            self.assertIn("s:00001", candidate_ids)
            self.assertNotIn("s:00006", candidate_ids)
            self.assertIn("s:00006", candidate["omitted_chunk_ids"])
            self.assertEqual(baseline["raw_retrieved_chunk_ids"], candidate["raw_retrieved_chunk_ids"])
            self.assertEqual(candidate["version"], rag.PACKING_VERSIONS["context-first-v1"])
            originals = {c["chunk_id"]: c for c in chunks}
            for pack in (baseline, candidate):
                rag.verify_evidence(pack)
                self.assertLessEqual(len(compare.canonical(pack)), 4000)
                for passage in pack["passages"]:
                    self.assertEqual(passage["text"], originals[passage["chunk_id"]]["text"])
                    self.assertEqual(passage["source_id"], "s")

    def test_anchored_context_attempts_late_raw_hit_before_its_own_neighbors(self):
        manifest, _, _ = self.fixture()
        chunks = [chunk(f"s:{i:05d}", f"Passage {i}. " + "X" * 550,
                        block_start=i, block_end=i) for i in (0, 10, 20, 29, 30, 31)]
        by_id = {c["chunk_id"]: c for c in chunks}
        ranked = [by_id[f"s:{i:05d}"] for i in (0, 10, 20, 30)]
        with patch.object(rag, "MAX_EVIDENCE_BYTES", 4000):
            old = rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain service",
                                     packing_policy="context-first-v1")
            new = rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain service",
                                     packing_policy="context-anchored-v1")
            old_ids, new_ids = ({p["chunk_id"] for p in pack["passages"]} for pack in (old, new))
            self.assertIn("s:00029", old_ids)
            self.assertNotIn("s:00030", old_ids)
            self.assertIn("s:00030", new_ids)
            self.assertNotIn("s:00029", new_ids)
            self.assertEqual(old["raw_retrieved_chunk_ids"], new["raw_retrieved_chunk_ids"])
            for pack in (old, new):
                rag.verify_evidence(pack)
                self.assertLessEqual(len(compare.canonical(pack)), 4000)
                for passage in pack["passages"]:
                    self.assertEqual(passage["text"], by_id[passage["chunk_id"]]["text"])

    def test_anchored_order_preserves_prefix_candidate_pool_and_source_local_neighbors(self):
        ranked = [chunk(f"s:{i:05d}") for i in (0, 10, 20, 30)] + [chunk("t:00030", source_id="t")]
        prefix = ["s:00000", "t:00002", "t:00003"]
        # Headers retain priority within their owner's neighbors, even when their
        # global header-first order puts a later owner's neighbor first.
        neighbors = ["t:00031", "s:00031", "s:00001", "s:00029"]
        baseline = rag.evidence_candidate_order(prefix, ranked, neighbors, "baseline")
        anchored = rag.evidence_candidate_order(prefix, ranked, neighbors, "context-anchored-v1")
        self.assertEqual(anchored, prefix + ["s:00010", "s:00020", "s:00001",
                                             "s:00030", "s:00031", "s:00029", "t:00030", "t:00031"])
        self.assertEqual(set(baseline), set(anchored))
        # Another source with a matching ordinal cannot become this hit's neighbor.
        self.assertLess(anchored.index("t:00030"), anchored.index("t:00031"))

    def test_lexical_tail_follows_six_anchored_context_groups_and_precedes_later_hits(self):
        ranked = [chunk(f"s:{i:05d}") for i in range(0, 100, 10)]
        neighbors = [f"s:{i:05d}" for i in (1, 9, 11, 19, 21, 29, 31, 39, 41, 49, 51,
                                                    59, 61, 69, 71, 79, 81, 89, 91)]
        tail = [f"t:{i:05d}" for i in range(11, 16)]
        order = rag.evidence_candidate_order(["s:00000"], ranked, neighbors,
                                             "context-anchored-tail-v1", tail)
        self.assertEqual(order[:3], ["s:00000", "s:00010", "s:00020"])
        self.assertLess(order.index("s:00051"), order.index(tail[0]))
        self.assertLess(order.index(tail[-1]), order.index("s:00060"))
        self.assertEqual(set(order), set(neighbors) | {c["chunk_id"] for c in ranked} | set(tail))

    def test_lexical_tail_packer_keeps_top_ten_raw_metrics_and_original_text(self):
        manifest, chunks, ranked = self.fixture()
        tail = chunk("s:00010", "Later lexical candidate", block_start=10, block_end=10)
        chunks.append(tail)
        pack = rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain service",
                                  packing_policy="context-anchored-tail-v1", lexical_tail=[tail])
        self.assertEqual(pack["raw_retrieved_chunk_ids"], [c["chunk_id"] for c in ranked])
        self.assertEqual(pack["lexical_tail_candidate_ids"], [tail["chunk_id"]])
        self.assertIn(tail["chunk_id"], {p["chunk_id"] for p in pack["passages"]})
        self.assertEqual(next(p["text"] for p in pack["passages"] if p["chunk_id"] == tail["chunk_id"]),
                         tail["text"])
        rag.verify_evidence(pack)

    def test_lexical_tail_rejects_wrong_ticker_duplicates_and_other_pack_modes(self):
        manifest, chunks, ranked = self.fixture()
        tail = chunk("s:00010", "Later lexical candidate", block_start=10, block_end=10)
        chunks.append(tail)
        with self.assertRaisesRegex(ValueError, "identity"):
            rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain service",
                               packing_policy="context-anchored-tail-v1", lexical_tail=[{**tail, "ticker": "IREN"}])
        with self.assertRaisesRegex(ValueError, "identity"):
            rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain service",
                               packing_policy="context-anchored-tail-v1", lexical_tail=[tail, tail])
        with self.assertRaisesRegex(ValueError, "identity"):
            rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain service",
                               packing_policy="context-anchored-tail-v1", lexical_tail=[chunk("s:00011")])
        with self.assertRaisesRegex(ValueError, "require the lexical-tail"):
            rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain service",
                               packing_policy="context-anchored-v1", lexical_tail=[tail])

    def test_all_packing_policies_keep_the_candidate_pool_when_budget_allows(self):
        manifest, chunks, ranked = self.fixture()
        for policy in rag.PACKING_VERSIONS:
            with self.subTest(policy=policy):
                pack = rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain service",
                                          packing_policy=policy)
                self.assertEqual({p["chunk_id"] for p in pack["passages"]},
                                 {c["chunk_id"] for c in chunks})
                self.assertEqual(pack["omitted_chunk_ids"], [])

    def test_table_header_row_groups_remain_atomic_under_all_policies(self):
        manifest, chunks, ranked = self.fixture()
        header = chunk("s:00008", "Q3 2030 in USD thousands " + "H" * 500)
        row = chunk("s:00009", "| Revenue | 123 | " + "R" * 500)
        chunks.extend([header, row])
        for policy in rag.PACKING_VERSIONS:
            for budget in (1900, 2900, 4500):
                with self.subTest(policy=policy, budget=budget), \
                        patch.object(rag, "table_support", return_value=[row["chunk_id"]]), \
                        patch.object(rag, "table_context_support", return_value=[header["chunk_id"]]), \
                        patch.object(rag, "MAX_EVIDENCE_BYTES", budget):
                    pack = rag.build_evidence(manifest, chunks, ranked, "NBIS", "Q3 revenue", packing_policy=policy)
                    ids = {p["chunk_id"] for p in pack["passages"]}
                    self.assertEqual(header["chunk_id"] in ids, row["chunk_id"] in ids)
                    rag.verify_evidence(pack)
                    if row["chunk_id"] not in ids:
                        self.assertTrue({header["chunk_id"], row["chunk_id"]} <= set(pack["omitted_chunk_ids"]))

    def test_context_candidates_cannot_borrow_another_company_or_accept_an_unknown_policy(self):
        manifest, chunks, ranked = self.fixture()
        chunks[1] = {**chunks[1], "ticker": "IREN"}
        for policy in rag.PACKING_VERSIONS:
            with self.subTest(policy=policy):
                pack = rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain service",
                                          packing_policy=policy)
                self.assertNotIn("s:00001", {p["chunk_id"] for p in pack["passages"]})
        with self.assertRaisesRegex(ValueError, "Unknown evidence packing"):
            rag.build_evidence(manifest, chunks, ranked, "NBIS", "Explain service", packing_policy="unknown")


class EmbeddingWindowTests(unittest.TestCase):
    def test_passage_window_validation_rejects_overlap_capacity_and_invalid_types(self):
        self.assertEqual(hybrid.validate_passage_window_tokens(128), 128)
        self.assertEqual(hybrid.validate_passage_window_tokens(254), 254)
        for value in (0, 64, 255, True, 128.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                hybrid.validate_passage_window_tokens(value)

    def test_shorter_passage_views_cover_all_tokens_without_shortening_queries_or_corpus(self):
        encoder = object.__new__(hybrid.LocalEncoder)
        encoder.content_limit = 254
        encoder.passage_window_tokens = 128
        encoder.tokens = lambda text: list(range(300 if text == "passage" else 200))
        encoder.encode_ids = lambda windows: windows
        chunks = [chunk("s:1", "passage")]
        original = deepcopy(chunks)
        windows, layout = encoder.encode_passages(chunks)
        self.assertEqual([len(w) for w in windows], [128, 128, 128, 108])
        self.assertEqual(set().union(*(set(range(w["start"], w["end"])) for w in layout)), set(range(300)))
        self.assertEqual(encoder.encode_query("query"), list(range(200)))
        self.assertEqual(chunks, original)
        encoder.passage_window_tokens = 254
        full_windows, _ = encoder.encode_passages(chunks)
        self.assertEqual([len(w) for w in full_windows], [254, 110])

    def test_worker_forwards_and_records_the_passage_window_setting(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            queries = directory / "queries.json"
            queries.write_text(json.dumps([{"case_id": str(i), "ticker": "NBIS", "question": "revenue"}
                                          for i in range(21)]))
            args = SimpleNamespace(arm="hybrid", release=directory, queries=queries,
                                   experiment_dir=directory, passage_window_tokens=128)
            encoder = SimpleNamespace(passage_window_tokens=128, identity={})
            index = SimpleNamespace(timing_ms={"passage_encoding_ms": 0}, cache_hit=True,
                                    cache_key="fixture", cache_path=directory, layout=[{}, {}])
            output = StringIO()
            with patch.object(compare, "load_release", return_value=({}, [])), \
                    patch.object(compare, "LocalEncoder", return_value=encoder) as load_encoder, \
                    patch.object(compare, "HybridIndex", return_value=index), \
                    patch.object(compare.sys, "stdin", ['{"action":"stop"}\n']), redirect_stdout(output):
                compare.worker(args)
            load_encoder.assert_called_once_with(directory / "model", directory / "model-artifacts.json",
                                                  compare.DEPENDENCIES, passage_window_tokens=128)
            ready = json.loads(output.getvalue())
            self.assertEqual(ready["status"], "ready")
            self.assertEqual(ready["setup"]["passage_window_tokens"], 128)
            self.assertEqual(ready["setup"]["window_count"], 2)


class FloorPolicyTests(unittest.TestCase):
    def test_conditional_floor_checks_each_prefix_hit_and_counts_distinct_query_terms(self):
        lexical = [chunk("s:1", "revenue"), chunk("s:2", "cash cost"),
                   chunk("s:3", "assets"), chunk("s:4", "revenue cash cost assets")]
        decision = hybrid.lexical_floor_decision(lexical, "revenue cash cost assets assets", 3,
                                                 "query-coverage-v1")
        self.assertEqual(decision["query_term_count"], 4)
        self.assertEqual(decision["protected_chunk_ids"], ["s:2"])
        self.assertEqual(decision["effective_floor"], 1)
        self.assertEqual([c["query_term_coverage"] for c in decision["candidates"]], [0.25, 0.5, 0.25])
        self.assertEqual(decision["reason"], "some_candidates_meet_coverage")
        self.assertNotIn("s:4", [c["chunk_id"] for c in decision["candidates"]])

    def test_weak_hits_lose_protection_but_stay_in_the_fusion_candidate_pool(self):
        lexical = [chunk("s:1", "revenue"), chunk("s:2", "cash cost"), chunk("s:3", "assets")]
        dense = [chunk("s:4", "liquid reserves"), lexical[2]]
        decision = hybrid.lexical_floor_decision(lexical, "revenue cash cost assets", 3, "query-coverage-v1")
        fused = hybrid.fuse(lexical, dense, lexical_weight=0.25, dense_weight=0.75)
        result = hybrid.fuse(lexical, dense, lexical_weight=0.25, dense_weight=0.75,
                             lexical_floor=3, protected_chunk_ids=decision["protected_chunk_ids"])
        self.assertEqual(result[0]["chunk_id"], "s:2")
        self.assertEqual([r["chunk_id"] for r in result[1:]],
                         [r["chunk_id"] for r in fused if r["chunk_id"] != "s:2"])
        self.assertTrue(result[0]["lexical_protected"])
        self.assertFalse(any(r.get("lexical_protected") for r in result[1:]))

    def test_empty_lexical_arm_reserves_no_slots_and_uses_dense_order(self):
        dense = [chunk("s:2"), chunk("s:1")]
        decision = hybrid.lexical_floor_decision([], "liquid reserves", 3, "query-coverage-v1")
        result = hybrid.fuse([], dense, lexical_weight=0.75, dense_weight=0.25,
                             lexical_floor=3, protected_chunk_ids=decision["protected_chunk_ids"])
        self.assertEqual([r["chunk_id"] for r in result], ["s:2", "s:1"])
        self.assertEqual(decision["reason"], "no_lexical_hits")
        self.assertEqual(decision["effective_floor"], 0)

    def test_short_stopword_only_and_low_overlap_queries_disable_protection(self):
        lexical = [chunk("s:1", "cash")]
        for question, reason in (("cash cash", "insufficient_query_terms"),
                                 ("what is it", "insufficient_query_terms"),
                                 ("cash revenue cost assets", "no_candidate_meets_coverage")):
            with self.subTest(question=question):
                decision = hybrid.lexical_floor_decision(lexical, question, 3, "query-coverage-v1")
                self.assertEqual(decision["effective_floor"], 0)
                self.assertEqual(decision["reason"], reason)

    def test_fixed_floor_and_disabled_floor_preserve_existing_fusion(self):
        lexical = [chunk("s:1", "unrelated"), chunk("s:2", "text")]
        fixed = hybrid.lexical_floor_decision(lexical, "cash revenue", 3, "fixed")
        self.assertEqual(fixed["protected_chunk_ids"], ["s:1", "s:2"])
        self.assertEqual(fixed["effective_floor"], 2)
        disabled = hybrid.lexical_floor_decision(lexical, "cash revenue", 0, "fixed")
        self.assertEqual(disabled["reason"], "disabled")
        self.assertEqual(hybrid.fuse(lexical, []),
                         hybrid.fuse(lexical, [], lexical_floor=0, protected_chunk_ids=[]))

    def test_invalid_policy_and_protected_subset_are_rejected(self):
        lexical = [chunk("s:1"), chunk("s:2"), chunk("s:3")]
        for policy, floor in (("unknown", 3), ("query-coverage-v1", 0)):
            with self.subTest(policy=policy, floor=floor), self.assertRaises(ValueError):
                hybrid.lexical_floor_decision(lexical, "cash revenue", floor, policy)
        for ids in (["s:3"], ["s:2", "s:1"], ["s:1", "s:1"], ["missing"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                hybrid.fuse(lexical, [], lexical_floor=2, protected_chunk_ids=ids)


class TuningTests(unittest.TestCase):
    @staticmethod
    def candidate(run_id, lexical_weight, complete, row_headers, p95, passes=True):
        return {"run_id": run_id, "weights": [lexical_weight, 1 - lexical_weight], "report": {
            "generation_gate": {"meaningful_evidence_gain": passes},
            "summary": {"hybrid": {"packed": {
                "answerable_complete": {"count": complete},
                "financial_rows_headers": {"count": row_headers}}}},
            "arms": {"hybrid": {"performance": {"p95_query_ms": p95}}}}}

    def test_selection_only_considers_gate_passing_settings(self):
        candidates = [self.candidate("fast-but-fails", 0.75, 19, 14, 1, passes=False),
                      self.candidate("slower-more-evidence", 0.50, 18, 14, 20),
                      self.candidate("fast-less-evidence", 0.25, 17, 14, 2)]
        self.assertEqual(compare.select_tuning_candidate(candidates), "slower-more-evidence")
        self.assertIsNone(compare.select_tuning_candidate([candidates[0]]))

    def test_selection_uses_p95_then_lexical_weight_for_evidence_ties(self):
        candidates = [self.candidate("slow", 0.75, 18, 14, 20),
                      self.candidate("fast-lexical", 0.75, 18, 14, 2),
                      self.candidate("fast-dense", 0.25, 18, 14, 2)]
        self.assertEqual(compare.select_tuning_candidate(candidates), "fast-lexical")

    def test_tuning_weights_and_secondary_probe_count_are_fixed(self):
        self.assertEqual(compare.TUNING_WEIGHTS, ((0.75, 0.25), (0.50, 0.50), (0.25, 0.75)))
        self.assertEqual(len(compare.PARAPHRASE_PROBES), 3)
        self.assertTrue(all(probe["case_id"].startswith("probe-") for probe in compare.PARAPHRASE_PROBES))


try:
    import numpy as np
except ImportError:
    np = None


class FixtureEncoder:
    """Synthetic unit vectors test mechanics only; never used for report runs."""
    content_limit = 128
    identity = {"fixture": True, "version": 1}

    def __init__(self, passage_window_tokens=None):
        self.calls = 0
        self.passage_window_tokens = hybrid.validate_passage_window_tokens(
            self.content_limit if passage_window_tokens is None else passage_window_tokens, self.content_limit)
        self.identity = {**self.identity, "passage_window_tokens": self.passage_window_tokens}

    def encode_passages(self, chunks):
        self.calls += 1
        values, layout = [], []
        for i, c in enumerate(chunks):
            n = c.get("test_tokens", 3)
            for start, end in hybrid.token_windows(n, self.passage_window_tokens):
                v = np.zeros(384, dtype="float32")
                v[0] = c.get("similarity", 1)
                v[1] = (1 - float(v[0]) ** 2) ** 0.5
                values.append(v)
                layout.append({"chunk_index": i, "start": start, "end": end, "token_count": n})
        return np.array(values), layout

    def encode_query(self, question):
        v = np.zeros(384, dtype="float32")
        v[0] = 1
        return v


@unittest.skipIf(np is None, "Isolated embedding dependencies not installed")
class CacheTests(unittest.TestCase):
    def test_window_sizes_get_separate_validated_caches_and_preserve_passage_identity(self):
        class WideFixtureEncoder(FixtureEncoder):
            content_limit = 254
        chunks = [chunk("s:1", test_tokens=300)]
        with tempfile.TemporaryDirectory() as d:
            full = hybrid.HybridIndex(chunks, WideFixtureEncoder(254), Path(d))
            focused_encoder = WideFixtureEncoder(128)
            focused = hybrid.HybridIndex(chunks, focused_encoder, Path(d))
            replay = hybrid.HybridIndex(chunks, focused_encoder, Path(d))
            self.assertNotEqual(full.cache_key, focused.cache_key)
            self.assertEqual(len(full.layout), 2)
            self.assertEqual(len(focused.layout), 4)
            self.assertTrue(replay.cache_hit)
            self.assertEqual(focused_encoder.calls, 1)
            self.assertEqual(focused.chunks, full.chunks)
            with self.assertRaisesRegex(ValueError, "Windows omit"):
                hybrid.validate_vectors(focused.vectors, focused.layout, 1, 254)

    def test_conditional_search_uses_actual_word_overlap_and_keeps_empty_query_diagnostics(self):
        a, b = chunk("s:1", "cash"), chunk("s:2", "cash cost")
        with tempfile.TemporaryDirectory() as d:
            index = hybrid.HybridIndex([a, b], FixtureEncoder(), Path(d))
            result = index.search_with_diagnostics("NBIS", "cash revenue cost assets", weights=(0.75, 0.25),
                                                    lexical_floor=3, floor_policy="query-coverage-v1")
            self.assertEqual(result["lexical_floor_decision"]["protected_chunk_ids"], ["s:2"])
            self.assertEqual(result["ranked"][0]["chunk_id"], "s:2")
            with patch.object(Path, "read_text", side_effect=AssertionError("Search read a file")), \
                    patch.object(Path, "read_bytes", side_effect=AssertionError("Search read a file")):
                empty = index.search_with_diagnostics("NBIS", " ", lexical_floor=3,
                                                       floor_policy="query-coverage-v1")
                unknown = index.search_with_diagnostics("UNKNOWN", "cash reserves", lexical_floor=3,
                                                         floor_policy="query-coverage-v1")
            self.assertEqual(empty["ranked"], [])
            self.assertEqual(empty["lexical_floor_decision"]["effective_floor"], 0)
            self.assertEqual(unknown["lexical_floor_decision"]["reason"], "no_lexical_hits")

    def test_hybrid_search_passes_requested_weights_to_fusion(self):
        a = chunk("s:00001", "Revenue 12")
        b = chunk("s:00002", "Annual revenue 12")
        with tempfile.TemporaryDirectory() as d:
            index = hybrid.HybridIndex([a, b], FixtureEncoder(), Path(d))
            with patch.object(index.lexical, "search", return_value=[a]), \
                    patch.object(index, "dense_search", return_value=[b]):
                result = index.search("NBIS", "revenue", weights=(0.75, 0.25), lexical_floor=1)
        self.assertEqual(result[0]["chunk_id"], a["chunk_id"])
        self.assertAlmostEqual(result[0]["score"], 0.75 / 61)
        self.assertTrue(result[0]["lexical_protected"])

    def test_search_has_no_gold_or_corpus_file_reads(self):
        with tempfile.TemporaryDirectory() as d:
            index = hybrid.HybridIndex([chunk("s:00001")], FixtureEncoder(), Path(d))
            with patch.object(Path, "read_text", side_effect=AssertionError("Search read a benchmark/label/file")), \
                    patch.object(Path, "read_bytes", side_effect=AssertionError("Search read a benchmark/label/file")):
                self.assertEqual(index.search("NBIS", "revenue")[0]["chunk_id"], "s:00001")
                self.assertEqual(index.search("NBIS", "revenue cash", lexical_floor=3,
                                              floor_policy="query-coverage-v1")[0]["chunk_id"], "s:00001")

    def test_ticker_filter_window_max_and_original_id_dedup(self):
        chunks = [chunk("s:00001", similarity=0.4, test_tokens=300), chunk("s:00002", similarity=0.8),
                  chunk("other:00001", ticker="IREN", similarity=1)]
        with tempfile.TemporaryDirectory() as d:
            index = hybrid.HybridIndex(chunks, FixtureEncoder(), Path(d))
            dense = index.dense_search("NBIS", "query")
            self.assertEqual([c["chunk_id"] for c in dense], ["s:00002", "s:00001"])
            self.assertEqual(index.search("UNKNOWN", "query"), [])
            self.assertEqual(index.search("NBIS", " "), [])

    def test_cache_replay_and_corruption_fail_closed(self):
        chunks = [chunk("s:00001")]
        with tempfile.TemporaryDirectory() as d:
            encoder = FixtureEncoder()
            index = hybrid.HybridIndex(chunks, encoder, Path(d))
            again = hybrid.HybridIndex(chunks, encoder, Path(d))
            self.assertEqual(encoder.calls, 1)
            self.assertTrue(again.cache_hit)
            path = index.cache_path / "vectors.npy"
            path.write_bytes(path.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum"):
                hybrid.HybridIndex(chunks, encoder, Path(d))

    def test_vectors_reject_nan_wrong_dtype_missing_tokens_and_wrong_mapping(self):
        encoder = FixtureEncoder()
        values, layout = encoder.encode_passages([chunk("s:00001", test_tokens=200)])
        hybrid.validate_vectors(values, layout, 1, encoder.content_limit)
        for v, l in ((values.astype("float64"), layout), (values * 0, layout), (values + np.nan, layout),
                     (values[:-1], layout[:-1]), (values, [dict(item, chunk_index=2) for item in layout])):
            with self.assertRaises(ValueError):
                hybrid.validate_vectors(v, l, 1, encoder.content_limit)


class FrozenSourceTests(unittest.TestCase):
    def test_default_lexical_packs_still_match_every_saved_v14_development_case(self):
        release = compare.LOCAL / "releases/sec-qa-pilot-v1"
        if not release.exists() or not compare.BASELINE_RUN.exists():
            self.skipTest("Fixed corpus/reference run missing; baseline parity UNKNOWN")
        manifest, chunks = compare.load_release(release)
        reference = compare.load_reference_run(compare.BASELINE_RUN)
        index = compare.LexicalIndex(chunks)
        for saved in reference["cases"]:
            evidence = saved["evidence"]
            ranked = index.search(evidence["ticker"], evidence["question"], 10)
            current = rag.build_evidence(manifest, chunks, ranked, evidence["ticker"], evidence["question"])
            with self.subTest(case_id=saved["case_id"]):
                self.assertEqual(current, evidence)

    def test_source_reviewed_labels_match_fixed_development_sources(self):
        release = compare.LOCAL / "releases/sec-qa-pilot-v1"
        if not release.exists():
            self.skipTest("Fixed local corpus missing; source integration UNKNOWN")
        manifest, chunks = compare.load_release(release)
        rows = [r for r in map(json.loads, compare.BENCHMARK.read_text().splitlines()) if r["split"] == "development"]
        labels = compare.load_labels(compare.LABELS, manifest, chunks, rows)
        self.assertEqual(len(labels["cases"]), 21)
        self.assertEqual(sum(len(c["requirements"]) for c in labels["cases"]), 39)
        changed = deepcopy(labels)
        changed["cases"][0]["requirements"][0]["alternatives"][0]["spans"][0]["quote"] = "invented"
        changed["annotation_hash"] = compare.fingerprint({k: v for k, v in changed.items() if k != "annotation_hash"})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "labels.json"
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "not exact"):
                compare.load_labels(path, manifest, chunks, rows)


if __name__ == "__main__":
    unittest.main()
