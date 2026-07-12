import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.init_db import initialize_database
from app.db.session import SessionLocal
from app.models.entities import ChatMessage, ChatSession, User
from app.services.chat import answer_question
from app.services.chat_agents import AgentDecision, AgentRunResult


class DummyOrchestrator:
    def __init__(self):
        self.calls = []

    def orchestrate(self, db, user, question, preferred_domain, top_k, history_messages):
        self.calls.append(
            {
                "user_id": user.id,
                "question": question,
                "preferred_domain": preferred_domain,
                "top_k": top_k,
                "history_messages": list(history_messages),
            }
        )
        return AgentRunResult(
            domain="policy",
            answer="ok",
            citations=[],
            conservative=False,
            decision=AgentDecision(domain="policy", intent="fact", reason="test", confidence=0.9),
        )


class ChatServiceSecurityTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_database()

    def test_foreign_session_id_does_not_leak_history(self):
        orchestrator = DummyOrchestrator()

        with SessionLocal() as db:
            owner = db.scalar(select(User).where(User.username == "admin"))
            other_user = db.scalar(select(User).where(User.username == "internal"))

            foreign_session = ChatSession(user_id=owner.id, title="foreign-session")
            db.add(foreign_session)
            db.flush()
            db.add(ChatMessage(session_id=foreign_session.id, role="user", question_domain="policy", content="secret"))
            db.commit()

            with patch("app.services.chat.get_chat_orchestrator", return_value=orchestrator):
                result = answer_question(
                    db=db,
                    user=other_user,
                    question="follow up",
                    domain=None,
                    session_id=foreign_session.id,
                    top_k=5,
                )

            db.expire_all()
            foreign_messages = db.scalars(
                select(ChatMessage).where(ChatMessage.session_id == foreign_session.id).order_by(ChatMessage.id.asc())
            ).all()

        self.assertEqual(orchestrator.calls[0]["history_messages"], [])
        self.assertNotEqual(result["session_id"], foreign_session.id)
        self.assertEqual(len(foreign_messages), 1)
        self.assertEqual(foreign_messages[0].content, "secret")

    def test_owned_session_keeps_history_and_reuses_session(self):
        orchestrator = DummyOrchestrator()

        with SessionLocal() as db:
            owner = db.scalar(select(User).where(User.username == "admin"))

            session = ChatSession(user_id=owner.id, title="owned-session")
            db.add(session)
            db.flush()
            db.add(ChatMessage(session_id=session.id, role="user", question_domain="policy", content="history-1"))
            db.add(ChatMessage(session_id=session.id, role="assistant", question_domain="policy", content="history-2"))
            db.commit()

            with patch("app.services.chat.get_chat_orchestrator", return_value=orchestrator):
                result = answer_question(
                    db=db,
                    user=owner,
                    question="next question",
                    domain="policy",
                    session_id=session.id,
                    top_k=3,
                )

        self.assertEqual(result["session_id"], session.id)
        self.assertEqual(
            orchestrator.calls[0]["history_messages"],
            [
                {"role": "user", "content": "history-1"},
                {"role": "assistant", "content": "history-2"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
