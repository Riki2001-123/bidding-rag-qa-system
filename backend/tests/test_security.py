import hashlib
import sys
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from app.db.init_db import initialize_database
from app.db.session import SessionLocal
from app.main import app
from app.models.entities import User
from app.services.security import create_token, decode_token, hash_password, needs_password_rehash, verify_password


class SecurityServiceTestCase(unittest.TestCase):
    def test_hash_password_uses_pbkdf2_and_verifies(self):
        password_hash = hash_password("secret-value")

        self.assertTrue(password_hash.startswith("pbkdf2_sha256$"))
        self.assertTrue(verify_password("secret-value", password_hash))
        self.assertFalse(verify_password("wrong-value", password_hash))
        self.assertFalse(needs_password_rehash(password_hash))

    def test_verify_password_supports_legacy_sha256_hash(self):
        legacy_hash = hashlib.sha256("legacy-pass".encode("utf-8")).hexdigest()

        self.assertTrue(verify_password("legacy-pass", legacy_hash))
        self.assertTrue(needs_password_rehash(legacy_hash))

    def test_decode_token_rejects_expired_tokens(self):
        with patch("app.services.security.time.time", return_value=1000):
            token = create_token(user_id=1, username="admin", role="admin")

        with patch("app.services.security.time.time", return_value=1000):
            payload = decode_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["user_id"], 1)
        self.assertEqual(payload["username"], "admin")
        self.assertEqual(payload["role"], "admin")

        with patch(
            "app.services.security.time.time",
            return_value=1000 + settings.access_token_ttl_seconds + 1,
        ):
            self.assertIsNone(decode_token(token))


class AuthCompatibilityTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_database()
        cls.client = TestClient(app)

    def test_login_rehashes_legacy_password_hash(self):
        username = f"legacy_{uuid4().hex[:8]}"
        password = "legacy-pass"

        with SessionLocal() as db:
            db.add(
                User(
                    username=username,
                    password_hash=hashlib.sha256(password.encode("utf-8")).hexdigest(),
                    role="internal",
                    display_name=username,
                    is_active=True,
                )
            )
            db.commit()

        response = self.client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)

        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.username == username))
            self.assertTrue(user.password_hash.startswith("pbkdf2_sha256$"))
            self.assertTrue(verify_password(password, user.password_hash))


if __name__ == "__main__":
    unittest.main()
