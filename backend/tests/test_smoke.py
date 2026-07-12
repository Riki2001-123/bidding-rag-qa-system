import os
import sys
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

os.environ.setdefault("UNITTEST_RUNNING", "1")

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.init_db import initialize_database
from app.db.session import SessionLocal
from app.main import app
from app.models.entities import PolicyRecord, TenderRecord


class SmokeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_database()
        with SessionLocal() as db:
            policy = db.scalar(select(PolicyRecord).where(PolicyRecord.external_id == "SMOKE_POLICY_RECORD"))
            if not policy:
                policy = PolicyRecord(external_id="SMOKE_POLICY_RECORD")
                db.add(policy)
            policy.title = "中华人民共和国招标投标法实施条例"
            policy.publish_date = date(2024, 1, 1)
            policy.region = "全国"
            policy.scope = "工程建设项目"
            policy.summary = "规范招标投标活动"
            policy.content = "本条例用于规范招标投标活动，提升采购透明度。"
            policy.access_level = "public"

            tender = db.scalar(select(TenderRecord).where(TenderRecord.external_id == "SMOKE_TENDER_RECORD"))
            if not tender:
                tender = TenderRecord(external_id="SMOKE_TENDER_RECORD")
                db.add(tender)
            tender.title = "Smoke 招标金额测试项目"
            tender.project_name = "Smoke 招标金额测试项目"
            tender.tenderer = "Smoke 采购单位"
            tender.stage = "中标"
            tender.region = "全国"
            tender.bid_amount = 1000000000000.0
            tender.content_summary = "用于测试最大中标金额直查能力"
            tender.access_level = "public"

            db.commit()

        cls.client = TestClient(app)
        login = cls.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200, login.text
        cls.token = login.json()["access_token"]
        cls.headers = {"Authorization": f"Bearer {cls.token}"}

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)

    def test_policy_search(self):
        response = self.client.get("/api/search/policy", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertGreaterEqual(len(payload["items"]), 1)

    def test_search_all(self):
        response = self.client.get("/api/search/all?q=Smoke&top_k=5", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("items", payload)
        self.assertTrue(any(item["domain"] == "tender" for item in payload["items"]))

    def test_chat_with_explicit_domain(self):
        response = self.client.post(
            "/api/chat/query",
            headers=self.headers,
            json={"question": "招标投标活动适用什么条例？", "domain": "policy"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("answer", payload)
        self.assertEqual(payload["domain"], "policy")

    def test_chat_without_manual_domain(self):
        response = self.client.post(
            "/api/chat/query",
            headers=self.headers,
            json={"question": "招标投标活动适用什么条例？"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("answer", payload)
        self.assertEqual(payload["domain"], "policy")

    def test_tender_max_amount_direct_answer(self):
        response = self.client.post(
            "/api/chat/query",
            headers=self.headers,
            json={"question": "招标金额最大是多少", "domain": "tender"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["domain"], "tender")
        self.assertIn("1,000,000,000,000", payload["answer"])
        self.assertTrue(payload["citations"])
        self.assertEqual(payload["citations"][0]["title"], "Smoke 招标金额测试项目")


if __name__ == "__main__":
    unittest.main()
