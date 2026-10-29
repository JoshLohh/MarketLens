"""Offline contracts for the paired lexical/hybrid answer comparison."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import qa_retrieval_answer_compare as paired
from qa_contract import answer_record, fingerprint


def rehash(value: dict) -> dict:
    value["run_hash"] = fingerprint({key: item for key, item in value.items() if key != "run_hash"})
    return value


HISTORICAL_V2_CODE = (
    paired.LOCAL / "answer_pair_reviews" / "paired-results-2026-10-25-v2" / "code"
)


def load_historical_pair_inputs() -> dict:
    """Load the frozen v2 fixture using the code snapshot that produced it."""
    if not HISTORICAL_V2_CODE.is_dir():
        raise RuntimeError("Archived v2 source snapshot is required for the frozen pair tests")
    with patch.object(paired, "HERE", HISTORICAL_V2_CODE):
        return paired.load_pair_inputs()


class DummyProvider:
    execution_kind = "offline_fixture"

    def __init__(self, cap: str):
        self.cap = cap
        self.attempts = []
        self.count_requests = 0
        self.generation_requests = 0

    def record_attempt(self, request: dict) -> tuple[dict, dict, str]:
        from copy import deepcopy

        usage = {"input_tokens": 100, "output_tokens": 5}
        cost = "0.0000125"
        timing = {"token_count_ms": 1.0, "generation_ms": 2.0}
        body = {"model": "offline-test-model", **request}
        self.attempts.append({
            "ordinal": len(self.attempts) + 1, "request_input_hash": fingerprint(body),
            "status": "completed", "phase": "completed", "usage": deepcopy(usage),
            "estimated_cost_usd": cost, "timing_ms": deepcopy(timing),
        })
        self.count_requests += 1
        self.generation_requests += 1
        return usage, timing, cost

    def run_state(self) -> dict:
        return {
            "model": "offline-test-model",
            "rate_configuration": {
                "input_usd_per_million": "0.10", "output_usd_per_million": "0.50",
                "count_usd_per_request": "0", "reviewed_by": "private-test-label",
            },
            "limits": {
                "max_spend_usd": self.cap, "max_input_tokens": 8000,
                "max_output_tokens": 2400, "max_generation_requests": 42,
                "max_count_requests": 42, "timeout_seconds": 45,
            },
            "count_requests": self.count_requests,
            "generation_requests": self.generation_requests,
            "halted": False,
            "attempts": deepcopy(self.attempts),
        }


class PairedBudgetTests(unittest.TestCase):
    def test_budget_reserves_42_calls_and_splits_cap(self):
        config = {
            "network_authorized": True, "model": "gpt-6-luna",
            "input_usd_per_million": "0.10", "output_usd_per_million": "0.50",
            "count_usd_per_request": "0", "max_spend_usd": "0.50",
            "max_input_tokens": 8000, "max_output_tokens": 2400,
            "max_generation_requests": 42,
        }
        lexical, hybrid, estimate = paired.paired_provider_configs(config)
        self.assertEqual((lexical["max_spend_usd"], hybrid["max_spend_usd"]), ("0.25", "0.25"))
        self.assertEqual(estimate["generation_calls"], 84)
        self.assertEqual(estimate["token_count_calls"], 84)
        self.assertEqual(estimate["max_reservation_usd"], "0.168")

    def test_budget_rejects_insufficient_cap_and_coerced_token_bounds(self):
        config = {
            "input_usd_per_million": "0.10", "output_usd_per_million": "0.50",
            "count_usd_per_request": "0", "max_spend_usd": "0.05",
            "max_input_tokens": 8000, "max_output_tokens": 2400,
            "max_generation_requests": 42,
        }
        with self.assertRaises(ValueError):
            paired.paired_provider_configs(config)
        config["max_spend_usd"] = "0.50"
        config["max_output_tokens"] = "2400"
        with self.assertRaises(ValueError):
            paired.paired_provider_configs(config)


class ProviderUsageTests(unittest.TestCase):
    def test_single_response_preserves_provider_usage_details(self):
        usage = {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 12},
            "output_tokens": 5,
            "output_tokens_details": {"reasoning_tokens": 2},
            "total_tokens": 105,
        }
        self.assertEqual(paired._expected_answer_usage([usage]), usage)

    def test_quote_retry_sums_token_totals_and_drops_per_response_details(self):
        first = {
            "input_tokens": 100, "input_tokens_details": {"cached_tokens": 12},
            "output_tokens": 5, "output_tokens_details": {"reasoning_tokens": 2},
        }
        retry = {
            "input_tokens": 110, "input_tokens_details": {"cached_tokens": 14},
            "output_tokens": 7, "output_tokens_details": {"reasoning_tokens": 3},
        }
        self.assertEqual(
            paired._expected_answer_usage([first, retry]),
            {"input_tokens": 210, "output_tokens": 12},
        )

    def test_historical_code_snapshot_is_exact_and_mutation_is_rejected(self):
        legacy = deepcopy(paired.LEGACY_ANSWER_CODE_HASHES[0])
        self.assertTrue(paired._answer_code_hashes_supported(legacy))
        self.assertTrue(paired._answer_code_hashes_supported(paired.answer_code_hashes()))
        legacy["qa_retrieval_answer_compare.py"] = "0" * 64
        self.assertFalse(paired._answer_code_hashes_supported(legacy))


class PairedGradeTests(unittest.TestCase):
    def test_expected_abstention_is_counted_even_when_claim_metadata_is_not_applicable(self):
        answer = {"status": "abstained", "execution_kind": "live", "claims": []}
        automatic = {"numeric": "NOT_APPLICABLE", "citation_integrity": "PASS", "expected_response": "PASS"}
        checks = {name: "UNKNOWN" for name in paired.OVERALL_CHECKS}
        checks.update(correctness="PASS", abstention="PASS")
        records = [{"checks": checks}, {"checks": deepcopy(checks)}]
        result = paired._review_verdict(answer, automatic, records)
        case = {"grading_reference": {"abstention": {"reason": "Evidence is incomplete"}},
                "answers": {"lexical": answer}}

        self.assertEqual(result["verdict"], "UNKNOWN")
        self.assertTrue(paired._is_correct_expected_abstention(case, "lexical", result))

class FrozenInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = load_historical_pair_inputs()

    def test_loads_exact_fixed_development_pairs_and_v14_lexical_packs(self):
        self.assertEqual(len(self.prepared["cases"]), 21)
        self.assertEqual(self.prepared["retrieval_run"]["documents"], 12)
        self.assertEqual(self.prepared["retrieval_run"]["passages"], 3922)
        self.assertTrue(all(set(case["packs"]) == {"lexical", "hybrid"} for case in self.prepared["cases"]))

    def test_source_text_tampering_is_rejected_even_with_a_recomputed_pack_hash(self):
        case = self.prepared["cases"][0]
        pack = deepcopy(case["packs"]["hybrid"])
        pack["passages"][0]["text"] += " altered"
        pack["evidence_hash"] = fingerprint({key: item for key, item in pack.items() if key != "evidence_hash"})
        question = case["reference"]
        with self.assertRaisesRegex(ValueError, "passage changed"):
            paired._validate_pack(pack, "hybrid", question, self.prepared["manifest"], self.prepared["chunks"])

    def test_retrieval_hash_manifest_rejects_path_injection(self):
        run = deepcopy(self.prepared["retrieval_run"])
        run["code_sha256"]["../outside-file"] = "not-a-hash"
        rehash(run)
        with self.assertRaisesRegex(ValueError, "unexpected file set"):
            paired._validate_retrieval_run(run, self.prepared["manifest"], paired._development_questions())

    def test_current_source_rejects_the_historical_retrieval_run(self):
        with self.assertRaisesRegex(ValueError, "Retrieval source code changed after the saved run"):
            paired._validate_retrieval_run(
                self.prepared["retrieval_run"], self.prepared["manifest"], paired._development_questions()
            )

    def test_generation_input_contains_no_benchmark_labels(self):
        item = self.prepared["cases"][0]
        request = paired.generation_input(item["packs"]["hybrid"])
        model_input = json.loads(request["input"])
        self.assertEqual(set(request), {"instructions", "input", "text"})
        self.assertEqual(set(model_input), {"ticker", "question", "evidence_hash", "passages", "sources",
                                            "financial_fact_index", "metric_scope_context_index"})
        self.assertNotIn("case_id", model_input)
        self.assertNotIn("expected_numbers", model_input)
        self.assertNotIn("evidence", model_input)
        passage_ids = {passage["chunk_id"] for passage in model_input["passages"]}
        passages = {passage["chunk_id"]: passage["text"] for passage in model_input["passages"]}
        for fact in model_input["financial_fact_index"]:
            self.assertEqual(set(fact), {"fact_id", "ticker", "metric", "period", "accounting_basis", "value", "unit", "source_ids", "chunk_ids", "value_row_citations"})
            self.assertTrue(set(fact["chunk_ids"]) <= passage_ids)
            for row_citation in fact["value_row_citations"]:
                self.assertEqual(set(row_citation), {"chunk_id", "quote"})
                self.assertIn(row_citation["chunk_id"], fact["chunk_ids"])
                self.assertIn(row_citation["quote"], passages[row_citation["chunk_id"]])
        for context in model_input["metric_scope_context_index"]:
            self.assertEqual(set(context), {"fact_id", "metric", "period", "source_id", "anchor_chunk_ids",
                                            "qualifier_chunk_id", "quote"})
            self.assertTrue(set(context["anchor_chunk_ids"]) <= passage_ids)
            self.assertIn(context["qualifier_chunk_id"], passage_ids)
            self.assertIn(context["quote"], passages[context["qualifier_chunk_id"]])

    def test_hybrid_fact_index_keeps_the_observed_financial_periods_attached(self):
        cases = {item["case_id"]: item for item in self.prepared["cases"]}
        nbis = paired.generation_input(cases["NBIS-QA-07"]["packs"]["hybrid"])
        nbis_facts = {fact["fact_id"]: fact for fact in json.loads(nbis["input"])["financial_fact_index"]}
        self.assertEqual((nbis_facts["nbis-capex-q3"]["period"], nbis_facts["nbis-capex-q3"]["value"]),
                         ("2024-07-01/2024-09-30", "167.0"))
        self.assertEqual((nbis_facts["nbis-capex-nine-months"]["period"], nbis_facts["nbis-capex-nine-months"]["value"]),
                         ("2024-01-01/2024-09-30", "387.6"))
        iren = paired.generation_input(cases["IREN-QA-08"]["packs"]["hybrid"])
        iren_facts = {fact["fact_id"]: fact for fact in json.loads(iren["input"])["financial_fact_index"]}
        self.assertEqual((iren_facts["iren-cost-oct-original"]["period"], iren_facts["iren-cost-oct-original"]["value"]), ("2024-10", "20834"))
        self.assertEqual((iren_facts["iren-cost-oct-revised"]["period"], iren_facts["iren-cost-oct-revised"]["value"]), ("2024-10", "18896"))


class OfflinePairFlowTests(unittest.TestCase):
    def setUp(self):
        self.prepared = load_historical_pair_inputs()
        self.historical_inputs_patch = patch.object(paired, "load_pair_inputs", return_value=self.prepared)
        self.historical_inputs_patch.start()
        self.addCleanup(self.historical_inputs_patch.stop)

    def test_same_rag_flow_keeps_references_out_of_generation_and_saves_separate_metrics(self):
        observed = []

        def fake_rag_answer(pack, provider):
            request = paired.generation_input(pack)
            observed.append((provider, request))
            usage, timing, cost = provider.record_attempt(request)
            answer = answer_record("rag", pack["ticker"], pack, "error", "Offline fixture; no answer generated.")
            answer.update(execution_kind="offline_fixture", provider_attempted=True, usage=usage,
                          provider_cost_usd=cost)
            answer["timing_ms"].update(timing)
            return answer

        with tempfile.TemporaryDirectory() as directory, patch.object(paired, "rag_answer", side_effect=fake_rag_answer):
            destination = paired.evaluate_pair(
                self.prepared,
                {"lexical": DummyProvider("0.25"), "hybrid": DummyProvider("0.25")},
                run_id="offline-paired-contract",
                output_root=Path(directory) / "runs",
            )
            run = paired.load_pair_run(destination)

        retried = deepcopy(run)
        lexical_state = retried["provider"]["lexical"]
        inserted = deepcopy(lexical_state["attempts"][0])
        first_case = retried["cases"][0]
        retry_input = paired.generation_input(first_case["evidence"]["lexical"], quote_retry=True)
        inserted["request_input_hash"] = fingerprint({"model": lexical_state["model"], **retry_input})
        lexical_state["attempts"].insert(1, inserted)
        for ordinal, attempt in enumerate(lexical_state["attempts"], 1):
            attempt["ordinal"] = ordinal
        lexical_state["count_requests"] += 1
        lexical_state["generation_requests"] += 1
        first_case["provider_attempts"]["lexical"] = [1, 2]
        first_case["answers"]["lexical"]["quote_retry"] = {
            "used": True, "first_failure_code": "citation_quote_not_verbatim",
            "retry_status": "error", "provider_attempt_count": 2,
        }
        first_case["answers"]["lexical"]["usage"] = {"input_tokens": 200, "output_tokens": 10}
        first_case["answers"]["lexical"]["provider_cost_usd"] = "0.0000250"
        first_case["answers"]["lexical"]["timing_ms"].update(token_count_ms=2.0, generation_ms=4.0)
        for index, case in enumerate(retried["cases"][1:], 1):
            case["provider_attempts"]["lexical"] = [index + 2]
        retried["answers_by_arm"]["lexical"] = paired._answer_summary(retried["cases"], "lexical")
        rehash(retried)
        paired.validate_pair_run(retried)

        bad_retry_hash = deepcopy(retried)
        bad_retry_hash["provider"]["lexical"]["attempts"][1]["request_input_hash"] = "0" * 64
        rehash(bad_retry_hash)
        with self.assertRaisesRegex(ValueError, "does not match the answer evidence input"):
            paired.validate_pair_run(bad_retry_hash)

        self.assertEqual(len(observed), 42)
        self.assertEqual(len(run["cases"]), 21)
        self.assertIn("retrieval_metrics", run)
        self.assertIn("answers_by_arm", run)
        self.assertEqual(run["answers_by_arm"]["lexical"]["latency_sample_size"], 0)
        self.assertEqual([case["provider_attempts"]["lexical"] for case in run["cases"]], [[i] for i in range(1, 22)])
        self.assertEqual([case["provider_attempts"]["hybrid"] for case in run["cases"]], [[i] for i in range(1, 22)])
        instructions = {request["instructions"] for _, request in observed}
        self.assertEqual(len(instructions), 3)  # The prompt names the ticker-specific company.
        for index in range(0, len(observed), 2):
            self.assertEqual(observed[index][1]["instructions"], observed[index + 1][1]["instructions"])
        for _, request in observed:
            payload = json.loads(request["input"])
            self.assertNotIn("expected_numbers", payload)
            self.assertNotIn("case_id", payload)
        for state in run["provider"].values():
            self.assertNotIn("reviewed_by", state["rate_configuration"])
            self.assertEqual(state["count_requests"], 21)
            self.assertEqual(state["execution_kind"], "offline_fixture")
        preflight_error = deepcopy(run)
        for arm in paired.ARMS:
            preflight_error["provider"][arm]["execution_kind"] = "live"
            for case in preflight_error["cases"]:
                case["answers"][arm]["execution_kind"] = "live"
        lexical_state = preflight_error["provider"]["lexical"]
        lexical_state["attempts"].pop()
        lexical_state["count_requests"] = 20
        lexical_state["generation_requests"] = 20
        last_case = preflight_error["cases"][-1]
        last_case["provider_attempts"]["lexical"] = []
        last_answer = last_case["answers"]["lexical"]
        last_answer["provider_attempted"] = False
        last_answer["failure_diagnostic"] = {"stage": "provider_request", "code": "request_preflight_rejected"}
        last_answer["usage"] = None
        last_answer["provider_cost_usd"] = None
        last_answer["timing_ms"].pop("token_count_ms")
        last_answer["timing_ms"].pop("generation_ms")
        config = deepcopy(preflight_error["provider"]["lexical"]["rate_configuration"])
        config.update({"max_spend_usd": "0.50", "max_input_tokens": 8000,
                       "max_output_tokens": 2400, "max_generation_requests": 42})
        _, _, preflight_error["generation_budget"] = paired.paired_provider_configs(config)
        preflight_error["answers_by_arm"] = {
            arm: paired._answer_summary(preflight_error["cases"], arm) for arm in paired.ARMS
        }
        rehash(preflight_error)
        paired.validate_pair_run(preflight_error)

        root = paired.review_template(run, "root")
        verifier = paired.review_template(run, "verifier")
        with self.assertRaises(ValueError):
            paired.grade_pair(run, [root])
        for review in (root, verifier):
            for case in review["cases"].values():
                for arm_record in case.values():
                    arm_record["checks"] = {key: "PASS" for key in arm_record["checks"]}
        graded = paired.grade_pair(run, [root, verifier])
        self.assertEqual(graded["summary"]["lexical"]["verdicts"], {"UNKNOWN": 21})
        self.assertEqual(graded["summary"]["hybrid"]["verdicts"], {"UNKNOWN": 21})

        edited_pack = deepcopy(run)
        hybrid_pack = edited_pack["cases"][0]["evidence"]["hybrid"]
        hybrid_pack["passages"][0]["text"] += " changed after generation"
        hybrid_pack["evidence_hash"] = fingerprint({key: value for key, value in hybrid_pack.items() if key != "evidence_hash"})
        edited_pack["cases"][0]["answers"]["hybrid"]["evidence_hash"] = hybrid_pack["evidence_hash"]
        rehash(edited_pack)
        with self.assertRaisesRegex(ValueError, "differs from its frozen retrieval pack"):
            paired.validate_pair_run(edited_pack)

        edited_score = deepcopy(run)
        edited_score["cases"][0]["automatic"]["lexical"]["citation_integrity"] = "PASS"
        rehash(edited_score)
        with self.assertRaisesRegex(ValueError, "automatic answer checks"):
            paired.validate_pair_run(edited_score)

        edited_summary = deepcopy(run)
        edited_summary["answers_by_arm"]["hybrid"]["statuses"] = {"answered": 21}
        rehash(edited_summary)
        with self.assertRaisesRegex(ValueError, "answer summaries"):
            paired.validate_pair_run(edited_summary)

        edited_execution = deepcopy(run)
        edited_execution["cases"][0]["answers"]["lexical"]["execution_kind"] = "live"
        edited_execution["cases"][0]["answers"]["lexical"]["provider_attempted"] = True
        rehash(edited_execution)
        with self.assertRaisesRegex(ValueError, "differs from its provider session"):
            paired.validate_pair_run(edited_execution)

        edited_unattempted = deepcopy(run)
        lexical_state = edited_unattempted["provider"]["lexical"]
        lexical_state["attempts"] = lexical_state["attempts"][1:]
        for ordinal, attempt in enumerate(lexical_state["attempts"], 1):
            attempt["ordinal"] = ordinal
        lexical_state["count_requests"] = 20
        lexical_state["generation_requests"] = 20
        for index, case in enumerate(edited_unattempted["cases"]):
            case["provider_attempts"]["lexical"] = [] if index == 0 else [index]
        answer = edited_unattempted["cases"][0]["answers"]["lexical"]
        answer["provider_attempted"] = False
        answer["usage"] = {"input_tokens": 100, "output_tokens": 5}
        answer["provider_cost_usd"] = "0.0000125"
        answer["timing_ms"].pop("token_count_ms")
        answer["timing_ms"].pop("generation_ms")
        edited_unattempted["answers_by_arm"]["lexical"] = paired._answer_summary(edited_unattempted["cases"], "lexical")
        rehash(edited_unattempted)
        with self.assertRaisesRegex(ValueError, "without a provider attempt"):
            paired.validate_pair_run(edited_unattempted)

        failed_count = deepcopy(run)
        failed_attempt = failed_count["provider"]["lexical"]["attempts"][-1]
        failed_attempt.update(phase="token_count_request", status="error", usage=None, estimated_cost_usd=None)
        failed_attempt.pop("timing_ms")
        failed_count["provider"]["lexical"]["generation_requests"] = 20
        failed_count["provider"]["lexical"]["halted"] = True
        failed_answer = failed_count["cases"][-1]["answers"]["lexical"]
        failed_answer["usage"] = None
        failed_answer["provider_cost_usd"] = None
        failed_answer["timing_ms"].pop("token_count_ms")
        failed_answer["timing_ms"].pop("generation_ms")
        failed_count["answers_by_arm"]["lexical"] = paired._answer_summary(failed_count["cases"], "lexical")
        rehash(failed_count)
        paired.validate_pair_run(failed_count)

        fabricated_timing = deepcopy(failed_count)
        fabricated_answer = fabricated_timing["cases"][-1]["answers"]["lexical"]
        fabricated_answer["timing_ms"].update(token_count_ms=99.0, generation_ms=99.0)
        fabricated_timing["answers_by_arm"]["lexical"] = paired._answer_summary(fabricated_timing["cases"], "lexical")
        rehash(fabricated_timing)
        with self.assertRaisesRegex(ValueError, "timing differs from its provider attempt"):
            paired.validate_pair_run(fabricated_timing)

        impossible_order = deepcopy(run)
        first_attempt = impossible_order["provider"]["lexical"]["attempts"][0]
        first_attempt.update(phase="token_count_request", status="error", usage=None, estimated_cost_usd=None)
        first_attempt.pop("timing_ms")
        impossible_order["provider"]["lexical"]["generation_requests"] = 20
        impossible_order["provider"]["lexical"]["halted"] = True
        with self.assertRaisesRegex(ValueError, "continue after a failed request"):
            paired._validate_provider_attempts(impossible_order)

        impossible_phase = deepcopy(run)
        impossible_phase["provider"]["lexical"]["attempts"][0]["phase"] = "token_count_request"
        impossible_phase["provider"]["lexical"]["generation_requests"] = 20
        with self.assertRaisesRegex(ValueError, "status and phase disagree"):
            paired._validate_provider_attempts(impossible_phase)


if __name__ == "__main__":
    unittest.main()
