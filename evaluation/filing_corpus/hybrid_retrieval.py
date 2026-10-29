"""One frozen, local embedding experiment beside the unchanged lexical baseline.

No benchmark, relevance labels, answer model, network download or database is
used here. Windows are derived index views; results retain original passage IDs.
Heavy dependencies load only when a real local encoder is explicitly supplied.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import re
import socket
import tempfile
import time
from unittest.mock import patch

from qa_baseline import LexicalIndex, tokenize
from qa_contract import canonical, fingerprint


VERSION = "hybrid-minilm-rrf-passage-window-v5"
MODEL = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
POLICY = {"model": MODEL, "revision": REVISION, "device": "cpu", "dtype": "float32",
          "seed": 0, "threads": 1, "batch_size": 32, "overlap_tokens": 64,
          "normalization": "L2", "window_aggregation": "max_similarity_per_original_passage",
          "candidate_k": 50, "rrf_k": 60, "top_k": 10, "query_prefix": "", "passage_prefix": "",
          "passage_window_tokens": 254}
PASSAGE_WINDOW_CHOICES = (128, 254)
FLOOR_POLICIES = ("fixed", "query-coverage-v1")
CONDITIONAL_FLOOR_POLICY = {"name": "query-coverage-v1", "min_query_terms": 2,
                          "min_query_term_coverage": 0.5,
                          "eligible_hits": "first_requested_lexical_hits",
                          "term_definition": "distinct_qa_baseline_tokenize_terms",
                          "interpretation": "literal_overlap_heuristic_not_calibrated_confidence"}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def no_network():
    """Fail on Python socket connections as well as disabling Hub telemetry."""
    settings = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false",
                "DO_NOT_TRACK": "1"}
    def blocked(*args, **kwargs):
        raise RuntimeError("Network is disabled during retrieval evaluation")
    with patch.dict(os.environ, settings), patch.object(socket.socket, "connect", blocked), \
            patch.object(socket.socket, "connect_ex", blocked), patch.object(socket, "create_connection", blocked):
        yield


def token_windows(length: int, content_limit: int, overlap: int = 64) -> list[tuple[int, int]]:
    """Cover every token without changing stored corpus passages or truncating."""
    if type(length) is not int or length < 1 or not 0 <= overlap < content_limit:
        raise ValueError("Invalid token window bounds")
    windows, start = [], 0
    while start < length:
        end = min(start + content_limit, length)
        windows.append((start, end))
        if end == length:
            break
        start = end - overlap
    return windows


def validate_passage_window_tokens(value: int, content_limit: int = 254) -> int:
    """Bound passage views independently from the encoder's query capacity."""
    if type(value) is not int or not POLICY["overlap_tokens"] < value <= content_limit:
        raise ValueError("Passage window must exceed the overlap and fit the encoder capacity")
    return value


