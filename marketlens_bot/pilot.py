"""Load the existing pilot cache and compare persistent retrieval to its control."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .answer import _EVALUATION
from .weaviate_client import WeaviateClient
from .weaviate_index import IndexError, WeaviateIndex
from hybrid_retrieval import POLICY, HybridIndex, LocalEncoder, cache_identity  # noqa: E402
from qa_baseline import BENCHMARK, load_release  # noqa: E402
from qa_contract import fingerprint  # noqa: E402
from retrieval_compare import DEPENDENCIES, hashes, query_result  # noqa: E402


ROOT = _EVALUATION.parents[1]
RELEASE = _EVALUATION / "local" / "releases" / "sec-qa-pilot-v1"
EXPERIMENT = _EVALUATION / "local" / "retrieval_experiments" / "hybrid-dev-v1"
FROZEN_BENCHMARK_SHA256 = "c8a4df8722bebbf35be644b2ec3281bc36f6a3d6b449580af6212664139240f4"


def frozen_benchmark_hash() -> str:
    digest = hashlib.sha256(BENCHMARK.read_bytes()).hexdigest()
    if digest != FROZEN_BENCHMARK_SHA256:
        raise IndexError("The fixed benchmark has changed")
    return digest


def load_pilot(release: Path = RELEASE, experiment: Path = EXPERIMENT):
    manifest, chunks = load_release(release)
    if manifest.get("corpus_version") != "sec-qa-pilot-v1" or len(manifest["documents"]) != 12 or len(chunks) != 3922:
        raise IndexError("The fixed pilot corpus does not match")
    encoder = LocalEncoder(experiment / "model", experiment / "model-artifacts.json", DEPENDENCIES,
                           passage_window_tokens=128)
    cache_key = fingerprint(cache_identity(chunks, encoder.identity))
    if not (experiment / "cache" / cache_key).is_dir():
        raise IndexError("The existing 128-token cache is missing; passage encoding is disabled")
    local = HybridIndex(chunks, encoder, experiment / "cache")
    if not local.cache_hit or len(local.layout) != 8279:
        raise IndexError("The existing window cache does not match the fixed pilot")
    return manifest, chunks, local


def parity_identity(manifest: dict, local, client: WeaviateClient) -> dict:
    meta = client._request("/v1/meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("version"), str):
        raise IndexError("Weaviate version is unavailable")
    code = {}
    for name in ("answer.py", "weaviate_index.py", "weaviate_client.py", "pilot.py"):
        code[name] = hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
    return {"version": "marketlens-parity-v1", "corpus_version": manifest["corpus_version"],
            "chunks_sha256": manifest["chunks_sha256"], "cache_key": local.cache_key,
            "weaviate_version": meta["version"], "evaluation_hashes": hashes(), "bot_hashes": code,
            "benchmark_sha256": frozen_benchmark_hash()}


def check_parity(manifest: dict, chunks: list[dict], local, candidate: WeaviateIndex, *, split: str) -> int:
    if split not in ("development", "holdout"):
        raise ValueError("Unknown benchmark split")
    frozen_benchmark_hash()
    questions = [json.loads(line) for line in BENCHMARK.read_text().splitlines()]
    rows = [row for row in questions if row.get("split") == split]
    if len(rows) != (21 if split == "development" else 9):
        raise IndexError("Benchmark split count changed")
    for row in rows:
        ticker, question = row["ticker"], row["question"]
        query_vector = local.encoder.encode_query(question)
        window_scores = candidate.window_scores(ticker, question)
        for ident, score in window_scores.items():
            position = candidate.expected[ident]["windowIndex"]
            baseline_score = float(local.vectors[position] @ query_vector)
            if abs(baseline_score - score) > 1e-5:
                raise IndexError("Window score parity failed for " + row["case_id"])
        candidate_k = POLICY["candidate_k"]
        reference = local.dense_search(ticker, question, candidate_k)
        dense = candidate.dense_search(ticker, question, candidate_k)
        if ([x["chunk_id"] for x in reference] != [x["chunk_id"] for x in dense]
                or any(abs(a["score"] - b["score"]) > 1e-5 for a, b in zip(reference, dense))):
            raise IndexError("Dense top-candidate retrieval parity failed for " + row["case_id"])
        query = {"case_id": row["case_id"], "ticker": ticker, "question": question}
        options = {"weights": (0.75, 0.25), "lexical_floor": 3, "floor_policy": "fixed",
                   "packing_policy": "context-anchored-tail-v1", "lexical_tail_depth": 15}
        left = query_result(local, manifest, chunks, query, **options)
        right = query_result(candidate, manifest, chunks, query, **options)
        if ([x["chunk_id"] for x in left["ranked"]] != [x["chunk_id"] for x in right["ranked"]]
                or left["pack"]["evidence_hash"] != right["pack"]["evidence_hash"]):
            raise IndexError("Hybrid ranking or evidence parity failed for " + row["case_id"])
    return len(rows)
