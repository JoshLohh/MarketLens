"""Paired RAG answers over frozen lexical and hybrid evidence packs.

The offline `validate` action makes no provider calls. Live evaluation requires
both an explicit `--live` flag and a reviewed config; benchmark references are
attached only after each answer has been generated.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time
from uuid import uuid4

import qa_compare
from corpus import HERE, LOCAL, SEED, sha256
from llm_provider import MAX_PROVIDER_REQUESTS_PER_ARM, ProviderError, ResponsesProvider, strict_json
from qa_baseline import BENCHMARK, load_release
from qa_contract import DIMENSIONS, answer_record, canonical, fingerprint, validate_answer
from rag_experiment import (
    MAX_EVIDENCE_BYTES,
    MAX_REQUEST_BYTES,
    MAX_QUOTE_RETRIES,
    PACKING_VERSIONS,
    PACK_VERSION,
    SOURCE_FIELDS,
    generation_input,
    omit_contacts,
    rag_answer,
    verify_evidence,
)


EXPERIMENT_VERSION = "paired-rag-retrieval-v2"
RUNS = LOCAL / "answer_pairs"
DEFAULT_RETRIEVAL_RUN = LOCAL / "retrieval_runs" / "hybrid-context-anchored-tail-2026-10-25-v1"
DEFAULT_RELEASE = LOCAL / "releases" / json.loads(SEED.read_text())["corpus_version"]
BASELINE_RUN = LOCAL / "answer_runs" / "live-development-2026-10-25-v14-01"
ARMS = ("lexical", "hybrid")
TAIL_POLICY = "context-anchored-tail-v1"
TAIL_PACK_VERSION = PACKING_VERSIONS[TAIL_POLICY]
ANSWER_CODE_FILES = tuple(dict.fromkeys((*qa_compare.CODE_FILES, "qa_retrieval_answer_compare.py")))
CLAIM_CHECKS = qa_compare.CLAIM_DIMENSIONS
OVERALL_CHECKS = DIMENSIONS
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
LEGACY_BENCHMARK_HASHES = frozenset({"337a3e0e242e6383ca8d31d6928fc37a1cf5ea84e8de9af41115daa6c5b56219"})
LEGACY_ANSWER_CODE_HASHES = ({
    "corpus.py": "574552a8796d1fae198598b5d54e819853e8fe39ce323e3adadcefdb511f05d0",
    "fact_baseline.py": "8c9c5a6fe67975ce33dab0ed92fcd06efdbebe0db9b9130a28dc3726b49315f6",
    "fact_specs.json": "8f07781fbd543d6c6171dc44d8abdcb8a3445b13c77bd3d259c5ba23de837dcb",
    "llm_provider.py": "7d44cc518494fd3dd7c0a48f8c34ad337455cf317aa49f2804105d3a675f5a3c",
    "qa_baseline.py": "fb22d0fc4e403c8becce6caa54450d0984b616629b7a09d5c0c1a93f8b351d4d",
    "qa_compare.py": "6b62648215c5fcd75b298f573081daa0cbf01b0833c779f106359983463a45d3",
    "qa_contract.py": "a5120951492e9c1c246f8006e34b04bc5dceffdf12cb5f4f9c1dbb940ec56043",
    "qa_retrieval_answer_compare.py": "442633b004feca621c42a1811cc893058832333cbee2473f0481609a47d29460",
    "rag_experiment.py": "d280caec5e4f4d6c40b33834733a34b5954cad0032d2647915302040c373a09b",
},)
RETRIEVAL_CODE_FILES = (
    "corpus.py", "qa_baseline.py", "rag_experiment.py", "qa_compare.py", "qa_contract.py",
    "fact_baseline.py", "fact_specs.json", "llm_provider.py", "hybrid_retrieval.py", "retrieval_compare.py",
)


def answer_code_hashes() -> dict:
    return {name: sha256((HERE / name).read_bytes()) for name in ANSWER_CODE_FILES}


def _answer_code_hashes_supported(saved: object) -> bool:
    """Accept current code or an exact pinned snapshot for a recoverable saved run."""
    return saved == answer_code_hashes() or any(saved == legacy for legacy in LEGACY_ANSWER_CODE_HASHES)


def _expected_answer_usage(usages: list) -> dict | None:
    """Preserve usage metadata for one response; sum token totals across retries."""
    def has_valid_totals(usage: object) -> bool:
        return (isinstance(usage, dict)
                and all(type(usage.get(key)) is int and usage[key] >= 0
                        for key in ("input_tokens", "output_tokens")))

    if len(usages) == 1 and has_valid_totals(usages[0]):
        # A single OpenAI response can include cached/reasoning token details.
        return deepcopy(usages[0])
    if usages and all(has_valid_totals(usage) for usage in usages):
        # The answer path intentionally keeps only aggregate totals after retries.
        return {key: sum(usage[key] for usage in usages) for key in ("input_tokens", "output_tokens")}
    return None


def _run_hash_valid(run: dict) -> bool:
    return fingerprint({key: value for key, value in run.items() if key != "run_hash"}) == run.get("run_hash")


def _development_questions() -> dict:
    raw = BENCHMARK.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    if Counter(row.get("split") for row in rows) != {"development": 21, "holdout": 9}:
        raise ValueError("Benchmark partition changed")
    questions = {row["case_id"]: row for row in rows if row["split"] == "development"}
    if len(questions) != 21 or sum(row.get("split") == "development" for row in rows) != len(questions):
        raise ValueError("Expected exactly 21 development questions")
    return questions


def _validate_retrieval_run(run: dict, manifest: dict, questions: dict) -> None:
    if not _run_hash_valid(run):
        raise ValueError("Retrieval run hash mismatch")
    if run.get("split") != "development" or run.get("corpus_version") != manifest.get("corpus_version"):
        raise ValueError("Retrieval run is not for the fixed development corpus")
    if (run.get("documents"), run.get("passages")) != (12, 3922):
        raise ValueError("SEC pilot identity changed")
    if run.get("chunks_sha256") != manifest.get("chunks_sha256"):
        raise ValueError("Retrieval run and corpus chunk hashes differ")
    current_benchmark_hash = sha256(BENCHMARK.read_bytes())
    if run.get("benchmark_sha256") not in {current_benchmark_hash, *LEGACY_BENCHMARK_HASHES}:
        raise ValueError("Retrieval run and benchmark hashes differ")
    if run.get("v14_pack_parity") is not True:
        raise ValueError("Saved lexical packs do not confirm v14 parity")
    if run.get("hybrid_packing_policy") != TAIL_POLICY or run.get("hybrid_pack_version") != TAIL_PACK_VERSION:
        raise ValueError("Expected the selected anchored lexical-tail retrieval run")
    if run.get("hybrid_lexical_tail_depth") != 15:
        raise ValueError("Expected lexical tail depth 15")
    if run.get("hybrid_lexical_floor") != 3 or run.get("hybrid_floor_policy") != "fixed":
        raise ValueError("Expected the fixed lexical floor of three")
    if run.get("hybrid_fusion_weights") != [0.75, 0.25] or run.get("hybrid_passage_window_tokens") != 128:
        raise ValueError("Hybrid retrieval settings do not match the selected candidate")
    code_hashes = run.get("code_sha256")
    if not isinstance(code_hashes, dict) or set(code_hashes) != set(RETRIEVAL_CODE_FILES):
        raise ValueError("Retrieval code-hash manifest has an unexpected file set")
    if code_hashes != {name: sha256((HERE / name).read_bytes()) for name in RETRIEVAL_CODE_FILES}:
        raise ValueError("Retrieval source code changed after the saved run")
    if set(run.get("arms", {})) != set(ARMS):
        raise ValueError("Both lexical and hybrid retrieval arms are required")
    for arm in ARMS:
        arm_cases = run["arms"][arm].get("cases")
        if not isinstance(arm_cases, list) or len(arm_cases) != 21:
            raise ValueError(f"{arm} arm must contain 21 development cases")
        case_ids = [case.get("case_id") for case in arm_cases]
        if len(set(case_ids)) != 21 or set(case_ids) != set(questions):
            raise ValueError(f"{arm} case IDs differ from the development benchmark")
        score_ids = [row.get("case_id") for row in run.get("scores", {}).get(arm, [])]
        if len(score_ids) != 21 or set(score_ids) != set(questions):
            raise ValueError(f"{arm} retrieval scores differ from the development benchmark")
        for case in arm_cases:
            row = questions[case["case_id"]]
            pack = case.get("pack")
            if (not isinstance(pack, dict) or pack.get("ticker") != row["ticker"]
                    or pack.get("question") != row["question"]
                    or case.get("ticker", row["ticker"]) != row["ticker"]
                    or case.get("question", row["question"]) != row["question"]):
                raise ValueError("Saved retrieval query differs from the current development question")


def _validate_pack(pack: dict, arm: str, row: dict, manifest: dict, chunks: list[dict]) -> None:
    verify_evidence(pack)
    expected_version = PACK_VERSION if arm == "lexical" else TAIL_PACK_VERSION
    if pack.get("version") != expected_version:
        raise ValueError(f"Unexpected {arm} evidence pack version")
    if (pack.get("corpus_version"), pack.get("chunks_sha256")) != (
            manifest.get("corpus_version"), manifest.get("chunks_sha256")):
        raise ValueError("Evidence pack and corpus identity differ")
    if pack.get("ticker") != row.get("ticker") or pack.get("question") != row.get("question"):
        raise ValueError("Evidence pack question/company differs from the development case")
    request_sizes = (len(canonical(generation_input(pack))),
                     len(canonical(generation_input(pack, quote_retry=True))))
    if len(canonical(pack)) > MAX_EVIDENCE_BYTES or max(request_sizes) > MAX_REQUEST_BYTES:
        raise ValueError("Evidence pack or request exceeds its fixed byte limit")
    docs = {doc["source_id"]: doc for doc in manifest["documents"]}
    by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    if not isinstance(pack.get("sources"), dict) or not isinstance(pack.get("passages"), list):
        raise ValueError("Evidence source/passages shape is invalid")
    for source_id, metadata in pack["sources"].items():
        doc = docs.get(source_id)
        if doc is None or metadata != {key: doc.get(key) for key in SOURCE_FIELDS}:
            raise ValueError("Evidence source metadata does not match the frozen corpus")
    seen = set()
    for passage in pack["passages"]:
        chunk_id = passage.get("chunk_id")
        chunk = by_id.get(chunk_id)
        if chunk is None or chunk_id in seen:
            raise ValueError("Evidence passage is absent from the frozen corpus or duplicated")
        seen.add(chunk_id)
        expected = {key: chunk[key] for key in ("chunk_id", "source_id", "block_start", "block_end")}
        expected["text"] = omit_contacts(chunk["text"])
        if passage != expected or chunk.get("ticker") != row.get("ticker"):
            raise ValueError("Evidence passage changed company, locator, or source text")
        if passage["source_id"] not in pack["sources"]:
            raise ValueError("Evidence passage source metadata is missing")


def load_pair_inputs(retrieval_run: Path = DEFAULT_RETRIEVAL_RUN,
                     release: Path = DEFAULT_RELEASE) -> dict:
    """Validate frozen lexical/hybrid packs before any answer generation."""
    manifest, chunks = load_release(release)
    questions = _development_questions()
    run = strict_json((retrieval_run / "run.json").read_bytes())
    _validate_retrieval_run(run, manifest, questions)
    baseline = qa_compare.load_run(BASELINE_RUN)
    if run.get("baseline_reference_run_hash") != baseline.get("run_hash"):
        raise ValueError("Retrieval run does not point to the frozen v14 baseline")
    current_benchmark_hash = sha256(BENCHMARK.read_bytes())
    if (baseline.get("experiment_version") != qa_compare.EXPERIMENT_VERSION
            or baseline.get("split") != "development"
            or baseline.get("benchmark_sha256") not in {current_benchmark_hash, *LEGACY_BENCHMARK_HASHES}
            or baseline.get("corpus_version") != run.get("corpus_version")
            or baseline.get("chunks_sha256") != run.get("chunks_sha256")):
        raise ValueError("Frozen v14 answer run identity differs from the retrieval run")
    baseline_queries = {case.get("case_id"): (case.get("ticker"), case.get("question"))
                        for case in baseline.get("cases", [])}
    current_queries = {case_id: (row["ticker"], row["question"]) for case_id, row in questions.items()}
    if baseline_queries != current_queries:
        raise ValueError("Frozen v14 queries differ from the current development questions")
    v14_packs = {case["case_id"]: case["evidence"] for case in baseline["cases"]}
    if len(v14_packs) != 21 or set(v14_packs) != set(questions):
        raise ValueError("Frozen v14 answer run does not contain the 21 development packs")
    cases_by_arm = {
        arm: {case["case_id"]: case for case in run["arms"][arm]["cases"]}
        for arm in ARMS
    }
    score_by_id = {arm: {row["case_id"]: row for row in run["scores"][arm]} for arm in ARMS}
    if any(cases_by_arm["lexical"][case_id]["pack"].get("evidence_hash") != v14_packs[case_id].get("evidence_hash")
           for case_id in questions):
        raise ValueError("Lexical evidence packs do not reproduce the frozen v14 packs")
    prepared = []
    for case_id in sorted(questions):
        question = questions[case_id]
        packs = {}
        for arm in ARMS:
            case = cases_by_arm[arm][case_id]
            pack = case.get("pack")
            if not isinstance(pack, dict):
                raise ValueError("Retrieval case has no evidence pack")
            _validate_pack(pack, arm, question, manifest, chunks)
            packs[arm] = pack
        score_by_id = {arm: {row["case_id"]: row for row in run["scores"][arm]} for arm in ARMS}
        prepared.append({"case_id": case_id, "ticker": question["ticker"], "category": question["category"],
                         "question": question["question"], "packs": packs,
                         "reference": question,
                         "retrieval_metrics": {arm: score_by_id[arm][case_id] for arm in ARMS}})
    return {"retrieval_run": run, "manifest": manifest, "chunks": chunks, "cases": prepared,
            "questions_hash": run["benchmark_sha256"]}


def _validate_provider_attempts(run: dict) -> None:
    """Tie each answer to its ordered initial/retry provider attempts."""
    provider_runs = run.get("provider")
    if not isinstance(provider_runs, dict) or set(provider_runs) != set(ARMS):
        raise ValueError("Paired answer run is missing provider state")
    signatures = []
    for arm in ARMS:
        state = provider_runs[arm]
        if not isinstance(state, dict) or not isinstance(state.get("attempts"), list):
            raise ValueError("Paired provider attempt log is missing")
        attempts = state["attempts"]
        limits = state.get("limits", {})
        if (type(state.get("count_requests")) is not int or state["count_requests"] != len(attempts)
                or type(state.get("generation_requests")) is not int
                or not 0 <= state["generation_requests"] <= len(attempts)
                or type(limits.get("max_generation_requests")) is not int
                or limits["max_generation_requests"] != MAX_PROVIDER_REQUESTS_PER_ARM
                or type(limits.get("max_count_requests")) is not int
                or limits["max_count_requests"] != MAX_PROVIDER_REQUESTS_PER_ARM
                or len(attempts) > MAX_PROVIDER_REQUESTS_PER_ARM):
            raise ValueError("Paired provider request counts do not match the attempt log")
        signature_limits = dict(state.get("limits", {}))
        signature_limits.pop("max_spend_usd", None)
        signature_rates = deepcopy(state.get("rate_configuration"))
        if isinstance(signature_rates, dict):
            signature_rates.pop("reviewed_by", None)
        signatures.append((state.get("model"), state.get("execution_kind"), signature_rates, signature_limits))
        expected_generation_requests = 0
        for ordinal, attempt in enumerate(attempts, 1):
            if not isinstance(attempt, dict) or attempt.get("ordinal") != ordinal:
                raise ValueError("Paired provider attempt order is invalid")
            if (attempt.get("status") not in {"completed", "error"}
                    or attempt.get("phase") not in {"token_count_request", "token_count_validation",
                                                     "answer_generation_request", "answer_response_validation", "completed"}
                    or (attempt.get("status") == "completed") != (attempt.get("phase") == "completed")):
                raise ValueError("Paired provider attempt status and phase disagree")
            if attempt.get("phase") in {"answer_generation_request", "answer_response_validation", "completed"}:
                expected_generation_requests += 1
        if state["generation_requests"] != expected_generation_requests:
            raise ValueError("Paired generation request count differs from recorded attempts")
        failed_attempt = any(attempt.get("status") != "completed" for attempt in attempts)
        if type(state.get("halted")) is not bool or state["halted"] != failed_attempt:
            raise ValueError("Paired provider halt state differs from its attempt results")
        if any(attempt.get("status") != "completed" for attempt in attempts[:-1]):
            raise ValueError("Provider attempts continue after a failed request halted the session")

        cursor = 0
        for case in run["cases"]:
            if set(case.get("provider_attempts", {})) != set(ARMS):
                raise ValueError("Paired answer case is missing provider-attempt references")
            answer = case["answers"][arm]
            reference = case.get("provider_attempts", {}).get(arm)
            attempted = answer.get("provider_attempted", False)
            if type(attempted) is not bool or not isinstance(reference, list):
                raise ValueError("Answer provider-attempt marker is invalid")
            if len(reference) > MAX_QUOTE_RETRIES + 1 or any(type(item) is not int for item in reference):
                raise ValueError("Answer has too many or malformed provider-attempt references")
            if not reference:
                execution_kind = answer.get("execution_kind")
                if attempted or answer.get("status") == "answered":
                    raise ValueError("Live answer has no corresponding provider attempt")
                if execution_kind == "live" and (answer.get("status") != "error"
                                                   or not isinstance(answer.get("failure_diagnostic"), dict)):
                    raise ValueError("Live answer without an attempt must be a diagnosed preflight error")
                timing = answer.get("timing_ms")
                if (answer.get("usage") is not None or answer.get("provider_cost_usd") is not None
                        or isinstance(timing, dict) and any(key in timing for key in ("token_count_ms", "generation_ms"))):
                    raise ValueError("Answer has provider usage, cost or timing without a provider attempt")
                continue
            expected_ordinals = list(range(cursor + 1, cursor + len(reference) + 1))
            if reference != expected_ordinals or not attempted or cursor + len(reference) > len(attempts):
                raise ValueError("Answer/provider-attempt reference is invalid")
            if answer.get("execution_kind") != state.get("execution_kind"):
                raise ValueError("Answer execution kind differs from its provider session")
            case_attempts = attempts[cursor:cursor + len(reference)]
            for attempt_index, attempt in enumerate(case_attempts):
                request = generation_input(case["evidence"][arm], quote_retry=attempt_index > 0)
                request_hash = fingerprint({"model": state.get("model"), **request})
                if attempt.get("request_input_hash") != request_hash:
                    raise ValueError("Provider attempt does not match the answer evidence input")
            cursor += len(reference)
            retry = answer.get("quote_retry")
            if len(reference) > 1:
                if (not isinstance(retry, dict) or retry.get("used") is not True
                        or retry.get("first_failure_code") != "citation_quote_not_verbatim"
                        or retry.get("provider_attempt_count") != len(reference)):
                    raise ValueError("Multiple provider attempts lack a valid quote-retry record")
            elif retry is not None:
                if (not isinstance(retry, dict) or retry.get("used") is not True
                        or retry.get("first_failure_code") != "citation_quote_not_verbatim"
                        or retry.get("provider_attempt_count") != len(reference)):
                    raise ValueError("Quote-retry record differs from provider attempts")
            usages = [attempt.get("usage") for attempt in case_attempts]
            expected_usage = _expected_answer_usage(usages)
            costs = [attempt.get("estimated_cost_usd") for attempt in case_attempts]
            expected_cost = (format(sum((Decimal(cost) for cost in costs), Decimal(0)), "f")
                             if all(isinstance(cost, str) for cost in costs) else None)
            if answer.get("usage") != expected_usage or answer.get("provider_cost_usd") != expected_cost:
                raise ValueError("Answer usage/cost differs from its provider attempts")
            attempt_timings = [attempt.get("timing_ms") for attempt in case_attempts]
            answer_timing = answer.get("timing_ms")
            provider_timing_keys = {"token_count_ms", "generation_ms"}
            answer_provider_timing = {key: answer_timing[key] for key in provider_timing_keys
                                      if isinstance(answer_timing, dict) and key in answer_timing}
            if any(timing is not None and not isinstance(timing, dict) for timing in attempt_timings):
                raise ValueError("Provider attempt timing record is invalid")
            expected_provider_timing = {}
            if all(isinstance(timing, dict) and all(isinstance(timing.get(key), (int, float)) for key in provider_timing_keys)
                   for timing in attempt_timings):
                expected_provider_timing = {
                    key: round(sum(timing[key] for timing in attempt_timings), 3)
                    for key in provider_timing_keys
                }
            if (set(answer_provider_timing) != set(expected_provider_timing)
                    or any(answer_provider_timing.get(name) != value for name, value in expected_provider_timing.items())):
                raise ValueError("Answer timing differs from its provider attempt")
        if cursor != len(attempts):
            raise ValueError("Provider attempt log contains an unpaired answer request")
    if signatures[0] != signatures[1]:
        raise ValueError("Paired provider model, pricing, execution or token settings differ")
    if signatures[0][1] == "live":
        lexical_limits = provider_runs["lexical"]["limits"]
        hybrid_limits = provider_runs["hybrid"]["limits"]
        if lexical_limits.get("max_spend_usd") != hybrid_limits.get("max_spend_usd"):
            raise ValueError("Live paired provider caps are not evenly split")
        total_cap = Decimal(str(lexical_limits["max_spend_usd"])) * 2
        config = deepcopy(provider_runs["lexical"]["rate_configuration"])
        config.update({
            "max_spend_usd": format(total_cap, "f"),
            "max_input_tokens": lexical_limits["max_input_tokens"],
            "max_output_tokens": lexical_limits["max_output_tokens"],
            "max_generation_requests": lexical_limits["max_generation_requests"],
        })
        _, _, expected_budget = paired_provider_configs(config)
        if run.get("generation_budget") != expected_budget:
            raise ValueError("Saved generation budget differs from provider limits and rates")


def paired_provider_configs(config: dict) -> tuple[dict, dict, dict]:
    """Reserve up to two provider calls per question and split the cap evenly."""
    if not isinstance(config, dict):
        raise ValueError("Reviewed provider config must be an object")
    try:
        total_cap = Decimal(str(config["max_spend_usd"]))
        input_rate = Decimal(str(config["input_usd_per_million"]))
        output_rate = Decimal(str(config["output_usd_per_million"]))
        count_fee = Decimal(str(config["count_usd_per_request"]))
        input_limit = int(config.get("max_input_tokens", 8000))
        output_limit = int(config.get("max_output_tokens", 1200))
        calls_per_arm = int(config.get("max_generation_requests", MAX_PROVIDER_REQUESTS_PER_ARM))
    except (KeyError, ValueError, TypeError, InvalidOperation) as error:
        raise ValueError("Reviewed provider config has invalid budget fields") from error
    if (not total_cap.is_finite() or not input_rate.is_finite() or not output_rate.is_finite()
            or not count_fee.is_finite() or not 0 < total_cap <= 1
            or min(input_rate, output_rate) <= 0 or count_fee < 0):
        raise ValueError("Reviewed provider config has invalid rates or cap")
    if (type(config.get("max_generation_requests", MAX_PROVIDER_REQUESTS_PER_ARM)) is not int
            or type(config.get("max_input_tokens", 8000)) is not int
            or type(config.get("max_output_tokens", 1200)) is not int
            or calls_per_arm != MAX_PROVIDER_REQUESTS_PER_ARM
            or not 1 <= input_limit <= 8000 or not 1 <= output_limit <= 2400):
        raise ValueError("Paired quote-retry evaluation requires 42 calls per arm and supported token limits")
    worst_case = Decimal(2 * calls_per_arm) * (
        (Decimal(input_limit) * input_rate + Decimal(output_limit) * output_rate) / Decimal(1_000_000)
        + count_fee
    )
    if worst_case > total_cap:
        raise ValueError("Worst-case paired reservation exceeds the configured aggregate budget")
    arm_cap = total_cap / Decimal(2)
    configs = {}
    for arm in ARMS:
        arm_config = deepcopy(config)
        arm_config["max_spend_usd"] = format(arm_cap, "f")
        arm_config["max_generation_requests"] = calls_per_arm
        configs[arm] = arm_config
    estimate = {"aggregate_cap_usd": format(total_cap, "f"),
                "per_arm_cap_usd": format(arm_cap, "f"),
                "max_reservation_usd": format(worst_case, "f"),
                "input_tokens_per_call": input_limit,
                "output_tokens_per_call": output_limit,
                "generation_calls": 2 * calls_per_arm,
                "token_count_calls": 2 * calls_per_arm,
                "count_fee_usd_per_call": format(count_fee, "f")}
    return configs["lexical"], configs["hybrid"], estimate


def _safe_provider_state(provider) -> dict:
    state = provider.run_state()
    # A reviewed_by field may contain a personal name; the rate/config hash is
    # enough to tie this execution to its private local configuration.
    state.get("rate_configuration", {}).pop("reviewed_by", None)
    state["execution_kind"] = getattr(provider, "execution_kind", "unknown")
    return state


def _run_id(value: str | None) -> str:
    result = value or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    if not RUN_ID_PATTERN.fullmatch(result):
        raise ValueError("Unsafe paired run ID")
    return result


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return ordered[index]


def _answer_summary(cases: list[dict], arm: str) -> dict:
    answers = [case["answers"][arm] for case in cases]
    automatic = [case["automatic"][arm] for case in cases]
    latencies = [a.get("timing_ms", {}).get("answer_ms") for a in answers
                 if a.get("execution_kind") == "live" and a.get("provider_attempted")
                 and isinstance(a.get("timing_ms", {}).get("answer_ms"), (int, float))]
    provider_latencies = [a["timing_ms"]["token_count_ms"] + a["timing_ms"]["generation_ms"]
                          for a in answers if a.get("execution_kind") == "live" and a.get("provider_attempted")
                          and isinstance(a.get("timing_ms"), dict)
                          and isinstance(a["timing_ms"].get("token_count_ms"), (int, float))
                          and isinstance(a["timing_ms"].get("generation_ms"), (int, float))]
    costs = [Decimal(str(a["provider_cost_usd"])) for a in answers if a.get("provider_cost_usd") is not None]
    inputs = [a["usage"]["input_tokens"] for a in answers
              if isinstance(a.get("usage"), dict) and isinstance(a["usage"].get("input_tokens"), int)]
    outputs = [a["usage"]["output_tokens"] for a in answers
               if isinstance(a.get("usage"), dict) and isinstance(a["usage"].get("output_tokens"), int)]
    evidence_sizes = [case["evidence_bytes"][arm] for case in cases]
    request_sizes = [case["request_bytes"][arm] for case in cases]
    return {
        "cases": len(answers),
        "statuses": dict(sorted(Counter(a.get("status", "UNKNOWN") for a in answers).items())),
        "numeric": dict(sorted(Counter(a.get("numeric", "UNKNOWN") for a in automatic).items())),
        "citation_integrity": dict(sorted(Counter(a.get("citation_integrity", "UNKNOWN") for a in automatic).items())),
        "expected_response": dict(sorted(Counter(a.get("expected_response", "UNKNOWN") for a in automatic).items())),
        "median_answer_ms": statistics.median(latencies) if latencies else None,
        "p95_answer_ms": _p95(latencies),
        "latency_sample_size": len(latencies),
        "median_provider_request_ms": statistics.median(provider_latencies) if provider_latencies else None,
        "p95_provider_request_ms": _p95(provider_latencies),
        "provider_latency_sample_size": len(provider_latencies),
        "input_tokens": sum(inputs),
        "output_tokens": sum(outputs),
        "provider_cost_usd": format(sum(costs, Decimal(0)), "f"),
        "provider_cost_unknown_cases": len(answers) - len(costs),
        "evidence_bytes_median_max": {"median": statistics.median(evidence_sizes), "max": max(evidence_sizes)},
        "request_bytes_median_max": {"median": statistics.median(request_sizes), "max": max(request_sizes)},
    }


def evaluate_pair(prepared: dict, providers: dict, run_id: str | None = None,
                  output_root: Path = RUNS, budget_estimate: dict | None = None) -> Path:
    """Generate and grade the two frozen evidence arms using identical RAG code."""
    if set(providers) != set(ARMS):
        raise ValueError("A provider session is required for each paired arm")
    if len(prepared.get("cases", [])) != 21:
        raise ValueError("Paired answer generation requires exactly 21 development cases")
    provider_states = {arm: providers[arm].run_state() for arm in ARMS}
    for key in ("model", "rate_configuration"):
        left = deepcopy(provider_states["lexical"].get(key))
        right = deepcopy(provider_states["hybrid"].get(key))
        if isinstance(left, dict):
            left.pop("reviewed_by", None)
        if isinstance(right, dict):
            right.pop("reviewed_by", None)
        if left != right:
            raise ValueError("Paired arms must use the same model and pricing configuration")
    left_limits = dict(provider_states["lexical"].get("limits", {}))
    right_limits = dict(provider_states["hybrid"].get("limits", {}))
    left_limits.pop("max_spend_usd", None)
    right_limits.pop("max_spend_usd", None)
    if left_limits != right_limits:
        raise ValueError("Paired arms must use the same request and token limits")
    cases = []
    for ordinal, item in enumerate(prepared["cases"]):
        answers, automatic, evidence_bytes, request_bytes, retry_request_bytes, provider_attempt_refs = {}, {}, {}, {}, {}, {}
        order = ARMS if ordinal % 2 == 0 else tuple(reversed(ARMS))
        for arm in order:
            pack = item["packs"][arm]
            frozen_hash = pack["evidence_hash"]
            answer_input = generation_input(pack)
            retry_input = generation_input(pack, quote_retry=True)
            evidence_bytes[arm] = len(canonical(pack))
            request_bytes[arm] = len(canonical(answer_input))
            retry_request_bytes[arm] = len(canonical(retry_input))
            if (evidence_bytes[arm] > MAX_EVIDENCE_BYTES
                    or max(request_bytes[arm], retry_request_bytes[arm]) > MAX_REQUEST_BYTES):
                raise ValueError("Paired evidence or generation request exceeds fixed budget")
            provider_attempts = getattr(providers[arm], "attempts", [])
            attempts_before = len(provider_attempts)
            start = time.perf_counter()
            try:
                answer = rag_answer(pack, providers[arm])
            except Exception:
                answer = answer_record("rag", item["ticker"], pack, "error", "Answer pipeline failed; no supported answer produced.")
            answer.setdefault("timing_ms", {})["paired_evaluation_ms"] = round((time.perf_counter() - start) * 1000, 3)
            verify_evidence(pack)
            if pack["evidence_hash"] != frozen_hash:
                raise ValueError("Answer flow changed a frozen evidence pack")
            if answer.get("method") != "rag" or answer.get("evidence_hash") != frozen_hash:
                raise ValueError("Answer flow changed method or evidence identity")
            attempts_after = getattr(providers[arm], "attempts", [])
            attempt_slice = attempts_after[attempts_before:]
            if len(attempt_slice) > MAX_QUOTE_RETRIES + 1:
                raise ValueError("One paired answer created an unexpected provider-attempt count")
            attempted = bool(attempt_slice)
            if bool(answer.get("provider_attempted", False)) != attempted:
                raise ValueError("Answer and provider-attempt records disagree")
            provider_attempt_refs[arm] = [attempt["ordinal"] for attempt in attempt_slice]
            answers[arm] = answer
            automatic[arm] = qa_compare.automatic_checks(answer, pack, item["reference"])
        cases.append({
            "case_id": item["case_id"], "ticker": item["ticker"], "category": item["category"],
            "question": item["question"], "evidence": item["packs"], "answers": answers,
            "automatic": automatic, "evidence_bytes": evidence_bytes, "request_bytes": request_bytes,
            "retry_request_bytes": retry_request_bytes,
            "provider_attempts": provider_attempt_refs,
            "retrieval_metrics": item["retrieval_metrics"],
            "grading_reference": item["reference"],
        })
    retrieval_run = prepared["retrieval_run"]
    run = {
        "experiment_version": EXPERIMENT_VERSION,
        "run_id": _run_id(run_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "split": "development",
        "retrieval_run_id": retrieval_run["run_id"],
        "retrieval_run_hash": retrieval_run["run_hash"],
        "retrieval_code_sha256": retrieval_run["code_sha256"],
        "answer_code_sha256": answer_code_hashes(),
        "benchmark_sha256": sha256(BENCHMARK.read_bytes()),
        "retrieval_benchmark_sha256": retrieval_run["benchmark_sha256"],
        "corpus_version": retrieval_run["corpus_version"],
        "chunks_sha256": retrieval_run["chunks_sha256"],
        "documents": 12,
        "passages": 3922,
        "retrieval_metrics": {arm: retrieval_run["summary"][arm] for arm in ARMS},
        "provider": {arm: _safe_provider_state(providers[arm]) for arm in ARMS},
        "generation_budget": budget_estimate,
        "answers_by_arm": {arm: _answer_summary(cases, arm) for arm in ARMS},
        "cases": cases,
        "limits": "Same question and RAG answer/validator flow for both frozen evidence packs. Retrieval metrics and answer grades are recorded separately. This is a development comparison, not a blind result.",
    }
    run["run_hash"] = fingerprint(run)
    destination = output_root / run["run_id"]
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return destination


def validate_pair_run(run: dict) -> None:
    if not _run_hash_valid(run) or run.get("experiment_version") != EXPERIMENT_VERSION:
        raise ValueError("Paired answer run hash/version mismatch")
    questions = _development_questions()
    if (run.get("split") != "development" or run.get("benchmark_sha256") != sha256(BENCHMARK.read_bytes())
            or run.get("documents") != 12 or run.get("passages") != 3922
            or len(run.get("cases", [])) != 21):
        raise ValueError("Paired answer run must contain 21 development cases")
    if not _answer_code_hashes_supported(run.get("answer_code_sha256")):
        raise ValueError("Paired answer code changed after the saved run")
    case_ids = [case.get("case_id") for case in run["cases"]]
    if len(set(case_ids)) != 21 or set(case_ids) != set(questions):
        raise ValueError("Paired answer run case IDs differ from the development benchmark")
    retrieval_run_id = run.get("retrieval_run_id")
    if not isinstance(retrieval_run_id, str) or not RUN_ID_PATTERN.fullmatch(retrieval_run_id):
        raise ValueError("Paired answer run has an invalid retrieval-run reference")
    retrieval_root = LOCAL / "retrieval_runs"
    retrieval_path = retrieval_root / retrieval_run_id
    if retrieval_path.resolve().parent != retrieval_root.resolve():
        raise ValueError("Paired answer run retrieval path escaped the local run directory")
    prepared = load_pair_inputs(retrieval_path, DEFAULT_RELEASE)
    retrieval = prepared["retrieval_run"]
    if (retrieval.get("run_id") != retrieval_run_id
            or run.get("retrieval_run_hash") != retrieval.get("run_hash")
            or run.get("retrieval_code_sha256") != retrieval.get("code_sha256")
            or run.get("retrieval_benchmark_sha256") != retrieval.get("benchmark_sha256")
            or run.get("retrieval_metrics") != {arm: retrieval["summary"][arm] for arm in ARMS}
            or run.get("corpus_version") != retrieval.get("corpus_version")
            or run.get("chunks_sha256") != retrieval.get("chunks_sha256")):
        raise ValueError("Paired answer run provenance differs from the frozen retrieval run")
    prepared_by_id = {case["case_id"]: case for case in prepared["cases"]}
    for case in run["cases"]:
        if set(case.get("evidence", {})) != set(ARMS) or set(case.get("answers", {})) != set(ARMS):
            raise ValueError("Paired answer case is missing an arm")
        expected_case = prepared_by_id[case["case_id"]]
        if (case.get("ticker") != expected_case["ticker"]
                or case.get("category") != expected_case["category"]
                or case.get("question") != expected_case["question"]
                or case.get("grading_reference") != expected_case["reference"]
                or case.get("retrieval_metrics") != expected_case["retrieval_metrics"]):
            raise ValueError("Paired answer case or retrieval metrics differ from frozen inputs")
        expected_evidence_bytes, expected_request_bytes, expected_retry_request_bytes = {}, {}, {}
        for arm in ARMS:
            pack, answer = case["evidence"][arm], case["answers"][arm]
            verify_evidence(pack)
            if canonical(pack) != canonical(expected_case["packs"][arm]):
                raise ValueError("Saved answer evidence differs from its frozen retrieval pack")
            if (answer.get("evidence_hash") != pack.get("evidence_hash")
                    or answer.get("ticker") != case.get("ticker") or answer.get("method") != "rag"):
                raise ValueError("Answer/evidence hash mismatch")
            validate_answer(answer, pack)
            expected_evidence_bytes[arm] = len(canonical(pack))
            expected_request_bytes[arm] = len(canonical(generation_input(pack)))
            expected_retry_request_bytes[arm] = len(canonical(generation_input(pack, quote_retry=True)))
            expected_automatic = qa_compare.automatic_checks(answer, pack, expected_case["reference"])
            if case.get("automatic", {}).get(arm) != expected_automatic:
                raise ValueError("Saved automatic answer checks differ from the answer and evidence")
        if (case.get("evidence_bytes") != expected_evidence_bytes
                or case.get("request_bytes") != expected_request_bytes
                or case.get("retry_request_bytes") != expected_retry_request_bytes):
            raise ValueError("Saved evidence/request byte metrics differ from the supplied packs")
    _validate_provider_attempts(run)
    if run.get("answers_by_arm") != {arm: _answer_summary(run["cases"], arm) for arm in ARMS}:
        raise ValueError("Saved answer summaries differ from the paired answer cases")


def load_pair_run(path: Path) -> dict:
    run = strict_json((path / "run.json").read_bytes())
    validate_pair_run(run)
    return run


def review_template(run: dict, reviewer: str) -> dict:
    if reviewer not in {"root", "verifier"}:
        raise ValueError("Reviewer must be root or verifier")
    validate_pair_run(run)
    return {
        "reviewer": reviewer,
        "run_hash": run["run_hash"],
        "cases": {
            case["case_id"]: {
                arm: {
                    "checks": {dimension: "UNKNOWN" for dimension in OVERALL_CHECKS},
                    "rationale": "Source review pending",
                    "claims": {
                        claim["claim_id"]: {
                            "checks": {dimension: "UNKNOWN" for dimension in CLAIM_CHECKS},
                            "rationale": "Source review pending",
                        }
                        for claim in case["answers"][arm].get("claims", [])
                    },
                }
                for arm in ARMS
            }
            for case in run["cases"]
        },
    }


def _review_verdict(answer: dict, automatic: dict, records: list[dict]) -> dict:
    checks, claim_checks, disagreements, values = {}, {}, [], []
    for dimension in OVERALL_CHECKS:
        states = [record.get("checks", {}).get(dimension, "UNKNOWN") for record in records]
        if any(state not in {"PASS", "FAIL", "UNKNOWN"} for state in states):
            raise ValueError("Invalid paired review state")
        if len(set(states)) > 1:
            disagreements.append(dimension)
        checks[dimension] = states[0] if len(set(states)) == 1 else "UNKNOWN"
        values.extend(states)
    for claim in answer.get("claims", []):
        claim_checks[claim["claim_id"]] = {}
        for dimension in CLAIM_CHECKS:
            states = [record.get("claims", {}).get(claim["claim_id"], {}).get("checks", {}).get(dimension, "UNKNOWN")
                      for record in records]
            if any(state not in {"PASS", "FAIL", "UNKNOWN"} for state in states):
                raise ValueError("Invalid paired claim review state")
            if len(set(states)) > 1:
                disagreements.append(claim["claim_id"] + ":" + dimension)
            claim_checks[claim["claim_id"]][dimension] = states[0] if len(set(states)) == 1 else "UNKNOWN"
            values.extend(states)
    if answer.get("status") in {"error", "unavailable"} or answer.get("execution_kind") != "live":
        verdict = "UNKNOWN"
    elif disagreements:
        verdict = "UNKNOWN"
    elif "FAIL" in values or "FAIL" in (automatic["numeric"], automatic["citation_integrity"], automatic["expected_response"]):
        verdict = "FAIL"
    elif "UNKNOWN" in values or "UNKNOWN" in (automatic["numeric"], automatic["citation_integrity"], automatic["expected_response"]):
        verdict = "UNKNOWN"
    else:
        verdict = "PASS"
    return {"verdict": verdict, "checks": checks, "claim_checks": claim_checks,
            "review_complete": "UNKNOWN" not in values,
            "disagreements": disagreements, "reviews": records}


def _is_correct_expected_abstention(case: dict, arm: str, result: dict) -> bool:
    """Count abstention quality independently from inapplicable answer metadata."""
    answer = case["answers"][arm]
    return (
        bool(case["grading_reference"].get("abstention"))
        and answer.get("status") == "abstained"
        and result["checks"].get("correctness") == "PASS"
        and result["checks"].get("abstention") == "PASS"
    )


def grade_pair(run: dict, reviews: list[dict]) -> dict:
    validate_pair_run(run)
    if (len(reviews) != 2 or {review.get("reviewer") for review in reviews} != {"root", "verifier"}
            or any(review.get("run_hash") != run["run_hash"] for review in reviews)):
        raise ValueError("Need root and fresh verifier reviews for this exact paired run")
    graded_cases, summaries = [], {}
    for arm in ARMS:
        values = []
        for case in run["cases"]:
            records = [review.get("cases", {}).get(case["case_id"], {}).get(arm, {}) for review in reviews]
            result = _review_verdict(case["answers"][arm], case["automatic"][arm], records)
            values.append((case, result))
        summaries[arm] = {
            "verdicts": dict(Counter(result["verdict"] for _, result in values)),
            "denominator": len(values),
            "reviewed_cases": sum(result["review_complete"] and case["answers"][arm].get("status") not in {"error", "unavailable"}
                                  and case["answers"][arm].get("execution_kind") == "live" for case, result in values),
            "answered_cases": sum(case["answers"][arm].get("status") == "answered" and case["answers"][arm].get("execution_kind") == "live" for case, _ in values),
            "correct_abstentions": sum(_is_correct_expected_abstention(case, arm, result)
                                       for case, result in values),
            "excessive_abstentions": sum(not bool(case["grading_reference"].get("abstention"))
                                         and case["answers"][arm].get("status") == "abstained" for case, _ in values),
            "by_category": {category: dict(Counter(result["verdict"] for case, result in values if case["category"] == category))
                            for category in sorted({case["category"] for case, _ in values})},
            "dimensions": {dimension: dict(Counter(result["checks"][dimension] for _, result in values))
                           for dimension in OVERALL_CHECKS},
            "claim_dimensions": {
                dimension: dict(Counter(states.get(dimension, "UNKNOWN")
                                        for _, result in values
                                        for states in result["claim_checks"].values()))
                for dimension in CLAIM_CHECKS
            },
        }
        for case, result in values:
            for graded in graded_cases:
                if graded["case_id"] == case["case_id"]:
                    graded["arms"][arm] = result
                    break
            else:
                graded_cases.append({"case_id": case["case_id"], "category": case["category"], "arms": {arm: result}})
    return {"grading_version": "paired-rag-review-v1", "run_hash": run["run_hash"],
            "review_hashes": [fingerprint(review) for review in reviews],
            "summary": summaries, "cases": graded_cases,
            "limits": "Two-reviewer source judgments and automatic checks are development-set evidence, not a blind generalization or automatic entailment guarantee."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "evaluate", "review-template", "grade"))
    parser.add_argument("--retrieval-run", type=Path, default=DEFAULT_RETRIEVAL_RUN)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--run-id")
    parser.add_argument("--run", type=Path)
    parser.add_argument("--reviewer", choices=("root", "verifier"))
    parser.add_argument("--root-review", type=Path)
    parser.add_argument("--verifier-review", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--live-config", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "validate":
            if args.live or args.live_config:
                parser.error("validate is offline and does not accept live configuration")
            prepared = load_pair_inputs(args.retrieval_run, args.release)
            print(json.dumps({"status": "valid", "retrieval_run_id": prepared["retrieval_run"]["run_id"],
                              "retrieval_run_hash": prepared["retrieval_run"]["run_hash"],
                              "questions": len(prepared["cases"]), "documents": 12, "passages": 3922,
                              "arms": list(ARMS), "network_calls": 0}, indent=2))
        elif args.action == "evaluate":
            if not args.live or not args.live_config:
                parser.error("evaluate requires --live and an explicit reviewed config; use validate for offline checks")
            prepared = load_pair_inputs(args.retrieval_run, args.release)
            config = strict_json(args.live_config.read_bytes())
            lexical_config, hybrid_config, estimate = paired_provider_configs(config)
            providers = {
                "lexical": ResponsesProvider(lexical_config, network_enabled=True),
                "hybrid": ResponsesProvider(hybrid_config, network_enabled=True),
            }
            destination = evaluate_pair(prepared, providers, args.run_id, budget_estimate=estimate)
            run = load_pair_run(destination)
            print(destination)
            print(json.dumps({"run_hash": run["run_hash"], "generation_budget": estimate,
                              "answers_by_arm": run["answers_by_arm"]}, indent=2))
        elif args.action == "review-template":
            if not args.run or not args.reviewer:
                parser.error("review-template requires --run and --reviewer")
            run = load_pair_run(args.run)
            path = args.run / (args.reviewer + "-review.json")
            with path.open("x") as handle:
                handle.write(json.dumps(review_template(run, args.reviewer), indent=2, ensure_ascii=False) + "\n")
            print(path)
        else:
            if not args.run or not args.root_review or not args.verifier_review:
                parser.error("grade requires --run and both reviewer files")
            run = load_pair_run(args.run)
            reviews = [strict_json(path.read_bytes()) for path in (args.root_review, args.verifier_review)]
            report = grade_pair(run, reviews)
            path = args.run / ("graded-" + fingerprint(report)[:12] + ".json")
            with path.open("x") as handle:
                handle.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
            print(json.dumps(report["summary"], indent=2))
            print(path)
        return 0
    except (OSError, ValueError, KeyError, TypeError, ProviderError, InvalidOperation):
        print("Paired RAG operation failed: invalid/missing config, frozen evidence, review, or run artifact.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