def validate_fusion_weights(lexical_weight, dense_weight) -> tuple[float, float]:
    """Require finite, nonnegative weights that sum to one."""
    values = (lexical_weight, dense_weight)
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError("Fusion weights must be finite numbers")
    lexical_weight, dense_weight = map(float, values)
    if lexical_weight < 0 or dense_weight < 0 or not math.isclose(
            lexical_weight + dense_weight, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("Fusion weights must be nonnegative and sum to one")
    return lexical_weight, dense_weight


def validate_lexical_floor(lexical_floor, limit: int) -> int:
    """Validate how many leading lexical hits the hybrid result must retain."""
    if type(lexical_floor) is not int or not 0 <= lexical_floor <= limit:
        raise ValueError("Lexical floor must be an integer between zero and the result limit")
    return lexical_floor


def validate_floor_policy(floor_policy: str, lexical_floor: int) -> str:
    if floor_policy not in FLOOR_POLICIES:
        raise ValueError("Unknown lexical floor policy")
    if floor_policy != "fixed" and lexical_floor == 0:
        raise ValueError("A conditional floor policy requires a positive lexical floor")
    return floor_policy


def lexical_floor_decision(lexical: list[dict], question: str, lexical_floor: int,
                           floor_policy: str = "fixed") -> dict:
    """Select protected hits using source text only; coverage is not confidence."""
    validate_lexical_floor(lexical_floor, POLICY["top_k"])
    validate_floor_policy(floor_policy, lexical_floor)
    query_terms = set(tokenize(question))
    decision = {"policy": floor_policy, "requested_floor": lexical_floor,
                "effective_floor": 0, "query_term_count": len(query_terms),
                "reason": "disabled", "protected_chunk_ids": [], "candidates": []}
    if lexical_floor == 0:
        return decision
    if not lexical:
        decision["reason"] = "no_lexical_hits"
        return decision
    conditional = floor_policy != "fixed"
    enough_terms = len(query_terms) >= CONDITIONAL_FLOOR_POLICY["min_query_terms"]
    for rank, hit in enumerate(lexical[:lexical_floor], 1):
        candidate = {"chunk_id": hit["chunk_id"], "lexical_rank": rank, "protected": True}
        if conditional:
            matched = len(query_terms & set(tokenize(hit["text"])))
            coverage = matched / len(query_terms) if query_terms else 0.0
            candidate.update(matched_query_terms=matched, query_term_coverage=coverage,
                             protected=enough_terms and coverage >= CONDITIONAL_FLOOR_POLICY["min_query_term_coverage"])
        decision["candidates"].append(candidate)
        if candidate["protected"]:
            decision["protected_chunk_ids"].append(hit["chunk_id"])
    decision["effective_floor"] = len(decision["protected_chunk_ids"])
    decision["reason"] = ("fixed" if not conditional else "insufficient_query_terms" if not enough_terms else
                          "all_candidates_meet_coverage" if decision["effective_floor"] == len(decision["candidates"]) else
                          "some_candidates_meet_coverage" if decision["effective_floor"] else "no_candidate_meets_coverage")
    return decision


def fuse(lexical: list[dict], dense: list[dict], limit: int = 10,
         lexical_weight: float = 0.5, dense_weight: float = 0.5,
         lexical_floor: int = 0, protected_chunk_ids: list[str] | None = None) -> list[dict]:
    """Weighted reciprocal-rank fusion, optionally protecting leading lexical hits."""
    if not 1 <= limit <= POLICY["top_k"]:
        raise ValueError("Invalid hybrid result count")
    lexical_weight, dense_weight = validate_fusion_weights(lexical_weight, dense_weight)
    lexical_floor = validate_lexical_floor(lexical_floor, limit)
    records, scores, positions = {}, {}, {}
    for name, hits, weight in (("lexical", lexical, lexical_weight), ("dense", dense, dense_weight)):
        seen = set()
        for rank, chunk in enumerate(hits[:POLICY["candidate_k"]], 1):
            cid = chunk["chunk_id"]
            if cid in seen:
                raise ValueError("Duplicate original passage in a ranked arm")
            seen.add(cid)
            if cid in records and any(records[cid].get(k) != chunk.get(k) for k in ("source_id", "ticker", "text")):
                raise ValueError("Ranked arms disagree on passage identity")
            records[cid] = chunk
            scores[cid] = scores.get(cid, 0) + weight / (POLICY["rrf_k"] + rank)
            positions.setdefault(cid, {})[name + "_rank"] = rank
    order = sorted(records, key=lambda cid: (-scores[cid], cid))
    eligible = [hit["chunk_id"] for hit in lexical[:lexical_floor]]
    protected = eligible if protected_chunk_ids is None else protected_chunk_ids
    if (type(protected) is not list or len(set(protected)) != len(protected)
            or protected != [cid for cid in eligible if cid in protected]):
        raise ValueError("Protected passages must be an ordered subset of the eligible lexical hits")
    protected_ids = set(protected)
    remainder = [cid for cid in order if cid not in protected_ids]
    selected = protected + remainder[:max(0, limit - len(protected))]
    return [{**records[cid], "score": scores[cid], **positions[cid],
             **({"lexical_protected": True} if cid in protected_ids else {})} for cid in selected]


class LocalEncoder:
    """Strictly local pinned CPU inference, with explicit token-window layout."""

    def __init__(self, model_dir: Path, artifacts: Path, requirements: Path,
                 passage_window_tokens: int = 254):
        validate_passage_window_tokens(passage_window_tokens)
        self.artifacts = json.loads(artifacts.read_text())
        if (self.artifacts.get("model"), self.artifacts.get("revision"), self.artifacts.get("license")) != (MODEL, REVISION, "apache-2.0"):
            raise ValueError("Encoder artifact identity changed")
        expected_files = self.artifacts.get("files", {})
        if not {"model.safetensors", "config.json", "tokenizer.json", "modules.json"} <= set(expected_files):
            raise ValueError("Incomplete pinned model artifacts")
        for name, item in expected_files.items():
            path = model_dir / name
            if not path.resolve().is_relative_to(model_dir.resolve()) or path.suffix in {".py", ".bin"}:
                raise ValueError("Unapproved model artifact")
            if file_hash(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
                raise ValueError("Model artifact hash mismatch")
        installed = {}
        for entry in requirements.read_text().splitlines():
            if not entry or entry.startswith("#"):
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+", entry):
                raise ValueError("Experiment dependencies must be exactly pinned")
            package, expected = entry.split("==")
            installed[package] = version(package)
            if installed[package] != expected:
                raise ValueError("Experiment runtime does not match dependency lock")
        with no_network():
            import torch
            from sentence_transformers import SentenceTransformer
            torch.manual_seed(0)
            torch.set_num_threads(1)
            if torch.get_num_interop_threads() != 1:
                torch.set_num_interop_threads(1)
            torch.use_deterministic_algorithms(True)
            self.model = SentenceTransformer(str(model_dir.resolve()), device="cpu", trust_remote_code=False,
                                            local_files_only=True,
                                            model_kwargs={"use_safetensors": True, "torch_dtype": torch.float32})
            self.model.eval()
        self.torch = torch
        self.tokenizer = self.model.tokenizer
        self.content_limit = self.model.max_seq_length - self.tokenizer.num_special_tokens_to_add(pair=False)
        if self.content_limit <= POLICY["overlap_tokens"]:
            raise ValueError("Pinned encoder has incompatible token limit")
        self.passage_window_tokens = validate_passage_window_tokens(passage_window_tokens, self.content_limit)
        self.identity = {"policy": {**POLICY, "passage_window_tokens": self.passage_window_tokens},
                         "artifact_manifest_sha256": file_hash(artifacts),
                         "requirements_sha256": file_hash(requirements), "runtime": installed,
                         "content_limit": self.content_limit}

    def tokens(self, text: str) -> list[int]:
        return self.tokenizer(text, add_special_tokens=False, truncation=False)["input_ids"]

    def encode_ids(self, windows: list[list[int]]):
        """Feed token IDs directly; decode/re-tokenize cannot silently lose text."""
        import numpy as np
        vectors = []
        with no_network(), self.torch.inference_mode():
            for start in range(0, len(windows), POLICY["batch_size"]):
                features = [self.tokenizer.prepare_for_model(ids, add_special_tokens=True, truncation=False,
                            return_attention_mask=True, return_token_type_ids=True)
                            for ids in windows[start:start + POLICY["batch_size"]]]
                if any(len(item["input_ids"]) > self.model.max_seq_length for item in features):
                    raise ValueError("Token window exceeds encoder capacity")
                batch = self.tokenizer.pad(features, padding=True, return_tensors="pt")
                values = self.model(batch)["sentence_embedding"]
                values = self.torch.nn.functional.normalize(values, p=2, dim=1)
                vectors.append(values.cpu().numpy().astype("float32", copy=False))
        return np.concatenate(vectors) if vectors else np.empty((0, 384), dtype="float32")

    def encode_passages(self, chunks: list[dict]):
        windows, layout = [], []
        for ordinal, chunk in enumerate(chunks):
            ids = self.tokens(chunk["text"])
            for start, end in token_windows(len(ids), self.passage_window_tokens, POLICY["overlap_tokens"]):
                windows.append(ids[start:end])
                layout.append({"chunk_index": ordinal, "start": start, "end": end, "token_count": len(ids)})
        return self.encode_ids(windows), layout

    def encode_query(self, question: str):
        ids = self.tokens(question)
        if not ids or len(ids) > self.content_limit or len(question.encode()) > 2000:
            raise ValueError("Query cannot be encoded without truncation")
        return self.encode_ids([ids])[0]


def cache_identity(chunks: list[dict], encoder_identity: dict) -> dict:
    if len({c["chunk_id"] for c in chunks}) != len(chunks):
        raise ValueError("Duplicate corpus passage IDs")
    return {"version": VERSION, "implementation_sha256": file_hash(Path(__file__)),
            "encoder": encoder_identity,
            "passages": [{"chunk_id": c["chunk_id"], "source_id": c["source_id"], "ticker": c["ticker"],
                          "text_sha256": hashlib.sha256(c["text"].encode()).hexdigest()} for c in chunks]}


def validate_vectors(vectors, layout: list[dict], count: int, passage_window_tokens: int):
    import numpy as np
    if vectors.dtype != np.float32 or vectors.ndim != 2 or vectors.shape != (len(layout), 384):
        raise ValueError("Embedding shape/dtype changed")
    if not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=2e-5):
        raise ValueError("Embedding is non-finite, zero or unnormalized")
    expected = []
    previous = -1
    for item in layout:
        i = item.get("chunk_index")
        if type(i) is not int or not 0 <= i < count or i < previous:
            raise ValueError("Invalid window-to-passage mapping")
        previous = i
    for i in range(count):
        items = [item for item in layout if item["chunk_index"] == i]
        if not items or len({item["token_count"] for item in items}) != 1:
            raise ValueError("Missing/inconsistent passage token windows")
        for start, end in token_windows(items[0]["token_count"], passage_window_tokens, POLICY["overlap_tokens"]):
            expected.append({"chunk_index": i, "start": start, "end": end, "token_count": items[0]["token_count"]})
    if expected != layout:
        raise ValueError("Windows omit, repeat or reorder source tokens")


class HybridIndex:
    def __init__(self, chunks: list[dict], encoder: LocalEncoder, cache_root: Path):
        import numpy as np
        self.chunks, self.encoder = chunks, encoder
        self.passage_window_tokens = validate_passage_window_tokens(
            getattr(encoder, "passage_window_tokens", encoder.content_limit), encoder.content_limit)
        start = time.perf_counter()
        self.lexical = LexicalIndex(chunks)
        self.timing_ms = {"lexical_index_ms": (time.perf_counter() - start) * 1000,
                          "passage_encoding_ms": 0.0}
        cache_start = time.perf_counter()
        identity = cache_identity(chunks, encoder.identity)
        self.cache_key = fingerprint(identity)
        self.cache_path = cache_root / self.cache_key
        self.cache_hit = self.cache_path.exists()
        if self.cache_hit:
            meta = json.loads((self.cache_path / "metadata.json").read_text())
            if meta.get("identity") != identity or meta.get("cache_key") != self.cache_key:
                raise ValueError("Stale or mismatched embedding cache")
            if file_hash(self.cache_path / "vectors.npy") != meta.get("vectors_sha256"):
                raise ValueError("Embedding cache checksum mismatch")
            self.vectors = np.load(self.cache_path / "vectors.npy", allow_pickle=False, mmap_mode="r")
            self.layout = meta["layout"]
            validate_vectors(self.vectors, self.layout, len(chunks), self.passage_window_tokens)
        else:
            encode_start = time.perf_counter()
            self.vectors, self.layout = encoder.encode_passages(chunks)
            self.timing_ms["passage_encoding_ms"] = (time.perf_counter() - encode_start) * 1000
            validate_vectors(self.vectors, self.layout, len(chunks), self.passage_window_tokens)
            cache_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".index-", dir=cache_root) as staging:
                staging = Path(staging)
                np.save(staging / "vectors.npy", self.vectors, allow_pickle=False)
                meta = {"identity": identity, "cache_key": self.cache_key, "layout": self.layout,
                        "vectors_sha256": file_hash(staging / "vectors.npy")}
                (staging / "metadata.json").write_bytes(canonical(meta))
                staging.rename(self.cache_path)
        self.parents = np.array([item["chunk_index"] for item in self.layout], dtype="int32")
        self.timing_ms["cache_and_validation_ms"] = (time.perf_counter() - cache_start) * 1000 - self.timing_ms["passage_encoding_ms"]

    def dense_search(self, ticker: str, question: str, limit: int = 50) -> list[dict]:
        import numpy as np
        eligible = np.array([c["ticker"] == ticker for c in self.chunks])
        if not eligible.any():
            return []
        query = self.encoder.encode_query(question)
        selected = eligible[self.parents]
        scores = np.full(len(self.chunks), -np.inf, dtype="float32")
        np.maximum.at(scores, self.parents[selected], self.vectors[selected] @ query)
        order = sorted(np.flatnonzero(eligible), key=lambda i: (-float(scores[i]), self.chunks[i]["chunk_id"]))[:limit]
        return [{**self.chunks[i], "score": float(scores[i])} for i in order]

    def search(self, ticker: str, question: str, limit: int = 10,
               weights: tuple[float, float] = (0.5, 0.5), lexical_floor: int = 0,
               floor_policy: str = "fixed") -> list[dict]:
        return self.search_with_diagnostics(ticker, question, limit, weights, lexical_floor, floor_policy)["ranked"]

    def search_with_diagnostics(self, ticker: str, question: str, limit: int = 10,
                                weights: tuple[float, float] = (0.5, 0.5), lexical_floor: int = 0,
                                floor_policy: str = "fixed") -> dict:
        """Return rankings and the floor decision, including queries with no hits."""
        validate_lexical_floor(lexical_floor, limit)
        validate_floor_policy(floor_policy, lexical_floor)
        if type(weights) not in (tuple, list) or len(weights) != 2:
            raise ValueError("Fusion weights must contain lexical and dense values")
        lexical_weight, dense_weight = validate_fusion_weights(*weights)
        lexical = self.lexical.search(ticker, question, POLICY["candidate_k"]) if question.strip() else []
        decision = lexical_floor_decision(lexical, question, lexical_floor, floor_policy)
        dense = self.dense_search(ticker, question, POLICY["candidate_k"]) if question.strip() else []
        ranked = fuse(lexical, dense, limit, lexical_weight, dense_weight, lexical_floor,
                      decision["protected_chunk_ids"])
        return {"ranked": ranked, "lexical_floor_decision": decision}
