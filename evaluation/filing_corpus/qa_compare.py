"""Run and review the two Step 3 answerers on the same fixed filing evidence.

Benchmark references enter only this grading layer, after answers are generated.
All run/review/report artifacts are local and ignored. No live model is defaulted.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
import sys
import time
from uuid import uuid4

from corpus import HERE, LOCAL, SEED, sha256
from fact_baseline import structured_answer
from llm_provider import ResponsesProvider, strict_json
from qa_baseline import BASELINE_VERSION, BENCHMARK, LexicalIndex, load_release
from qa_contract import DIMENSIONS, answer_record, decimal, fingerprint, same_metric_name, validate_answer, value_in_unit
from rag_experiment import PACK_VERSION, TOP_K, build_evidence, rag_answer, verify_evidence


EXPERIMENT_VERSION = "filing-answers-v14"
RUNS = LOCAL / "answer_runs"
CODE_FILES = ("corpus.py", "qa_baseline.py", "qa_contract.py", "fact_specs.json", "fact_baseline.py", "rag_experiment.py", "llm_provider.py", "qa_compare.py")
CLAIM_DIMENSIONS = ("support", "citations", "company", "period", "basis", "units", "vintage")


def code_hashes() -> dict:
    return {name: sha256((HERE / name).read_bytes()) for name in CODE_FILES}


def prepare(release: Path):
    start = time.perf_counter()
    manifest, chunks = load_release(release)
    return manifest, chunks, LexicalIndex(chunks), round((time.perf_counter() - start) * 1000, 3)


def generate_pair(manifest: dict, chunks: list[dict], index: LexicalIndex, ticker: str, question: str, provider=None) -> tuple[dict, dict, list[dict]]:
    start = time.perf_counter()
    ranked = index.search(ticker, question, TOP_K)
    retrieval_ms = (time.perf_counter() - start) * 1000
    pack = build_evidence(manifest, chunks, ranked, ticker, question)
    evidence_ms = (time.perf_counter() - start) * 1000
    answers = []
    for method, generate in (("structured", structured_answer), ("rag", lambda p: rag_answer(p, provider))):
        frozen_hash = pack["evidence_hash"]
        try:
            answer = generate(pack)
        except Exception:
            answer = answer_record(method, ticker, pack, "error", "Answer pipeline failed; no supported answer produced.")
        verify_evidence(pack)
        if pack["evidence_hash"] != frozen_hash:
            raise ValueError("Answerer changed shared evidence")
        answer["timing_ms"].update(retrieval_ms=round(retrieval_ms, 3), evidence_pack_ms=round(evidence_ms - retrieval_ms, 3))
        answer["timing_ms"]["query_end_to_end_ms"] = round(evidence_ms + answer["timing_ms"].get("answer_ms", 0), 3)
        answers.append(answer)
    return pack, {a["method"]: a for a in answers}, ranked


def automatic_checks(answer: dict, pack: dict, reference: dict) -> dict:
    targets = reference["expected_numbers"] + [
        {"name": c["output"], "value": c["expected"], "unit": c["unit"], "absolute_tolerance": c["absolute_tolerance"]} for c in reference["calculations"]
    ]
    if answer["status"] in {"unavailable", "error"} or (answer["method"] == "rag" and answer.get("execution_kind") != "live"):
        return {"numeric": "UNKNOWN" if targets else "NOT_APPLICABLE", "numeric_targets": [],
                "citation_integrity": "UNKNOWN", "expected_response": "UNKNOWN"}
    try:
        validate_answer(answer, pack)
        integrity = "PASS"
    except (ValueError, KeyError, TypeError):
        integrity = "FAIL"
    expected_abstention = bool(reference["abstention"])
    response = "PASS" if (answer["status"] == "abstained") == expected_abstention else "FAIL"
    observed = answer["numbers"] + [{"name": c["output"], "value": c["value"], "unit": c["unit"]} for c in answer["calculations"]]
    details = []
    for target in targets:
        named = [n for n in observed if same_metric_name(target["name"], n["name"])]
        status = "UNKNOWN" if not named else "FAIL"
        if any((converted := value_in_unit(n["value"], n["unit"], target["unit"])) is not None
               and abs(converted - decimal(target["value"])) <= decimal(target["absolute_tolerance"]) for n in named):
            status = "PASS"
        details.append({"target": target, "status": status})
    numeric = ("NOT_APPLICABLE" if not details else
               "FAIL" if any(d["status"] == "FAIL" for d in details) else
               "UNKNOWN" if any(d["status"] == "UNKNOWN" for d in details) else "PASS")
    if details and answer["status"] == "abstained" and not expected_abstention:
        numeric = "FAIL"
    return {"numeric": numeric, "numeric_targets": details, "citation_integrity": integrity, "expected_response": response,
            "semantic_support": "UNKNOWN; quote validity and numeric matches do not prove entailment"}


def run_summary(cases: list[dict]) -> dict:
    summary = {"cases": len(cases), "methods": {}}
    for method in ("structured", "rag"):
        answers = [c["answers"][method] for c in cases]
        numeric_target_cases = [
            c for c in cases
            if c.get("grading_reference", {}).get("expected_numbers") or c.get("grading_reference", {}).get("calculations")
        ]
        statuses = dict(Counter(a["status"] for a in answers))
        answered = [a for a in answers if a["status"] == "answered" and (method == "structured" or a.get("execution_kind") == "live")]
        # Never report unavailable local dispatch time as live LLM latency.
        measurable = answers if method == "structured" else [a for a in answers if a.get("provider_attempted") and a.get("execution_kind") == "live"]
        costs = [decimal(a["provider_cost_usd"]) for a in answers if a["provider_cost_usd"] is not None and (method == "structured" or a.get("execution_kind") == "live")]
        summary["methods"][method] = {
            "statuses": statuses, "answer_coverage": f"{len(answered)}/{len(cases)}",
            "numeric_auto_passes": sum(c["automatic"][method]["numeric"] == "PASS" and c["answers"][method]["status"] == "answered" for c in numeric_target_cases),
            "numeric_target_cases": len(numeric_target_cases),
            "numeric_not_applicable_cases": len(cases) - len(numeric_target_cases),
            "median_query_end_to_end_ms": round(statistics.median(a["timing_ms"]["query_end_to_end_ms"] for a in measurable), 3) if measurable else None,
            "latency_sample_size": len(measurable), "known_provider_cost_usd": str(sum(costs)) if costs else None,
            "cost_unknown_cases": len(answers) - len(costs), "answer_quality": "UNKNOWN until source reviews are complete",
        }
    summary["retrieval_only"] = {
        "any_expected_source_at_10": sum(c["retrieval"]["any_source_recalled"] for c in cases),
        "all_expected_sources_at_10": sum(c["retrieval"]["all_sources_recalled"] for c in cases),
        "denominator": len(cases), "limits": "Source presence does not establish row retrieval, correctness, support or abstention",
    }
    return summary


def load_run(path: Path) -> dict:
    run = strict_json((path / "run.json").read_bytes())
    if fingerprint({k: v for k, v in run.items() if k != "run_hash"}) != run.get("run_hash"):
        raise ValueError("Run record hash mismatch")
    for row in run["cases"]:
        verify_evidence(row["evidence"])
        if any(a["evidence_hash"] != row["evidence"]["evidence_hash"] for a in row["answers"].values()):
            raise ValueError("Answer/evidence parity mismatch")
    return run


def evaluate(release: Path, split: str, run_id: str | None = None, provider=None, frozen: Path | None = None) -> Path:
    rows = [json.loads(line) for line in BENCHMARK.read_text().splitlines()]
    if Counter(r["split"] for r in rows) != {"development": 21, "holdout": 9}:
        raise ValueError("Benchmark partition changed")
    benchmark_hash = sha256(BENCHMARK.read_bytes())
    hashes = code_hashes()
    if split == "holdout":
        if provider is not None or frozen is None:
            raise ValueError("Exposed regression requires a frozen development run and offline mode")
        development = load_run(frozen)
        if development["split"] != "development" or development["code_sha256"] != hashes or development["benchmark_sha256"] != benchmark_hash or development["config"]["pack_version"] != PACK_VERSION:
            raise ValueError("Code/benchmark/evidence policy changed after development freeze")
    manifest, chunks, index, setup_ms = prepare(release)
    if split == "holdout" and development["chunks_sha256"] != manifest["chunks_sha256"]:
        raise ValueError("Corpus changed after development freeze")
    results = []
    for row in (r for r in rows if r["split"] == split):
        # Exactly these two input fields enter retrieval and answer generation.
        pack, answers, ranked = generate_pair(manifest, chunks, index, row["ticker"], row["question"], provider)
        wanted = {e["source_id"] for e in row["evidence"]}
        found = {c["source_id"] for c in ranked}
        results.append({
            "case_id": row["case_id"], "ticker": row["ticker"], "category": row["category"],
            "question": row["question"], "evidence": pack, "answers": answers,
            "retrieval": {"required_source_ids": sorted(wanted), "raw_source_ids": sorted(found),
                          "packed_source_ids": sorted(pack["sources"]), "any_source_recalled": bool(wanted & found), "all_sources_recalled": wanted <= found},
            "automatic": {m: automatic_checks(a, pack, row) for m, a in answers.items()},
            "grading_reference": row,  # stored AFTER answers, for source reviewers
        })
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
        raise ValueError("Unsafe run ID")
    run = {
        "experiment_version": EXPERIMENT_VERSION, "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "split": split,
        "holdout_exposure": "Exposed regression only; no tuning or blind-test claim" if split == "holdout" else None,
        "frozen_development_run_hash": development["run_hash"] if split == "holdout" else None,
        "corpus_version": manifest["corpus_version"], "chunks_sha256": manifest["chunks_sha256"],
        "source_seed_sha256": sha256(SEED.read_bytes()), "manifest_sha256": sha256((release / "manifest.json").read_bytes()),
        "benchmark_sha256": benchmark_hash, "code_sha256": hashes, "setup_ms": setup_ms,
        "config": {"pack_version": PACK_VERSION, "top_k": TOP_K, "baseline_version": BASELINE_VERSION, "live": provider is not None},
        "provider": provider.run_state() if provider else None, "cases": results, "summary": run_summary(results),
        "limits": "Source review required. Offline fixtures/replay are contract tests and have no live quality scores. Cost excludes local infrastructure; unknown charge/usage remains unknown.",
    }
    run["run_hash"] = fingerprint(run)
    destination = RUNS / run_id
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return destination


def review_template(run: dict, reviewer: str) -> dict:
    return {"reviewer": reviewer, "run_hash": run["run_hash"], "cases": {
        c["case_id"]: {method: {
            "checks": {d: "UNKNOWN" for d in DIMENSIONS}, "rationale": "Source review pending",
            "claims": {claim["claim_id"]: {"checks": {d: "UNKNOWN" for d in CLAIM_DIMENSIONS}, "rationale": "Source review pending"} for claim in c["answers"][method]["claims"]},
        } for method in ("structured", "rag")} for c in run["cases"]
    }}


def reviewed_grade(run: dict, reviews: list[dict]) -> dict:
    if len(reviews) != 2 or {r.get("reviewer") for r in reviews} != {"root", "verifier"} or any(r.get("run_hash") != run["run_hash"] for r in reviews):
        raise ValueError("Need root and fresh verifier reviews for this exact run")
    graded = []
    for row in run["cases"]:
        result = {"case_id": row["case_id"], "category": row["category"], "methods": {}}
        for method, answer in row["answers"].items():
            records = [r.get("cases", {}).get(row["case_id"], {}).get(method, {}) for r in reviews]
            values, disagreements, agreed = [], [], {}
            for dimension in DIMENSIONS:
                states = [r.get("checks", {}).get(dimension, "UNKNOWN") for r in records]
                if any(s not in {"PASS", "FAIL", "UNKNOWN"} for s in states):
                    raise ValueError("Invalid review status")
                if len(set(states)) > 1:
                    disagreements.append(dimension)
                agreed[dimension] = states[0] if len(set(states)) == 1 else "UNKNOWN"
                values.extend(states)
            for claim in answer["claims"]:
                for dimension in CLAIM_DIMENSIONS:
                    states = [r.get("claims", {}).get(claim["claim_id"], {}).get("checks", {}).get(dimension, "UNKNOWN") for r in records]
                    if any(s not in {"PASS", "FAIL", "UNKNOWN"} for s in states):
                        raise ValueError("Invalid claim review status")
                    values.extend(states)
                    if len(set(states)) > 1:
                        disagreements.append(claim["claim_id"] + ":" + dimension)
            auto = row["automatic"][method]
            if answer["status"] in {"error", "unavailable"} or (method == "rag" and answer.get("execution_kind") != "live"):
                verdict = "UNKNOWN"
            elif disagreements:
                verdict = "UNKNOWN"
            elif "FAIL" in values or "FAIL" in (auto["numeric"], auto["citation_integrity"], auto["expected_response"]):
                verdict = "FAIL"
            elif disagreements or "UNKNOWN" in values or auto["numeric"] == "UNKNOWN":
                verdict = "UNKNOWN"
            else:
                verdict = "PASS"
            result["methods"][method] = {"verdict": verdict, "checks": agreed, "review_complete": "UNKNOWN" not in values, "disagreements": disagreements, "reviews": records}
        graded.append(result)
    summary = {}
    for method in ("structured", "rag"):
        values = [c["methods"][method] for c in graded]
        summary[method] = {
            "verdicts": dict(Counter(v["verdict"] for v in values)), "denominator": len(values),
            "reviewed_cases": sum(v["review_complete"] and c["answers"][method]["status"] not in {"error", "unavailable"} and (method == "structured" or c["answers"][method].get("execution_kind") == "live") for c, v in zip(run["cases"], values)),
            "answered_cases": sum(c["answers"][method]["status"] == "answered" and (method == "structured" or c["answers"][method].get("execution_kind") == "live") for c in run["cases"]),
            "correct_abstentions": sum(c["grading_reference"]["abstention"] is not None and c["answers"][method]["status"] == "abstained" and g["methods"][method]["verdict"] == "PASS" for c, g in zip(run["cases"], graded)),
            "excessive_abstentions": sum(c["grading_reference"]["abstention"] is None and c["answers"][method]["status"] == "abstained" for c in run["cases"]),
            "by_category": {category: dict(Counter(c["methods"][method]["verdict"] for c in graded if c["category"] == category)) for category in sorted({c["category"] for c in graded})},
            "case_dimensions": {dimension: {"counts": dict(Counter(v["checks"][dimension] for v in values)), "denominator": len(values)} for dimension in DIMENSIONS},
        }
        abstained = [(row, assessed["methods"][method]["checks"]) for row, assessed in zip(run["cases"], graded)
                     if row["answers"][method]["status"] == "abstained"
                     and (method == "structured" or row["answers"][method].get("execution_kind") == "live")]
        summary[method]["abstention_review"] = {
            "denominator": len(abstained),
            "safe_given_bundle": sum(checks["abstention"] == "PASS" for _, checks in abstained),
            "excessive_given_bundle": sum(checks["abstention"] == "FAIL" for _, checks in abstained),
            "unknown": sum(checks["abstention"] == "UNKNOWN" for _, checks in abstained),
            "correct_expected_abstentions": sum(bool(row["grading_reference"]["abstention"]) and checks["correctness"] == checks["abstention"] == "PASS" for row, checks in abstained),
            "answerable_pipeline_misses": sum(not row["grading_reference"]["abstention"] and checks["correctness"] == "FAIL" and checks["abstention"] == "PASS" for row, checks in abstained),
        }
        claims = []
        for row, assessed in zip(run["cases"], graded):
            if method == "rag" and row["answers"][method].get("execution_kind") != "live":
                continue
            records = assessed["methods"][method]["reviews"]
            for claim in row["answers"][method]["claims"]:
                claims.append({dimension: (
                    states[0] if len(set(states := [r.get("claims", {}).get(claim["claim_id"], {}).get("checks", {}).get(dimension, "UNKNOWN") for r in records])) == 1 else "UNKNOWN"
                ) for dimension in CLAIM_DIMENSIONS})
        summary[method]["claim_dimensions"] = {dimension: {"counts": dict(Counter(c[dimension] for c in claims)), "denominator": len(claims)} for dimension in CLAIM_DIMENSIONS}
        summary[method]["answerable_cases"] = sum(c["grading_reference"]["abstention"] is None for c in run["cases"])
    return {"grading_version": "source-review-v2", "grading_code_sha256": {name: sha256((HERE / name).read_bytes()) for name in ("qa_compare.py", "qa_contract.py")},
            "run_hash": run["run_hash"], "review_hashes": [fingerprint(r) for r in reviews], "summary": summary, "cases": graded,
            "limits": "Human/source-review judgments, not blind generalization or an automated entailment guarantee. Review disagreement or incomplete coverage cannot count as a pass."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["ask", "evaluate", "review-template", "grade"])
    parser.add_argument("--release", type=Path, default=LOCAL / "releases" / json.loads(SEED.read_text())["corpus_version"])
    parser.add_argument("--ticker", choices=["NBIS", "IREN", "ASTS"])
    parser.add_argument("--question")
    parser.add_argument("--split", choices=["development", "holdout"], default="development")
    parser.add_argument("--run-id")
    parser.add_argument("--run", type=Path)
    parser.add_argument("--freeze-run", type=Path)
    parser.add_argument("--reviewer", choices=["root", "verifier"])
    parser.add_argument("--root-review", type=Path)
    parser.add_argument("--verifier-review", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--live-config", type=Path)
    args = parser.parse_args()
    try:
        provider = None
        if args.live:
            if args.action not in {"ask", "evaluate"} or not args.live_config or args.split != "development":
                parser.error("Live mode requires ask/development evaluate and an explicit reviewed config")
            provider = ResponsesProvider(strict_json(args.live_config.read_bytes()), network_enabled=True)
        elif args.live_config:
            parser.error("--live-config requires explicit --live")
        if args.action == "ask":
            if not args.ticker or not args.question:
                parser.error("ask requires --ticker and --question")
            manifest, chunks, index, setup = prepare(args.release)
            pack, answers, _ = generate_pair(manifest, chunks, index, args.ticker, args.question, provider)
            print(json.dumps({"setup_ms": setup, "evidence_hash": pack["evidence_hash"], "answers": answers, "provider": provider.run_state() if provider else None}, indent=2))
        elif args.action == "evaluate":
            destination = evaluate(args.release, args.split, args.run_id, provider, args.freeze_run)
            print(destination)
            print(json.dumps(load_run(destination)["summary"], indent=2))
        else:
            if not args.run:
                parser.error("review-template/grade require --run")
            run = load_run(args.run)
            if args.action == "review-template":
                if not args.reviewer:
                    parser.error("review-template requires --reviewer")
                path = args.run / (args.reviewer + "-review.json")
                with path.open("x") as f:
                    f.write(json.dumps(review_template(run, args.reviewer), indent=2) + "\n")
            else:
                if not args.root_review or not args.verifier_review:
                    parser.error("grade requires both review files")
                reviews = [strict_json(p.read_bytes()) for p in (args.root_review, args.verifier_review)]
                report = reviewed_grade(run, reviews)
                path = args.run / ("graded-" + fingerprint(report)[:12] + ".json")
                with path.open("x") as f:
                    f.write(json.dumps(report, indent=2) + "\n")
                print(json.dumps(report["summary"], indent=2))
            print(path)
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        # No raw exceptions/config/body output, including potential credentials.
        print("Q&A operation failed: invalid/missing configuration, evidence, review, or run artifact.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
