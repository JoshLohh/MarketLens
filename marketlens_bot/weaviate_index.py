"""Exact Weaviate window retrieval using the evaluated app-side fusion."""

from __future__ import annotations

import math
from uuid import NAMESPACE_URL, uuid5

from .answer import _EVALUATION  # Registers the existing evaluation modules.
from hybrid_retrieval import POLICY, fuse, lexical_floor_decision  # noqa: E402


class IndexError(RuntimeError):
    """The persistent vector collection does not match the frozen pilot."""


def window_id(cache_key: str, position: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"marketlens:{cache_key}:{position}"))


def expected_windows(local_index) -> dict[str, dict]:
    """Describe every cached window without modifying or re-encoding it."""
    expected = {}
    for position, layout in enumerate(local_index.layout):
        chunk = local_index.chunks[layout["chunk_index"]]
        ident = window_id(local_index.cache_key, position)
        expected[ident] = {"cacheKey": local_index.cache_key, "windowIndex": position,
                           "chunkId": chunk["chunk_id"], "sourceId": chunk["source_id"],
                           "ticker": chunk["ticker"], "start": layout["start"],
                           "end": layout["end"], "tokenCount": layout["token_count"]}
    return expected


class WeaviateIndex:
    """Verify all stored windows before allowing exact dense retrieval.

    The client supplies verified schema, full listing, and near-vector queries.
    It must not return a partial list as a successful response.
    """

    def __init__(self, local_index, client):
        self.local, self.client = local_index, client
        self.chunks, self.lexical = local_index.chunks, local_index.lexical
        self.expected = expected_windows(local_index)
        self.by_chunk = {chunk["chunk_id"]: chunk for chunk in self.chunks}
        self._verified = False

    def verify_collection(self) -> None:
        self._verified = False
        schema = self.client.schema()
        config = schema.get("vectorIndexConfig", {})
        if (schema.get("vectorIndexType") != "flat" or schema.get("vectorizer") != "none"
                or config.get("distance") != "cosine"
                or any(config.get(kind, {}).get("enabled", False) for kind in ("bq", "rq", "pq", "sq"))
                or schema.get("description") != "marketlens-pilot:" + self.local.cache_key):
            raise IndexError("Collection identity or vector configuration changed")
        seen = set()
        for record in self.client.list_windows():
            ident = record.get("id")
            properties = self.expected.get(ident)
            if properties is None or ident in seen or record.get("properties") != properties:
                raise IndexError("Window ID or metadata mismatch")
            vector = record.get("vector")
            original = self.local.vectors[properties["windowIndex"]]
            if (not isinstance(vector, list) or len(vector) != len(original)
                    or any(type(value) not in (int, float) or not math.isfinite(value)
                           or abs(float(value) - float(reference)) > 1e-5
                           for value, reference in zip(vector, original))):
                raise IndexError("Window vector mismatch")
            seen.add(ident)
        if seen != set(self.expected):
            raise IndexError("Window collection is incomplete")
        self._verified = True

    def window_scores(self, ticker: str, question: str) -> dict[str, float]:
        if not self._verified:
            raise IndexError("Collection has not passed startup verification")
        eligible = {ident for ident, item in self.expected.items() if item["ticker"] == ticker}
        if not eligible:
            return {}
        if len(eligible) > 10_000:
            raise IndexError("Eligible window count exceeds configured search limit")
        query = self.local.encoder.encode_query(question)
        results = self.client.search_windows(ticker, [float(x) for x in query], len(eligible))
        scores = {}
        for record in results:
            ident, distance = record.get("id"), record.get("distance")
            if (ident not in eligible or ident in scores or type(distance) not in (int, float)
                    or not math.isfinite(distance) or not -1e-5 <= distance <= 2 + 1e-5):
                raise IndexError("Invalid or duplicate dense-search result")
            scores[ident] = 1.0 - float(distance)
        if set(scores) != eligible:
            raise IndexError("Dense search returned an incomplete window set")
        return scores

    def dense_search(self, ticker: str, question: str, limit: int = 50) -> list[dict]:
        scores = {}
        for ident, score in self.window_scores(ticker, question).items():
            chunk_id = self.expected[ident]["chunkId"]
            scores[chunk_id] = max(scores.get(chunk_id, -math.inf), score)
        ranked = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:limit]
        return [{**self.by_chunk[chunk_id], "score": scores[chunk_id]} for chunk_id in ranked]

    def search_with_diagnostics(self, ticker: str, question: str, limit: int = 10,
                                weights=(0.75, 0.25), lexical_floor: int = 3,
                                floor_policy: str = "fixed") -> dict:
        lexical = self.lexical.search(ticker, question, POLICY["candidate_k"]) if question.strip() else []
        decision = lexical_floor_decision(lexical, question, lexical_floor, floor_policy)
        dense = self.dense_search(ticker, question, POLICY["candidate_k"]) if question.strip() else []
        return {"ranked": fuse(lexical, dense, limit, *weights, lexical_floor,
                               decision["protected_chunk_ids"]), "lexical_floor_decision": decision}
