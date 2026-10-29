"""Retrieval-only paired evaluation on the immutable SEC pilot, with no LLM calls.

The parent scores source-reviewed requirements AFTER worker retrieval. Workers
receive only case handles, tickers and questions, never labels or answer keys.
Separate persistent processes isolate peak RSS; requests alternate sequentially.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import re
import resource
import select
import statistics
import subprocess
import sys
import time

from corpus import HERE, LOCAL, SEED
from hybrid_retrieval import (CONDITIONAL_FLOOR_POLICY, FLOOR_POLICIES, PASSAGE_WINDOW_CHOICES, POLICY, VERSION,
                              HybridIndex, LocalEncoder, file_hash, no_network,
                              validate_floor_policy, validate_fusion_weights, validate_lexical_floor,
                              validate_passage_window_tokens)
from qa_baseline import BENCHMARK, BASELINE_VERSION, LexicalIndex, load_release
from qa_contract import canonical, fingerprint
from rag_experiment import (MAX_EVIDENCE_BYTES, MAX_REQUEST_BYTES, PACK_VERSION, PACKING_VERSIONS,
                            build_evidence, generation_input, packing_policy_version)


LABELS = HERE / "retrieval_relevance.json"
DEPENDENCIES = HERE / "requirements-embedding-experiment.txt"
LEXICAL_TAIL_DEPTH_CHOICES = (10, 15)
BASELINE_RUN = LOCAL / "answer_runs/live-development-2026-10-25-v14-01/run.json"
CODE = ("corpus.py", "qa_baseline.py", "rag_experiment.py", "qa_compare.py", "qa_contract.py",
        "fact_baseline.py", "fact_specs.json", "llm_provider.py", "hybrid_retrieval.py", "retrieval_compare.py")
TUNING_WEIGHTS = ((0.75, 0.25), (0.50, 0.50), (0.25, 0.75))
PARAPHRASE_PROBES = (
    {"case_id": "probe-nbis-arr", "ticker": "NBIS",
     "question": "How does Nebius define ARR in its Q3 2024 release, and should it be treated as revenue already recognized?"},
    {"case_id": "probe-iren-capacity", "ticker": "IREN",
     "question": "What mining capacity was installed at October 2024 month-end, and how does it differ from the monthly average operating rate?"},
    {"case_id": "probe-asts-service", "ticker": "ASTS",
     "question": "How does the 2024 Form 10-K say AST SpaceMobile will connect everyday mobile phones?"},
)


def hashes() -> dict:
    return {name: file_hash(HERE / name) for name in CODE}


def load_labels(path: Path, manifest: dict, chunks: list[dict], rows: list[dict]) -> dict:
    """Validate exact source locators and a reviewed, development-only freeze."""
    labels = json.loads(path.read_text())
    if fingerprint({k: v for k, v in labels.items() if k != "annotation_hash"}) != labels.get("annotation_hash"):
        raise ValueError("Annotation hash mismatch")
    if labels.get("schema_version") != "retrieval-relevance-v1" or labels.get("review_status") != "source-reviewed":
        raise ValueError("Source-reviewed annotations are required before comparison")
    if (labels["corpus_version"], labels["chunks_sha256"], labels["benchmark_sha256"], labels["split"]) != (
            manifest["corpus_version"], manifest["chunks_sha256"], file_hash(BENCHMARK), "development"):
        raise ValueError("Annotations belong to different frozen inputs")
    by_case = {c["case_id"]: c for c in labels["cases"]}
    if len(by_case) != 21 or len(labels["cases"]) != 21 or set(by_case) != {r["case_id"] for r in rows}:
        raise ValueError("Annotations must cover exactly the development cases")
    if Counter(c["kind"] for c in by_case.values()) != {"financial": 14, "narrative": 5, "abstention_scope": 2}:
        raise ValueError("Annotation denominators changed")
    docs = {d["source_id"]: d for d in manifest["documents"]}
    passages = {c["chunk_id"]: c for c in chunks}
    for row in rows:
        case = by_case[row["case_id"]]
        if case["ticker"] != row["ticker"] or case["question_sha256"] != fingerprint_question(row["question"]):
            raise ValueError("Annotation question/company changed")
        if case["expected_source_ids"] != sorted({e["source_id"] for e in row["evidence"]}):
            raise ValueError("Expected-source references changed")
        ids = [r["requirement_id"] for r in case["requirements"]]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("Missing/duplicate evidence requirements")
        for requirement in case["requirements"]:
            if requirement.get("review_status") not in {"PASS", "UNKNOWN"} or not requirement["alternatives"]:
                raise ValueError("Requirement review is incomplete or malformed")
            for alternative in requirement["alternatives"]:
                source = docs[alternative["source_id"]]
                if source["ticker"] != case["ticker"]:
                    raise ValueError("Alternative changes company")
                if any(key not in source or source[key] != value for key, value in alternative["source_metadata"].items()):
                    raise ValueError("Annotation source identity/basis/time mismatch")
                if alternative["benchmark_vintage_date"] != source.get("benchmark_vintage_date"):
                    raise ValueError("Benchmark vintage must remain distinct from issuer publication")
                if not alternative["spans"] and requirement["kind"] != "scope_source":
                    raise ValueError("Answer evidence cannot consist of metadata alone")
                roles = set()
                for span in alternative["spans"]:
                    chunk = passages[span["chunk_id"]]
                    if chunk["source_id"] != source["source_id"] or chunk["text_sha256"] != span["chunk_text_sha256"]:
                        raise ValueError("Span belongs to a different passage/source")
                    if (type(span["start"]) is not int or type(span["end"]) is not int
                            or not 0 <= span["start"] < span["end"] <= len(chunk["text"])
                            or chunk["text"][span["start"]:span["end"]] != span["quote"]):
                        raise ValueError("Annotated quote is not exact source text")
                    roles.update(span["roles"])
                if requirement["kind"] == "financial_fact" and not {"row", "period", "units"} <= roles:
                    raise ValueError("Financial alternative omits row/period/scale requirements")
    return labels


def fingerprint_question(question: str) -> str:
    import hashlib
    return hashlib.sha256(question.encode()).hexdigest()


def load_reference_run(path: Path) -> dict:
    """Use the existing run/evidence checksum checks before trusting v14 parity."""
    from qa_compare import load_run
    return load_run(path.parent)


def ranked_references(ranked: list[dict]) -> list[dict]:
    """Persist original locators/scores; raw contact text stays in the corpus."""
    fields = ("chunk_id", "source_id", "ticker", "block_start", "block_end", "text_sha256",
              "score", "lexical_rank", "dense_rank", "lexical_protected")
    return [{k: row[k] for k in fields if k in row} for row in ranked]


def alternative_present(alternative: dict, passages: dict, sources: dict, roles: set | None = None) -> bool:
    sid = alternative["source_id"]
    if sid not in sources or not any(p["source_id"] == sid for p in passages.values()):
        return False
    if any(k not in sources[sid] or sources[sid][k] != v for k, v in alternative["source_metadata"].items()):
        return False
    spans = [s for s in alternative["spans"] if roles is None or roles & set(s["roles"])]
    if roles is not None and not spans:
        return False
    return all(s["chunk_id"] in passages and passages[s["chunk_id"]]["source_id"] == sid
               and s["quote"] in passages[s["chunk_id"]]["text"] for s in spans)


def assess_evidence(case: dict, supplied: list[dict], sources: dict) -> dict:
    passages = {p["chunk_id"]: p for p in supplied}
    groups = []
    for requirement in case["requirements"]:
        alternatives = requirement["alternatives"]
        matched = [i for i, alt in enumerate(alternatives) if alternative_present(alt, passages, sources)]
        reviewed = requirement["review_status"] == "PASS"
        status = "UNKNOWN" if not reviewed else "PASS" if matched else "FAIL"
        result = {"requirement_id": requirement["requirement_id"], "kind": requirement["kind"],
                  "critical": requirement["critical"], "status": status, "matching_alternatives": matched}
        if requirement["kind"] == "financial_fact":
            for label, roles in (("row", {"row"}), ("row_header", {"row", "period", "units"})):
                result[label] = "UNKNOWN" if not reviewed else "PASS" if any(
                    alternative_present(alt, passages, sources, roles) for alt in alternatives) else "FAIL"
        groups.append(result)
    def combined(states):
        return "FAIL" if "FAIL" in states else "UNKNOWN" if "UNKNOWN" in states else "PASS"
    financial = [g for g in groups if g["kind"] == "financial_fact"]
    return {"all_required": combined([g["status"] for g in groups]), "requirements": groups,
            "financial_rows": combined([g["row"] for g in financial]) if financial else "NOT_APPLICABLE",
            "financial_rows_headers": combined([g["row_header"] for g in financial]) if financial else "NOT_APPLICABLE"}


def score_case(case: dict, result: dict, manifest: dict) -> dict:
    sources = {d["source_id"]: d for d in manifest["documents"]}
    expected = set(case["expected_source_ids"])
    raw_sources = {p["source_id"] for p in result["ranked"]}
    pack = result["pack"]
    packed_sources = {p["source_id"] for p in pack["passages"]}
    return {"case_id": case["case_id"], "kind": case["kind"],
            "literal_sources": {"expected": sorted(expected), "raw": sorted(raw_sources), "packed": sorted(packed_sources),
                                "raw_any": bool(expected & raw_sources), "raw_all": expected <= raw_sources,
                                "packed_any": bool(expected & packed_sources), "packed_all": expected <= packed_sources},
            "raw": assess_evidence(case, result["ranked"], sources),
            "packed": assess_evidence(case, pack["passages"], pack["sources"])}


def summarize(scores: list[dict]) -> dict:
    summary = {"cases": len(scores), "literal_source_presence": {key: {"count": sum(c["literal_sources"][key] for c in scores),
               "denominator": len(scores)} for key in ("raw_any", "raw_all", "packed_any", "packed_all")}}
    for stage in ("raw", "packed"):
        summary[stage] = {}
        for key, cases, metric in (
                ("financial_rows", [c for c in scores if c["kind"] == "financial"], "financial_rows"),
                ("financial_rows_headers", [c for c in scores if c["kind"] == "financial"], "financial_rows_headers"),
                ("financial_complete", [c for c in scores if c["kind"] == "financial"], "all_required"),
                ("narrative_complete", [c for c in scores if c["kind"] == "narrative"], "all_required"),
                ("answerable_complete", [c for c in scores if c["kind"] != "abstention_scope"], "all_required"),
                ("abstention_scope", [c for c in scores if c["kind"] == "abstention_scope"], "all_required")):
            summary[stage][key] = {"count": sum(c[stage][metric] == "PASS" for c in cases), "denominator": len(cases),
                                   "unknown": sum(c[stage][metric] == "UNKNOWN" for c in cases)}
        groups = [g for c in scores if c["kind"] != "abstention_scope" for g in c[stage]["requirements"]]
        summary[stage]["answerable_requirements"] = {"count": sum(g["status"] == "PASS" for g in groups),
                                                     "denominator": len(groups), "unknown": sum(g["status"] == "UNKNOWN" for g in groups)}
    return summary


def improvement_gate(lexical: list[dict], hybrid: list[dict]) -> dict:
    """A predeclared pilot decision rule, not a significance/generalization test."""
    left, right = ({c["case_id"]: c for c in rows} for rows in (lexical, hybrid))
    if set(left) != set(right):
        raise ValueError("Unpaired comparison")
    answerable = [cid for cid, c in left.items() if c["kind"] != "abstention_scope"]
    gains = [cid for cid in answerable if left[cid]["packed"]["all_required"] != "PASS" and right[cid]["packed"]["all_required"] == "PASS"]
    losses = [cid for cid in answerable if left[cid]["packed"]["all_required"] == "PASS" and right[cid]["packed"]["all_required"] != "PASS"]
    critical_losses, unknown = [], []
    for cid in left:
        previous = {g["requirement_id"]: g for g in left[cid]["packed"]["requirements"]}
        current = {g["requirement_id"]: g for g in right[cid]["packed"]["requirements"]}
        if set(previous) != set(current):
            raise ValueError("Evidence requirements changed between arms")
        for gid, group in previous.items():
            if group["critical"] and group["status"] == "PASS" and current[gid]["status"] != "PASS":
                critical_losses.append(cid + ":" + gid)
            if "UNKNOWN" in {group["status"], current[gid]["status"]}:
                unknown.append(cid + ":" + gid)
    missing = [cid for cid in answerable if left[cid]["packed"]["all_required"] != "PASS"]
    enough = len(gains) >= 2 or (len(missing) == 1 and gains == missing)
    advance = bool(enough and not losses and not critical_losses and not unknown)
    required = 1 if len(missing) == 1 else 2
    failed = (["insufficient_newly_complete"] if not enough else [])
    failed += [name for name, values in (("lost_complete", losses),
               ("critical_requirement_losses", critical_losses), ("unknown", unknown)) if values]
    return {"meaningful_evidence_gain": advance, "newly_complete": gains, "lost_complete": losses,
            "critical_requirement_losses": critical_losses, "unknown": unknown,
            "reference": "lexical_plus_table", "required_newly_complete": required,
            "failed_conditions": failed,
            "baseline_incomplete": missing, "paid_generation_authorized": False,
            "next": "Evidence gate met; access, current rates, matched generation and explicit paid-stage authorization still required." if advance else
                    f"Do not run paid generation: {len(gains)} newly complete answerable cases versus {required} required against lexical-plus-table; failed conditions: {', '.join(failed)}. Also inspect regressions against the hybrid control separately. The four v14 quote errors occur after retrieval."}


def query_result(index, manifest, chunks, query, weights=None, lexical_floor=0, floor_policy="fixed",
                 packing_policy="baseline", lexical_tail_depth=10):
    start = time.perf_counter()
    if type(lexical_tail_depth) is not int or lexical_tail_depth not in LEXICAL_TAIL_DEPTH_CHOICES:
        raise ValueError("Lexical tail depth must be 10 or 15")
    uses_tail = packing_policy == "context-anchored-tail-v1"
    if (lexical_tail_depth > POLICY["top_k"]) != uses_tail:
        raise ValueError("Depth 15 requires the lexical-tail packing policy")
    decision = None
    if weights is None and lexical_floor == 0 and floor_policy == "fixed" and lexical_tail_depth == 10:
        ranked = index.search(query["ticker"], query["question"], 10)
    else:
        result = index.search_with_diagnostics(query["ticker"], query["question"], 10,
                                               weights=weights if weights is not None else (0.5, 0.5),
                                               lexical_floor=lexical_floor, floor_policy=floor_policy)
        ranked, decision = result["ranked"], result["lexical_floor_decision"]
    lexical_tail = []
    if uses_tail:
        top_ids = {record["chunk_id"] for record in ranked}
        lexical_tail = [record for record in index.lexical.search(
            query["ticker"], query["question"], lexical_tail_depth)[POLICY["top_k"]:]
            if record["chunk_id"] not in top_ids]
    originals = {c["chunk_id"]: c for c in chunks}
    if len(ranked) > 10 or len({c["chunk_id"] for c in ranked}) != len(ranked):
        raise ValueError("Retriever changed the original passage/result contract")
    for record in ranked:
        original = originals.get(record["chunk_id"])
        if not original or record["ticker"] != query["ticker"] or any(
                record.get(k) != original.get(k) for k in
                ("source_id", "ticker", "text", "text_sha256", "block_start", "block_end")):
            raise ValueError("Retriever changed corpus text or company/source identity")
    for record in lexical_tail:
        original = originals.get(record["chunk_id"])
        if not original or record.get("ticker") != query["ticker"] or any(
                record.get(k) != original.get(k) for k in
                ("source_id", "ticker", "text", "text_sha256", "block_start", "block_end")):
            raise ValueError("Lexical tail changed corpus text or company/source identity")
    searched = time.perf_counter()
    pack_options = {}
    if packing_policy != "baseline":
        pack_options["packing_policy"] = packing_policy
    if uses_tail:
        pack_options["lexical_tail"] = lexical_tail
    pack = build_evidence(manifest, chunks, ranked, query["ticker"], query["question"], **pack_options)
    finished = time.perf_counter()
    # The existing request projection can be measured without invoking a model.
    request_bytes = len(canonical(generation_input(pack)))
    if len(canonical(pack)) > MAX_EVIDENCE_BYTES or request_bytes > MAX_REQUEST_BYTES:
        raise ValueError("Evidence/request byte budget exceeded")
    return {"case_id": query["case_id"], "ranked": ranked, "pack": pack,
            **({"lexical_floor_decision": decision} if decision is not None else {}),
            "evidence_bytes": len(canonical(pack)), "request_projection_bytes": request_bytes,
            "search_ms": (searched - start) * 1000, "pack_ms": (finished - searched) * 1000,
            "query_ms": (finished - start) * 1000}


def peak_rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1024)


def worker(args):
    """Private line protocol: readiness, one warmup, then sequential measurements."""
    def emit(value):
        print(json.dumps(value, separators=(",", ":"), ensure_ascii=False), flush=True)
    stage = "load_release"
    try:
        with no_network():
            start = time.perf_counter()
            manifest, chunks = load_release(args.release)
            verify_ms = (time.perf_counter() - start) * 1000
            queries = json.loads(args.queries.read_text())
            if (len(queries) not in {21, 3}
                    or any(set(q) != {"case_id", "ticker", "question"} for q in queries)
                    or len({q["case_id"] for q in queries}) != len(queries)
                    or (len(queries) == 3 and any(not q["case_id"].startswith("probe-") for q in queries))):
                raise ValueError("Workers receive 21 development questions or three labeled diagnostic probes")
            stage = "index_setup"
            start = time.perf_counter()
            if args.arm == "lexical":
                index = LexicalIndex(chunks)
                setup = {"model_load_ms": 0, "passage_encoding_ms": 0, "embedding_api_cost_usd": "0"}
            else:
                encoder_start = time.perf_counter()
                encoder = LocalEncoder(args.experiment_dir / "model", args.experiment_dir / "model-artifacts.json", DEPENDENCIES,
                                       passage_window_tokens=args.passage_window_tokens)
                model_ms = (time.perf_counter() - encoder_start) * 1000
                index = HybridIndex(chunks, encoder, args.experiment_dir / "cache")
                setup = {"model_load_ms": model_ms, **index.timing_ms, "cache_hit": index.cache_hit,
                         "cache_key": index.cache_key, "cache_path": str(index.cache_path), "window_count": len(index.layout),
                         "passage_window_tokens": encoder.passage_window_tokens,
                         "original_passages": len(chunks), "encoder": encoder.identity, "embedding_api_cost_usd": "0"}
            setup.update(index_setup_ms=(time.perf_counter() - start) * 1000, common_verification_ms=verify_ms)
            emit({"status": "ready", "setup": setup, "peak_rss_bytes": peak_rss_bytes()})
            for command in sys.stdin:
                action = json.loads(command)["action"]
                if action == "stop":
                    break
                if action not in {"warm", "measure"}:
                    raise ValueError("Unknown worker command")
                stage = action
                cpu = time.process_time()
                weights = (args.lexical_weight, args.dense_weight) if args.arm == "hybrid" else None
                lexical_floor = args.lexical_floor if args.arm == "hybrid" else 0
                floor_policy = args.floor_policy if args.arm == "hybrid" else "fixed"
                packing_policy = args.packing_policy if args.arm == "hybrid" else "baseline"
                lexical_tail_depth = args.lexical_tail_depth if args.arm == "hybrid" else POLICY["top_k"]
                values = [query_result(index, manifest, chunks, q, weights, lexical_floor,
                                       floor_policy, packing_policy, lexical_tail_depth) for q in queries]
                emit({"status": "ok", "action": action, "cases": values if action == "measure" else [],
                      "cpu_ms": (time.process_time() - cpu) * 1000, "peak_rss_bytes": peak_rss_bytes()})
    except Exception as error:
        unavailable = isinstance(error, (ImportError, FileNotFoundError))
        emit({"status": "unavailable" if unavailable else "error", "stage": stage,
              "code": "missing_local_runtime_or_artifacts" if unavailable else "invalid_runtime_cache_or_evidence",
              "exception_class": type(error).__name__})


def read_message(process, timeout=900):
    if not select.select([process.stdout], [], [], timeout)[0]:
        raise ValueError("Retrieval worker timed out")
    line = process.stdout.readline()
    if not line:
        raise ValueError("Retrieval worker exited without a result; inspect local worker log")
    return json.loads(line)


def send(process, action):
    process.stdin.write(json.dumps({"action": action}) + "\n")
    process.stdin.flush()
    return read_message(process)


def percentile95(values):
    return sorted(values)[max(0, math.ceil(0.95 * len(values)) - 1)]


def compare(args):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.run_id or ""):
        raise ValueError("A safe unique run ID is required")
    weights = validate_fusion_weights(args.lexical_weight, args.dense_weight)
    lexical_floor = validate_lexical_floor(getattr(args, "lexical_floor", 0), POLICY["top_k"])
    floor_policy = validate_floor_policy(getattr(args, "floor_policy", "fixed"), lexical_floor)
    passage_window_tokens = validate_passage_window_tokens(
        getattr(args, "passage_window_tokens", POLICY["passage_window_tokens"]))
    packing_policy = getattr(args, "packing_policy", "baseline")
    lexical_tail_depth = getattr(args, "lexical_tail_depth", POLICY["top_k"])
    if type(lexical_tail_depth) is not int or lexical_tail_depth not in LEXICAL_TAIL_DEPTH_CHOICES:
        raise ValueError("Lexical tail depth must be 10 or 15")
    if (lexical_tail_depth > POLICY["top_k"]) != (packing_policy == "context-anchored-tail-v1"):
        raise ValueError("Depth 15 and the lexical-tail packing policy must be selected together")
    hybrid_pack_version = packing_policy_version(packing_policy)
    destination = LOCAL / "retrieval_runs" / args.run_id
    destination.mkdir(parents=True, exist_ok=False)
    manifest, chunks = load_release(args.release)
    rows = [r for r in map(json.loads, BENCHMARK.read_text().splitlines()) if r["split"] == "development"]
    labels = load_labels(args.labels, manifest, chunks, rows)
    baseline = load_reference_run(BASELINE_RUN)
    frozen_hashes = hashes()
    frozen_labels = file_hash(args.labels)
    queries = [{k: r[k] for k in ("case_id", "ticker", "question")} for r in rows]
    query_file = destination / "queries.json"
    query_file.write_bytes(canonical(queries))
    (destination / "annotations.json").write_text(args.labels.read_text())
    processes, logs, arms = {}, {}, {}
    try:
        for arm in (["lexical"] if args.action == "baseline" else ["lexical", "hybrid"]):
            log = (destination / (arm + "-worker.log")).open("w")
            logs[arm] = log
            command = [sys.executable, str(Path(__file__).resolve()), "worker", "--arm", arm,
                       "--release", str(args.release.resolve()), "--queries", str(query_file.resolve()),
                       "--experiment-dir", str(args.experiment_dir.resolve()),
                       "--lexical-weight", str(weights[0]), "--dense-weight", str(weights[1]),
                       "--lexical-floor", str(lexical_floor if arm == "hybrid" else 0),
                       "--floor-policy", floor_policy if arm == "hybrid" else "fixed",
                       "--passage-window-tokens", str(passage_window_tokens if arm == "hybrid" else POLICY["passage_window_tokens"]),
                       "--packing-policy", packing_policy if arm == "hybrid" else "baseline",
                       "--lexical-tail-depth", str(lexical_tail_depth if arm == "hybrid" else POLICY["top_k"])]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
            processes[arm] = process
            ready = read_message(process)
            arms[arm] = {"readiness": ready, "repetitions": []}
            if ready["status"] != "ready":
                continue
            warm = send(process, "warm")
            if warm["status"] != "ok":
                raise ValueError("Worker warmup failed")
            arms[arm]["warmup"] = {k: v for k, v in warm.items() if k != "cases"}
        orders = []
        for repetition in range(5):
            order = ["lexical", "hybrid"] if repetition % 2 == 0 else ["hybrid", "lexical"]
            order = [a for a in order if a in arms and arms[a]["readiness"]["status"] == "ready"]
            orders.append(order)
            for arm in order:
                result = send(processes[arm], "measure")
                if result["status"] != "ok":
                    raise ValueError("Worker measurement failed")
                arms[arm]["repetitions"].append(result)
    finally:
        for process in processes.values():
            if process.poll() is None:
                try:
                    process.stdin.write('{"action":"stop"}\n')
                    process.stdin.flush()
                    process.wait(timeout=5)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    process.terminate()
                    process.wait(timeout=5)
        for log in logs.values():
            log.close()
    if hashes() != frozen_hashes or file_hash(args.labels) != frozen_labels:
        raise ValueError("Code or labels changed during measurement")
    docs = {d["source_id"]: d for d in manifest["documents"]}
    by_case = {c["case_id"]: c for c in labels["cases"]}
    scored, summaries = {}, {}
    for arm, data in arms.items():
        if data["readiness"]["status"] != "ready":
            continue
        first = data["repetitions"][0]["cases"]
        for trial in data["repetitions"][1:]:
            if [(c["case_id"], c["pack"]["evidence_hash"], [r["chunk_id"] for r in c["ranked"]],
                 c.get("lexical_floor_decision")) for c in first] != [
                    (c["case_id"], c["pack"]["evidence_hash"], [r["chunk_id"] for r in c["ranked"]],
                     c.get("lexical_floor_decision")) for c in trial["cases"]]:
                raise ValueError("Rankings/evidence changed across repetitions")
        scored[arm] = [score_case(by_case[c["case_id"]], c, manifest) for c in first]
        summaries[arm] = summarize(scored[arm])
        times = [c["query_ms"] for trial in data["repetitions"] for c in trial["cases"]]
        data["performance"] = {"unique_questions": 21, "repetitions": 5, "observations": len(times),
                               "median_query_ms": statistics.median(times), "p95_query_ms": percentile95(times),
                               "peak_rss_bytes": max(t["peak_rss_bytes"] for t in data["repetitions"]),
                               "measured_cpu_ms": sum(t["cpu_ms"] for t in data["repetitions"]),
                               "per_question_median_ms": {c["case_id"]: statistics.median(trial["cases"][i]["query_ms"] for trial in data["repetitions"]) for i, c in enumerate(first)}}
        data["cases"] = first
        decisions = [c["lexical_floor_decision"] for c in first if "lexical_floor_decision" in c]
        if decisions:
            data["floor_decisions"] = {
                "queries": len(decisions),
                "effective_floor_counts": dict(sorted(Counter(str(d["effective_floor"]) for d in decisions).items())),
                "reason_counts": dict(sorted(Counter(d["reason"] for d in decisions).items()))}
        # Retain raw timing observations without five copies of every passage.
        data["repetitions"] = [{**t, "cases": [{k: c[k] for k in ("case_id", "search_ms", "pack_ms", "query_ms")} for c in t["cases"]]} for t in data["repetitions"]]
    baseline_cases = {c["case_id"]: c for c in baseline["cases"]}
    parity = all(c["pack"] == baseline_cases[c["case_id"]]["evidence"] for c in arms["lexical"]["cases"])
    if not parity:
        raise ValueError("Frozen lexical-plus-table baseline differs from v14")
    gate = improvement_gate(scored["lexical"], scored["hybrid"]) if "hybrid" in scored else {
        "meaningful_evidence_gain": "UNKNOWN", "paid_generation_authorized": False,
        "next": "Hybrid unavailable/not requested; retrieval-only baseline completed. No quality claim or paid calls."}
    differences = []
    if "hybrid" in scored:
        for a, b, sa, sb in zip(arms["lexical"]["cases"], arms["hybrid"]["cases"], scored["lexical"], scored["hybrid"]):
            previous = {g["requirement_id"]: g["status"] for g in sa["packed"]["requirements"]}
            current = {g["requirement_id"]: g["status"] for g in sb["packed"]["requirements"]}
            differences.append({"case_id": a["case_id"], "rankings_changed": [c["chunk_id"] for c in a["ranked"]] != [c["chunk_id"] for c in b["ranked"]],
                                "pack_changed": a["pack"]["evidence_hash"] != b["pack"]["evidence_hash"],
                                "gained_requirements": [g for g in previous if previous[g] != "PASS" and current[g] == "PASS"],
                                "lost_requirements": [g for g in previous if previous[g] == "PASS" and current[g] != "PASS"],
                                "packed_added": sorted({p["chunk_id"] for p in b["pack"]["passages"]} - {p["chunk_id"] for p in a["pack"]["passages"]}),
                                "packed_removed": sorted({p["chunk_id"] for p in a["pack"]["passages"]} - {p["chunk_id"] for p in b["pack"]["passages"]})})
    sizes = {name: sum(p.stat().st_size for p in (args.experiment_dir / name).rglob("*") if p.is_file()) for name in ("model", "cache", "venv")}
    for data in arms.values():
        for case in data.get("cases", []):
            case["ranked"] = ranked_references(case["ranked"])
    report = {"experiment_version": VERSION, "run_id": args.run_id, "created_at": datetime.now(timezone.utc).isoformat(),
              "split": "development", "corpus_version": manifest["corpus_version"], "documents": len(docs), "passages": len(chunks),
              "chunks_sha256": manifest["chunks_sha256"], "benchmark_sha256": file_hash(BENCHMARK), "code_sha256": frozen_hashes,
              "annotation_hash": labels["annotation_hash"], "annotation_file_sha256": frozen_labels,
              "annotation_revision": labels.get("annotation_revision", 1),
              "post_run_annotation_correction": labels.get("post_run_correction"),
              "baseline_version": BASELINE_VERSION, "pack_version": PACK_VERSION, "v14_pack_parity": parity,
              "hybrid_packing_policy": packing_policy, "hybrid_pack_version": hybrid_pack_version,
              "hybrid_lexical_tail_depth": lexical_tail_depth,
              "baseline_reference_run_hash": baseline["run_hash"],
              "policy": {**POLICY, "passage_window_tokens": passage_window_tokens},
              "hybrid_fusion_weights": list(weights),
              "hybrid_passage_window_tokens": passage_window_tokens,
              "hybrid_lexical_floor": lexical_floor,
              "hybrid_floor_policy": floor_policy,
              "conditional_floor_policy": CONDITIONAL_FLOOR_POLICY if floor_policy != "fixed" else None,
              "evidence_byte_cap": MAX_EVIDENCE_BYTES, "request_byte_cap": MAX_REQUEST_BYTES,
              "hardware": {"platform": platform.platform(), "machine": platform.machine(), "python": sys.version, "cpu_count": __import__('os').cpu_count()},
              "execution_order": orders, "directory_bytes": sizes, "arms": arms, "scores": scored, "summary": summaries,
              "differences": differences, "generation_gate": gate, "provider_generation_calls": 0, "embedding_api_cost_usd": "0",
              "source_review": "PENDING: inspect every difference; exact source annotations are reviewed, method-level outcomes still need inspection.",
              "limits": "21 exposed development questions, no blind/generalization claim. Source presence, annotated evidence coverage and answer correctness are distinct. Labels are not exhaustive qrels. Two scope checks do not prove universal absence. Local compute cost is unmeasured. No LLM generation occurred."}
    report["run_hash"] = fingerprint(report)
    (destination / "run.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return destination, report


def select_tuning_candidate(candidates: list[dict]) -> str | None:
    """Choose only a gate-passing setting, preferring evidence then retrieval speed."""
    eligible = [candidate for candidate in candidates
                if candidate["report"]["generation_gate"].get("meaningful_evidence_gain") is True
                and "hybrid" in candidate["report"].get("summary", {})]
    if not eligible:
        return None

    def order(candidate):
        report = candidate["report"]
        summary = report["summary"]["hybrid"]["packed"]
        performance = report["arms"]["hybrid"]["performance"]
        weights = candidate["weights"]
        return (-summary["answerable_complete"]["count"],
                -summary["financial_rows_headers"]["count"],
                performance["p95_query_ms"], -weights[0])

    return min(eligible, key=order)["run_id"]


def run_paraphrase_probes(run_id: str, release: Path, experiment_dir: Path,
                          weights: tuple[float, float], destination: Path) -> dict:
    """Compare three unlabeled paraphrases as diagnostics, never as gate evidence."""
    destination.mkdir(parents=True, exist_ok=False)
    query_file = destination / "probe-queries.json"
    query_file.write_bytes(canonical(list(PARAPHRASE_PROBES)))
    frozen_hashes = hashes()
    arms, processes, logs = {}, {}, {}
    try:
        for arm in ("lexical", "hybrid"):
            log = (destination / (arm + "-worker.log")).open("w")
            logs[arm] = log
            command = [sys.executable, str(Path(__file__).resolve()), "worker", "--arm", arm,
                       "--release", str(release.resolve()), "--queries", str(query_file.resolve()),
                       "--experiment-dir", str(experiment_dir.resolve()),
                       "--lexical-weight", str(weights[0]), "--dense-weight", str(weights[1])]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=log, text=True)
            processes[arm] = process
            arms[arm] = {"readiness": read_message(process)}
        ready = all(arms[arm]["readiness"]["status"] == "ready" for arm in arms)
        if not ready:
            return {"run_id": run_id, "status": "unavailable", "weights": list(weights),
                    "arms": arms, "probes": [],
                    "interpretation": "Unlabeled secondary probes; unavailable results do not change the retrieval gate."}
        for arm in arms:
            warm = send(processes[arm], "warm")
            if warm["status"] != "ok":
                raise ValueError("Paraphrase worker warmup failed")
            arms[arm]["warmup"] = {k: v for k, v in warm.items() if k != "cases"}
            measured = send(processes[arm], "measure")
            if measured["status"] != "ok":
                raise ValueError("Paraphrase worker measurement failed")
            arms[arm]["measurement"] = measured
    finally:
        for process in processes.values():
            if process.poll() is None:
                try:
                    process.stdin.write('{"action":"stop"}\n')
                    process.stdin.flush()
                    process.wait(timeout=5)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    process.terminate()
                    process.wait(timeout=5)
        for log in logs.values():
            log.close()
    if hashes() != frozen_hashes:
        raise ValueError("Code changed during paraphrase diagnostics")
    by_arm = {arm: {case["case_id"]: case for case in data["measurement"]["cases"]}
              for arm, data in arms.items()}
    probes = []
    for query in PARAPHRASE_PROBES:
        lexical = by_arm["lexical"][query["case_id"]]
        hybrid = by_arm["hybrid"][query["case_id"]]
        lexical_pack = [p["chunk_id"] for p in lexical["pack"]["passages"]]
        hybrid_pack = [p["chunk_id"] for p in hybrid["pack"]["passages"]]
        probes.append({"probe_id": query["case_id"], "ticker": query["ticker"],
                       "question_sha256": fingerprint_question(query["question"]),
                       "ranking_changed": [p["chunk_id"] for p in lexical["ranked"]] !=
                                           [p["chunk_id"] for p in hybrid["ranked"]],
                       "lexical_ranked": ranked_references(lexical["ranked"]),
                       "hybrid_ranked": ranked_references(hybrid["ranked"]),
                       "lexical_pack_hash": lexical["pack"]["evidence_hash"],
                       "hybrid_pack_hash": hybrid["pack"]["evidence_hash"],
                       "lexical_packed_ids": lexical_pack, "hybrid_packed_ids": hybrid_pack,
                       "packed_added": sorted(set(hybrid_pack) - set(lexical_pack)),
                       "packed_removed": sorted(set(lexical_pack) - set(hybrid_pack)),
                       "lexical_query_ms": lexical["query_ms"], "hybrid_query_ms": hybrid["query_ms"]})
    report = {"run_id": run_id, "status": "complete", "weights": list(weights),
              "arms": {arm: {"readiness": data["readiness"],
                             "warmup": data["warmup"],
                             "measurement": {k: v for k, v in data["measurement"].items() if k != "cases"}}
                       for arm, data in arms.items()},
              "probes": probes,
              "interpretation": "Three fixed unlabeled probes for human inspection only. No relevance labels, answer grades, or gate contribution."}
    report["run_hash"] = fingerprint(report)
    (destination / "run.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report


def tune(args):
    """Run the three predeclared fusion settings, then limited diagnostic probes."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,45}", args.run_id or ""):
        raise ValueError("A safe tuning run ID of at most 46 characters is required")
    weights = [validate_fusion_weights(*pair) for pair in TUNING_WEIGHTS]
    child_ids = [f"{args.run_id}-l{int(pair[0] * 100):02d}-d{int(pair[1] * 100):02d}" for pair in weights]
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", child) for child in child_ids):
        raise ValueError("Candidate run ID exceeds the safe limit")
    if (LOCAL / "retrieval_runs" / args.run_id).exists() or any(
            (LOCAL / "retrieval_runs" / child).exists() for child in child_ids):
        raise ValueError("Tuning or candidate run ID already exists")

    candidates = []
    for child_id, pair in zip(child_ids, weights):
        candidate_args = argparse.Namespace(action="compare", run_id=child_id, release=args.release,
                                            labels=args.labels, experiment_dir=args.experiment_dir,
                                            lexical_weight=pair[0], dense_weight=pair[1], lexical_floor=0,
                                            floor_policy="fixed", passage_window_tokens=POLICY["passage_window_tokens"])
        _, report = compare(candidate_args)
        candidates.append({"run_id": child_id, "weights": list(pair), "report": report})

    selected_id = select_tuning_candidate(candidates)
    selected = next((candidate for candidate in candidates if candidate["run_id"] == selected_id), None)
    probe_weights = tuple(selected["weights"]) if selected else (0.5, 0.5)
    probe_reason = ("Use the best candidate that passed the unchanged evidence gate." if selected else
                    "No candidate passed; use equal fusion only as a diagnostic control, not as an adoption decision.")
    destination = LOCAL / "retrieval_runs" / args.run_id
    destination.mkdir(parents=True, exist_ok=False)
    probe_report = run_paraphrase_probes(args.run_id + "-probes", args.release, args.experiment_dir,
                                         probe_weights, destination / "paraphrase-probes")
    candidate_summary = []
    for candidate in candidates:
        report = candidate["report"]
        candidate_summary.append({"run_id": candidate["run_id"], "run_hash": report["run_hash"],
                                  "weights": candidate["weights"], "summary": report["summary"],
                                  "generation_gate": report["generation_gate"],
                                  "hybrid_performance": report.get("arms", {}).get("hybrid", {}).get("performance"),
                                  "hybrid_setup": report.get("arms", {}).get("hybrid", {}).get("readiness", {}).get("setup")})
    report = {"tuning_version": "weighted-rrf-three-setting-v1", "run_id": args.run_id,
              "created_at": datetime.now(timezone.utc).isoformat(), "development_cases": 21,
              "corpus_version": candidates[0]["report"]["corpus_version"],
              "documents": candidates[0]["report"]["documents"], "passages": candidates[0]["report"]["passages"],
              "weights_tested": [list(pair) for pair in weights], "selection_rule":
              "Only a candidate passing the existing evidence gate may be selected. Rank passing candidates by packed complete answerable cases, financial row/header coverage, lower hybrid p95 query latency, then higher lexical weight.",
              "candidates": candidate_summary, "selected_for_conditional_answer_followup": selected_id,
              "permanent_default_changed": False, "probe_weights": list(probe_weights),
              "probe_weight_reason": probe_reason, "paraphrase_probes": probe_report,
              "provider_generation_calls": 0, "paid_generation_authorized": False,
              "answer_generation_next": "Requires a passing retrieval gate, available credentials/network, a fresh spend estimate, and explicit paid-stage authorization. This tune command makes no provider calls.",
              "limits": "Only the 21 exposed development questions were labeled and scored. The three fixed paraphrase probes are unlabeled diagnostics excluded from candidate selection. Nine exposed holdout questions were not used. This is not an answer-quality or generalization result."}
    report["run_hash"] = fingerprint(report)
    (destination / "tuning.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return destination, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate-labels", "baseline", "compare", "tune", "worker"])
    parser.add_argument("--release", type=Path, default=LOCAL / "releases" / json.loads(SEED.read_text())["corpus_version"])
    parser.add_argument("--labels", type=Path, default=LABELS)
    parser.add_argument("--experiment-dir", type=Path, default=LOCAL / "retrieval_experiments/hybrid-dev-v1")
    parser.add_argument("--run-id")
    parser.add_argument("--arm", choices=["lexical", "hybrid"])
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--lexical-weight", type=float, default=0.5)
    parser.add_argument("--dense-weight", type=float, default=0.5)
    parser.add_argument("--lexical-floor", type=int, default=0,
                        help="For hybrid comparisons, preserve this many top lexical results before filling by fusion")
    parser.add_argument("--floor-policy", choices=FLOOR_POLICIES, default="fixed",
                        help="Protect every eligible lexical hit, or only hits meeting the frozen query-coverage rule")
    parser.add_argument("--passage-window-tokens", type=int, choices=PASSAGE_WINDOW_CHOICES,
                        default=POLICY["passage_window_tokens"],
                        help="Embedding input size for passage views; query capacity and corpus passages stay fixed")
    parser.add_argument("--packing-policy", choices=tuple(PACKING_VERSIONS), default="baseline",
                        help="Hybrid evidence ordering; lexical-tail-v1 admits lexical ranks 11-15 after six raw hits/context groups")
    parser.add_argument("--lexical-tail-depth", type=int, choices=LEXICAL_TAIL_DEPTH_CHOICES, default=POLICY["top_k"],
                        help="For the lexical-tail policy, offer up to five lexical ranks 11-15 as extra pack candidates; raw top ten stays fixed")
    args = parser.parse_args()
    if args.action not in {"compare", "worker"} and (args.lexical_floor != 0 or args.floor_policy != "fixed"):
        parser.error("Lexical floor options are supported only by compare and worker actions")
    if args.floor_policy != "fixed" and args.lexical_floor == 0:
        parser.error("A conditional floor policy requires --lexical-floor greater than zero")
    if args.action not in {"compare", "worker"} and args.passage_window_tokens != POLICY["passage_window_tokens"]:
        parser.error("Passage window options are supported only by compare and worker actions")
    if args.action not in {"compare", "worker"} and args.packing_policy != "baseline":
        parser.error("Packing policy options are supported only by compare and worker actions")
    if args.action not in {"compare", "worker"} and args.lexical_tail_depth != POLICY["top_k"]:
        parser.error("Lexical tail options are supported only by compare and worker actions")
    if (args.lexical_tail_depth > POLICY["top_k"]) != (args.packing_policy == "context-anchored-tail-v1"):
        parser.error("Depth 15 and the context-anchored-tail packing policy must be selected together")
    if args.action == "worker":
        if not args.arm or not args.queries:
            parser.error("worker needs arm and query input")
        if args.arm == "lexical" and (args.lexical_floor != 0 or args.floor_policy != "fixed"):
            parser.error("The lexical worker cannot use hybrid floor options")
        if args.arm == "lexical" and args.passage_window_tokens != POLICY["passage_window_tokens"]:
            parser.error("The lexical worker cannot use embedding window options")
        if args.arm == "lexical" and args.packing_policy != "baseline":
            parser.error("The lexical worker must use the frozen baseline packing policy")
        if args.arm == "lexical" and args.lexical_tail_depth != POLICY["top_k"]:
            parser.error("The lexical worker cannot use hybrid lexical-tail options")
        worker(args)
        return 0
    try:
        if args.action == "validate-labels":
            manifest, chunks = load_release(args.release)
            rows = [r for r in map(json.loads, BENCHMARK.read_text().splitlines()) if r["split"] == "development"]
            labels = load_labels(args.labels, manifest, chunks, rows)
            print(json.dumps({"annotation_hash": labels["annotation_hash"], "cases": len(labels["cases"]), "status": "PASS"}))
        elif args.action == "tune":
            destination, report = tune(args)
            print(destination)
            print(json.dumps({"weights_tested": report["weights_tested"],
                              "candidates": [{"run_id": c["run_id"], "summary": c["summary"],
                                              "generation_gate": c["generation_gate"]}
                                             for c in report["candidates"]],
                              "selected_for_conditional_answer_followup": report[
                                  "selected_for_conditional_answer_followup"]}, indent=2))
        else:
            destination, report = compare(args)
            print(destination)
            print(json.dumps({"hybrid_settings": {"weights": report["hybrid_fusion_weights"],
                                                   "maximum_lexical_floor": report["hybrid_lexical_floor"],
                                                   "floor_policy": report["hybrid_floor_policy"],
                                                   "passage_window_tokens": report["hybrid_passage_window_tokens"],
                                                   "lexical_tail_depth": report["hybrid_lexical_tail_depth"],
                                                   "packing_policy": report["hybrid_packing_policy"],
                                                   "pack_version": report["hybrid_pack_version"]},
                              "floor_decisions": report["arms"].get("hybrid", {}).get("floor_decisions"),
                              "resources": {arm: {"median_query_ms": data["performance"]["median_query_ms"],
                                                   "p95_query_ms": data["performance"]["p95_query_ms"],
                                                   "peak_rss_bytes": data["performance"]["peak_rss_bytes"],
                                                   **{key: data["readiness"]["setup"].get(key) for key in
                                                      ("cache_hit", "index_setup_ms", "passage_encoding_ms", "window_count")}}
                                            for arm, data in report["arms"].items() if "performance" in data},
                              "summary": report["summary"], "generation_gate": report["generation_gate"]}, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print("Retrieval evaluation failed: " + type(error).__name__ + "; inspect frozen inputs and local worker logs.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
