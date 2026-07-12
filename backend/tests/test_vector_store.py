import sys
import unittest
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.vector_store import VectorStore
from app.core.config import settings


class FakeDataType:
    INT64 = "INT64"
    VARCHAR = "VARCHAR"
    FLOAT_VECTOR = "FLOAT_VECTOR"


class FakeSchema:
    def __init__(self):
        self.fields = []

    def add_field(self, **kwargs):
        self.fields.append(kwargs)


class FakeIndexParams:
    def __init__(self):
        self.indices = []

    def add_index(self, **kwargs):
        self.indices.append(kwargs)


class FakeMilvusClientFactory:
    @staticmethod
    def create_schema(auto_id=False, enable_dynamic_field=False):
        _ = auto_id, enable_dynamic_field
        return FakeSchema()

    @staticmethod
    def prepare_index_params():
        return FakeIndexParams()


class FakeMilvusClient:
    def __init__(self):
        self.collections = {}

    def has_collection(self, collection_name):
        return collection_name in self.collections

    def create_collection(self, collection_name, schema, index_params):
        self.collections[collection_name] = {
            "schema": schema,
            "index_params": index_params,
            "loaded": False,
            "rows": {},
        }

    def load_collection(self, collection_name):
        self.collections[collection_name]["loaded"] = True

    def describe_collection(self, collection_name):
        collection = self.collections[collection_name]
        fields = []
        for field in collection["schema"].fields:
            params = {}
            if "dim" in field:
                params["dim"] = field["dim"]
            if "max_length" in field:
                params["max_length"] = field["max_length"]
            fields.append(
                {
                    "name": field["field_name"],
                    "params": params,
                    "is_primary": field.get("is_primary", False),
                }
            )
        return {"fields": fields}

    def upsert(self, collection_name, data):
        rows = self.collections[collection_name]["rows"]
        if isinstance(data, dict):
            data = [data]
        for row in data:
            rows[row["chunk_id"]] = dict(row)

    def search(self, collection_name, data, anns_field, limit, output_fields, search_params):
        _ = output_fields, search_params
        query = np.asarray(data[0], dtype="float32")
        rows = self.collections[collection_name]["rows"]
        ranked = []
        for row in rows.values():
            vector = np.asarray(row[anns_field], dtype="float32")
            distance = float(np.dot(query, vector))
            ranked.append({"id": row["chunk_id"], "distance": distance, "entity": dict(row)})
        ranked.sort(key=lambda item: item["distance"], reverse=True)
        return [ranked[:limit]]

    def delete(self, collection_name, ids):
        rows = self.collections[collection_name]["rows"]
        for item in ids:
            rows.pop(item, None)
        return {"delete_cnt": len(ids)}

    def drop_collection(self, collection_name):
        self.collections.pop(collection_name, None)


class VectorStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.client = FakeMilvusClient()
        self.store = VectorStore(
            client=self.client,
            client_cls=FakeMilvusClientFactory,
            datatype_cls=FakeDataType,
        )
        self.zero_tail = [0.0] * (settings.embedding_dimension - 2)

    def test_ensure_collections_creates_expected_schema(self):
        self.store.ensure_collections(["policy"])

        self.assertIn("rag_policy", self.client.collections)
        collection = self.client.collections["rag_policy"]
        field_names = [field["field_name"] for field in collection["schema"].fields]
        self.assertEqual(field_names, ["chunk_id", "record_id", "source_field", "chunk_key", "vector"])
        self.assertTrue(collection["loaded"])

    def test_upsert_search_delete_and_rebuild(self):
        self.store.upsert(
            "policy",
            [
                {
                    "chunk_id": 1,
                    "record_id": 101,
                    "source_field": "content",
                    "metadata": {"chunk_key": "policy:101:content:0"},
                    "vector": [1.0, 0.0] + self.zero_tail,
                },
                {
                    "chunk_id": 2,
                    "record_id": 102,
                    "source_field": "content",
                    "metadata": {"chunk_key": "policy:102:content:0"},
                    "vector": [0.0, 1.0] + self.zero_tail,
                },
            ],
        )

        hits = self.store.search("policy", [1.0, 0.0] + self.zero_tail, top_k=2)
        self.assertEqual(hits[0]["chunk_id"], 1)
        self.assertEqual(hits[0]["record_id"], 101)

        self.store.delete("policy", [1])
        hits_after_delete = self.store.search("policy", [1.0, 0.0] + self.zero_tail, top_k=2)
        self.assertTrue(all(hit["chunk_id"] != 1 for hit in hits_after_delete))

        self.store.rebuild(
            "policy",
            [
                {
                    "chunk_id": 9,
                    "record_id": 909,
                    "source_field": "summary",
                    "metadata": {"chunk_key": "policy:909:summary:0"},
                    "vector": [1.0, 1.0] + self.zero_tail,
                }
            ],
        )
        rebuilt_hits = self.store.search("policy", [1.0, 1.0] + self.zero_tail, top_k=2)
        self.assertEqual([hit["chunk_id"] for hit in rebuilt_hits], [9])

    def test_existing_collection_dimension_mismatch_raises(self):
        broken_schema = FakeSchema()
        broken_schema.add_field(field_name="chunk_id", datatype=FakeDataType.INT64, is_primary=True)
        broken_schema.add_field(field_name="record_id", datatype=FakeDataType.INT64)
        broken_schema.add_field(field_name="source_field", datatype=FakeDataType.VARCHAR, max_length=512)
        broken_schema.add_field(field_name="chunk_key", datatype=FakeDataType.VARCHAR, max_length=512)
        broken_schema.add_field(field_name="vector", datatype=FakeDataType.FLOAT_VECTOR, dim=128)
        self.client.create_collection("rag_policy", broken_schema, FakeIndexParams())

        with self.assertRaises(RuntimeError):
            self.store.ensure_collections(["policy"], force=True)


if __name__ == "__main__":
    unittest.main()
