"""Local-only Weaviate HTTP adapter for the fixed cached window collection."""

from __future__ import annotations

import json
import math
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .weaviate_index import IndexError, WeaviateIndex, expected_windows


COLLECTION = "MarketLensPilotV1"
FIELDS = ("cacheKey", "windowIndex", "chunkId", "sourceId", "ticker", "start", "end", "tokenCount")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class WeaviateError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class WeaviateClient:
    def __init__(self, api_key: str, *, port: int = 8080):
        if not api_key or any(c.isspace() for c in api_key) or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("A local Weaviate key and port are required")
        self._key = api_key
        self._base = f"http://127.0.0.1:{port}"
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _request(self, path: str, *, data=None):
        request = Request(self._base + path, data=None if data is None else json.dumps(data, allow_nan=False).encode(),
                          headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"},
                          method="GET" if data is None else "POST")
        try:
            with self._opener.open(request, timeout=30) as response:
                body = response.read(8_000_001)
            if len(body) > 8_000_000:
                raise WeaviateError("Weaviate response exceeded the size limit")
            return json.loads(body)
        except HTTPError as exc:
            raise WeaviateError("Weaviate request failed", exc.code) from None
        except (OSError, URLError, ValueError, TypeError):
            raise WeaviateError("Weaviate request failed or its outcome is uncertain") from None

    def schema(self):
        return self._request("/v1/schema/" + COLLECTION)

    def _query(self, query: str) -> list[dict]:
        response = self._request("/v1/graphql", data={"query": query})
        if not isinstance(response, dict) or response.get("errors"):
            raise WeaviateError("Weaviate query failed")
        try:
            records = response["data"]["Get"][COLLECTION]
        except (KeyError, TypeError):
            raise WeaviateError("Invalid Weaviate query response") from None
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise WeaviateError("Invalid Weaviate query response")
        return records

    def list_windows(self):
        after = None
        while True:
            cursor = "" if after is None else ", after: " + json.dumps(after)
            query = ("{ Get { " + COLLECTION + "(limit: 128" + cursor + ") { " +
                     " ".join(FIELDS) + " _additional { id vector } } } }")
            page = self._query(query)
            if len(page) > 128:
                raise WeaviateError("Invalid collection page")
            for item in page:
                extra = item.get("_additional") or {}
                yield {"id": extra.get("id"), "vector": extra.get("vector"),
                       "properties": {name: item.get(name) for name in FIELDS}}
            if len(page) < 128:
                break
            next_after = (page[-1].get("_additional") or {}).get("id")
            if not isinstance(next_after, str) or next_after == after:
                raise WeaviateError("Invalid collection cursor")
            after = next_after

    def search_windows(self, ticker: str, vector: list[float], count: int):
        if (ticker not in ("NBIS", "IREN", "ASTS") or type(count) is not int or not 1 <= count <= 10_000
                or len(vector) != 384 or any(not math.isfinite(x) for x in vector)):
            raise WeaviateError("Invalid vector query")
        query = ("{ Get { " + COLLECTION + "(limit: " + str(count) +
                 ", nearVector: {vector: " + json.dumps(vector, allow_nan=False) + "}, "
                 "where: {path: [\"ticker\"], operator: Equal, valueText: " + json.dumps(ticker) + "}) "
                 "{ _additional { id distance } } } }")
        return [{"id": (row.get("_additional") or {}).get("id"),
                 "distance": (row.get("_additional") or {}).get("distance")} for row in self._query(query)]

    def create_collection(self, cache_key: str) -> None:
        properties = []
        for name in FIELDS:
            is_text = name in ("cacheKey", "chunkId", "sourceId", "ticker")
            properties.append({"name": name, "dataType": ["text" if is_text else "int"],
                               **({"tokenization": "field"} if is_text else {})})
        self._request("/v1/schema", data={"class": COLLECTION, "description": "marketlens-pilot:" + cache_key,
                                         "vectorizer": "none", "vectorIndexType": "flat",
                                         "vectorIndexConfig": {"distance": "cosine"}, "properties": properties})

    def insert_windows(self, objects: list[dict]) -> None:
        result = self._request("/v1/batch/objects", data={"objects": objects})
        if (not isinstance(result, list) or len(result) != len(objects)
                or any(not isinstance(item, dict) or (item.get("result") or {}).get("status") != "SUCCESS"
                       for item in result)):
            raise WeaviateError("Weaviate batch import was incomplete or uncertain")


def bootstrap_collection(client: WeaviateClient, local_index) -> WeaviateIndex:
    """Import only missing cached vectors; never replace mismatched data."""
    if len(local_index.chunks) != 3922 or len(local_index.layout) != 8279:
        raise IndexError("The fixed pilot dimensions do not match")
    try:
        schema = client.schema()
    except WeaviateError as exc:
        if exc.status != 404:
            raise
        client.create_collection(local_index.cache_key)
        schema = client.schema()
    config = schema.get("vectorIndexConfig", {})
    if (schema.get("description") != "marketlens-pilot:" + local_index.cache_key
            or schema.get("vectorIndexType") != "flat" or schema.get("vectorizer") != "none"
            or config.get("distance") != "cosine"
            or any(config.get(kind, {}).get("enabled", False) for kind in ("bq", "rq", "pq", "sq"))):
        raise IndexError("Existing collection has a different identity or index")
    expected = expected_windows(local_index)
    seen = set()
    for record in client.list_windows():
        ident = record.get("id")
        properties = expected.get(ident)
        if properties is None or ident in seen or record.get("properties") != properties:
            raise IndexError("Existing window metadata does not match the cache")
        vector = record.get("vector")
        original = local_index.vectors[properties["windowIndex"]]
        if (not isinstance(vector, list) or len(vector) != len(original)
                or any(type(a) not in (int, float) or not math.isfinite(a) or abs(float(a) - float(b)) > 1e-5
                       for a, b in zip(vector, original))):
            raise IndexError("Existing window vector does not match the cache")
        seen.add(ident)
    missing = [ident for ident in expected if ident not in seen]
    for offset in range(0, len(missing), 50):
        objects = []
        for ident in missing[offset:offset + 50]:
            properties = expected[ident]
            objects.append({"class": COLLECTION, "id": ident, "properties": properties,
                            "vector": [float(x) for x in local_index.vectors[properties["windowIndex"]]]})
        client.insert_windows(objects)
    index = WeaviateIndex(local_index, client)
    index.verify_collection()
    return index
