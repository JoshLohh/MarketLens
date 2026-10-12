"""Rank SEC filing passages for a question and score evidence retrieval.

This baseline returns cited evidence, not a generated financial answer. It never
uses benchmark reference answers or expected source IDs while ranking.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time

from corpus import HERE, LOCAL, SEED, sha256, verify


BENCHMARK = HERE.parent / "qa_benchmark.jsonl"
BASELINE_VERSION = "bm25-ticker-v1"
TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
STOPWORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for", "from", "how", "in", "is", "it", "of", "on", "or", "the", "to", "was", "were", "what", "when", "which", "why", "with"}


def tokenize(value: str) -> list[str]:
    return [token for token in TOKEN_PATTERN.findall(value.casefold()) if token not in STOPWORDS]


def load_release(release: Path) -> tuple[dict, list[dict]]:
    manifest = verify(release)
    chunks = [json.loads(line) for line in (release / "chunks.jsonl").read_text().splitlines()]
    return manifest, chunks


class LexicalIndex:
    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks
        self.terms = [Counter(tokenize(chunk["text"])) for chunk in chunks]
        self.lengths = [sum(counts.values()) for counts in self.terms]
        self.average_length = statistics.mean(self.lengths) if self.lengths else 1
        self.document_frequency = Counter()
        for counts in self.terms:
            self.document_frequency.update(counts.keys())

    def search(self, ticker: str, question: str, limit: int = 5) -> list[dict]:
        query = set(tokenize(question))
        if not query:
            return []
        results: list[tuple[float, int]] = []
        count = len(self.chunks)
        for index, chunk in enumerate(self.chunks):
            if chunk["ticker"] != ticker:
                continue
            score = 0.0
            for term in query:
                frequency = self.terms[index].get(term, 0)
                if not frequency:
                    continue
                df = self.document_frequency[term]
                idf = math.log1p((count - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.2 * (0.25 + 0.75 * self.lengths[index] / self.average_length)
                score += idf * frequency * 2.2 / denominator
            if score:
                results.append((score, index))
        results.sort(key=lambda pair: (-pair[0], self.chunks[pair[1]]["chunk_id"]))
        return [{**self.chunks[index], "score": round(score, 6)} for score, index in results[:limit]]


def evaluate(release: Path, split: str, top_k: int = 5) -> Path:
    manifest, chunks = load_release(release)
    index = LexicalIndex(chunks)
    rows = [json.loads(line) for line in BENCHMARK.read_text().splitlines()]
    counts = Counter(row["split"] for row in rows)
    if counts != {"development": 21, "holdout": 9}:
        raise ValueError(f"Benchmark split changed: {counts}")
    known = {item["source_id"] for item in manifest["documents"]}
    expected = {evidence["source_id"] for row in rows for evidence in row["evidence"]}
    if expected - known or len(expected) != 10:
        raise ValueError(f"Benchmark source mapping is incomplete: {sorted(expected - known)}")
    selected = [row for row in rows if row["split"] == split]
    report_path = LOCAL / "reports" / f"{manifest['corpus_version']}-{BASELINE_VERSION}-{split}-k{top_k}.json"
    if report_path.exists():
        raise FileExistsError(f"Existing report is retained; bump baseline/corpus version for a new run: {report_path}")
    results: list[dict] = []
    latencies: list[float] = []
    for row in selected:
        start = time.perf_counter()
        ranked = index.search(row["ticker"], row["question"], top_k)
        latencies.append((time.perf_counter() - start) * 1000)
        wanted = {evidence["source_id"] for evidence in row["evidence"]}
        found = {chunk["source_id"] for chunk in ranked}
        results.append({
            "case_id": row["case_id"],
            "ticker": row["ticker"],
            "category": row["category"],
            "required_source_ids": sorted(wanted),
            "retrieved_source_ids": sorted(found),
            "any_source_recalled": bool(wanted & found),
            "all_sources_recalled": wanted <= found,
            "citations": [{
                "source_id": chunk["source_id"],
                "chunk_id": chunk["chunk_id"],
                "block_start": chunk["block_start"],
                "block_end": chunk["block_end"],
                "score": chunk["score"],
            } for chunk in ranked],
        })
    summary = {
        "cases": len(results),
        "any_source_recall_at_k": round(sum(row["any_source_recalled"] for row in results) / len(results), 4),
        "all_sources_recall_at_k": round(sum(row["all_sources_recalled"] for row in results) / len(results), 4),
        "median_query_ms": round(statistics.median(latencies), 3),
        "zero_result_cases": [row["case_id"] for row in results if not row["citations"]],
    }
    report = {
        "corpus_version": manifest["corpus_version"],
        "extraction_version": manifest["extraction_version"],
        "baseline_version": BASELINE_VERSION,
        "baseline_code_sha256": sha256(Path(__file__).read_bytes()),
        "benchmark_sha256": sha256(BENCHMARK.read_bytes()),
        "split": split,
        "top_k": top_k,
        "method": "BM25-style lexical passage ranking, ticker filter; no reference answer or gold source used in ranking",
        "holdout_exposure": "Gold answers already present in repository; regression check, not blind generalization" if split == "holdout" else None,
        "limits": "Source recall measures retrieval only; cited-answer support, numeric correctness and abstention remain unscored",
        "summary": summary,
        "cases": results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report_path


def ask(release: Path, ticker: str, question: str, top_k: int = 5) -> None:
    manifest, chunks = load_release(release)
    by_id = {item["source_id"]: item for item in manifest["documents"]}
    ranked = LexicalIndex(chunks).search(ticker, question, top_k)
    if not ranked:
        print("Insufficient retrieved evidence; no answer produced.")
        return
    print("Evidence candidates only. Verify passages before answering:")
    for chunk in ranked:
        source = by_id[chunk["source_id"]]
        print(f"\n[{chunk['source_id']}:{chunk['block_start']}-{chunk['block_end']}] {source['reporting_period']}")
        print(source["sec_url"])
        print(chunk["text"][:900])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["ask", "evaluate"])
    parser.add_argument("--release", type=Path)
    parser.add_argument("--ticker", choices=["NBIS", "IREN", "ASTS"])
    parser.add_argument("--question")
    parser.add_argument("--split", choices=["development", "holdout"])
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 20:
        parser.error("--top-k must be between 1 and 20")
    release = args.release or LOCAL / "releases" / json.loads(SEED.read_text())["corpus_version"]
    try:
        if args.action == "ask":
            if not args.ticker or not args.question:
                parser.error("ask requires --ticker and --question")
            ask(release, args.ticker, args.question, args.top_k)
        else:
            if not args.split:
                parser.error("evaluate requires --split")
            print(evaluate(release, args.split, args.top_k))
        return 0
    except (ValueError, FileExistsError, OSError, json.JSONDecodeError) as error:
        print(f"Baseline failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
