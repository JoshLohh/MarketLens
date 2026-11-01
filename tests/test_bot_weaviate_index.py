"""Synthetic checks for exact vector-window identity and score folding."""

import unittest
from types import SimpleNamespace

from marketlens_bot.weaviate_index import IndexError, WeaviateIndex, expected_windows


class Encoder:
    def encode_query(self, _question):
        return [1.0, 0.0]


class Client:
    def __init__(self, local):
        self.local = local
        self.expected = expected_windows(local)
        self.records = [{"id": ident, "properties": item, "vector": local.vectors[item["windowIndex"]]}
                        for ident, item in self.expected.items()]
        self.matches = []

    def schema(self):
        return {"vectorIndexType": "flat", "vectorizer": "none",
                "vectorIndexConfig": {"distance": "cosine"},
                "description": "marketlens-pilot:" + self.local.cache_key}

    def list_windows(self):
        return self.records

    def search_windows(self, ticker, vector, limit):
        return self.matches


def fixture():
    local = SimpleNamespace(cache_key="cache-key", encoder=Encoder(), lexical=None,
                            chunks=[{"chunk_id": "a", "source_id": "s", "ticker": "NBIS"},
                                    {"chunk_id": "b", "source_id": "s", "ticker": "NBIS"}],
                            layout=[{"chunk_index": 0, "start": 0, "end": 4, "token_count": 5},
                                    {"chunk_index": 0, "start": 2, "end": 5, "token_count": 5},
                                    {"chunk_index": 1, "start": 0, "end": 3, "token_count": 3}],
                            vectors=[[1.0, 0.0], [0.0, 1.0], [0.6, 0.8]])
    client = Client(local)
    return local, client, WeaviateIndex(local, client)


class WeaviateIndexTests(unittest.TestCase):
    def test_startup_refuses_incomplete_or_changed_collection(self):
        local, client, index = fixture()
        client.records.pop()
        with self.assertRaises(IndexError):
            index.verify_collection()
        with self.assertRaises(IndexError):
            index.dense_search("NBIS", "question")
        client = Client(local)
        client.records[0]["vector"] = [0.5, 0.5]
        with self.assertRaises(IndexError):
            WeaviateIndex(local, client).verify_collection()

    def test_all_windows_required_and_max_score_folded_by_passage(self):
        local, client, index = fixture()
        index.verify_collection()
        ids = list(client.expected)
        client.matches = [{"id": ids[0], "distance": 0.4}, {"id": ids[1], "distance": 0.1},
                          {"id": ids[2], "distance": 0.4}]
        result = index.dense_search("NBIS", "question")
        self.assertEqual([row["chunk_id"] for row in result], ["a", "b"])
        self.assertAlmostEqual(result[0]["score"], 0.9)
        client.matches.pop()
        with self.assertRaises(IndexError):
            index.dense_search("NBIS", "question")

    def test_duplicate_search_id_is_rejected(self):
        local, client, index = fixture()
        index.verify_collection()
        ident = next(iter(client.expected))
        client.matches = [{"id": ident, "distance": 0.0}, {"id": ident, "distance": 0.0}]
        with self.assertRaises(IndexError):
            index.dense_search("NBIS", "question")


if __name__ == "__main__":
    unittest.main()
