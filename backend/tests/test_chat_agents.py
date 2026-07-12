import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.chat_agents import (
    AgentDecision,
    ChatOrchestrator,
    EnterpriseAgent,
    JudgeAgent,
    PolicyAgent,
    TenderAgent,
)
from app.services.retrieval import RetrievedItem


class JudgeAgentTestCase(unittest.TestCase):
    def test_policy_question_routes_to_policy(self):
        agent = JudgeAgent()

        with patch("app.services.chat_agents.get_llm_client", return_value=None):
            decision = agent.judge(
                question="政府采购法规定的供应商资格条件有哪些？",
                preferred_domain=None,
                user_role="admin",
                history_messages=[],
            )

        self.assertEqual(decision.domain, "policy")
        self.assertEqual(decision.intent, "filter")
        self.assertFalse(decision.cross_domain_candidate)

    def test_cross_domain_question_is_flagged(self):
        agent = JudgeAgent()

        with patch("app.services.chat_agents.get_llm_client", return_value=None):
            decision = agent.judge(
                question="这家公司中标过哪些项目，是否符合政府采购资格条件？",
                preferred_domain=None,
                user_role="admin",
                history_messages=[],
            )

        self.assertEqual(decision.domain, "tender")
        self.assertTrue(decision.cross_domain_candidate)
        self.assertIn("enterprise", decision.candidate_domains)
        self.assertIn("policy", decision.candidate_domains)

    def test_low_confidence_falls_back_to_preferred_domain(self):
        agent = JudgeAgent()

        with patch("app.services.chat_agents.get_llm_client", return_value=None):
            decision = agent.judge(
                question="帮我看看这个",
                preferred_domain="enterprise",
                user_role="admin",
                history_messages=[],
            )

        self.assertEqual(decision.domain, "enterprise")
        self.assertTrue(decision.low_confidence)

    def test_llm_can_override_rule_route(self):
        agent = JudgeAgent()
        fake_llm = RunnableLambda(
            lambda _: AIMessage(
                content=(
                    '{"domain":"enterprise","primary_domain":"enterprise","intent":"association",'
                    '"confidence":0.88,"reason":"问题核心是企业主体识别。",'
                    '"cross_domain_candidate":false,"candidate_domains":["enterprise","tender"]}'
                )
            )
        )

        with patch("app.services.chat_agents.get_llm_client", return_value=fake_llm):
            decision = agent.judge(
                question="这家公司和另一家公司是不是同一主体？",
                preferred_domain=None,
                user_role="admin",
                history_messages=[],
            )

        self.assertEqual(decision.domain, "enterprise")
        self.assertEqual(decision.intent, "association")
        self.assertAlmostEqual(decision.confidence, 0.88)


class BusinessAgentTestCase(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=1, role="admin")
        self.decision = AgentDecision(domain="tender", intent="fact", reason="test", confidence=0.9)

    def test_tender_agent_answers_direct_max_amount_question(self):
        db = MagicMock()
        record = SimpleNamespace(
            id=7,
            title="超大金额项目",
            project_name="超大金额项目",
            tenderer="示例采购人",
            stage="中标",
            region="全国",
            bid_amount=1000000000000.0,
            source_url="https://example.com/tender/7",
            publish_date="2026-04-01",
        )
        db.scalars.return_value.first.return_value = record

        with patch("app.services.chat_agents.get_attachments", return_value=[]):
            result = TenderAgent().run(
                db=db,
                user=self.user,
                question="中标金额最大的项目是哪个？",
                top_k=5,
                history_messages=[],
                decision=self.decision,
            )

        self.assertFalse(result.conservative)
        self.assertEqual(result.domain, "tender")
        self.assertIn("1,000,000,000,000", result.answer)
        self.assertIn("直接结论", result.answer)
        self.assertEqual(len(result.citations), 1)

    def test_policy_agent_uses_search_and_llm(self):
        item = RetrievedItem(
            domain="policy",
            record_id=3,
            title="政府采购法",
            score=0.9,
            summary="供应商资格条件摘要",
            publish_date=None,
            key_fields={"region": "全国", "scope": "政府采购"},
            source_fields=["structured"],
        )
        decision = AgentDecision(
            domain="policy",
            intent="fact",
            reason="test",
            confidence=0.9,
            cross_domain_candidate=True,
            candidate_domains=("policy", "enterprise"),
        )

        with patch("app.services.chat_agents.search_domain", return_value=[item]), patch(
            "app.services.chat_agents.get_attachments", return_value=[]
        ), patch(
            "app.services.chat_agents.llm_service.generate_answer",
            return_value="直接结论：满足资格条件。\n\n依据说明：依据政策条款。\n\n补充说明：需结合企业材料复核。",
        ) as mocked_generate:
            result = PolicyAgent().run(
                db=MagicMock(),
                user=self.user,
                question="政府采购法规定的供应商资格条件有哪些？",
                top_k=5,
                history_messages=[],
                decision=decision,
            )

        self.assertFalse(result.conservative)
        self.assertEqual(result.domain, "policy")
        self.assertEqual(len(result.citations), 1)
        self.assertIn("跨域特征", result.answer)
        mocked_generate.assert_called_once()

    def test_enterprise_agent_handles_empty_results_conservatively(self):
        decision = AgentDecision(domain="enterprise", intent="fact", reason="test", confidence=0.5, low_confidence=True)

        with patch("app.services.chat_agents.search_domain", return_value=[]), patch(
            "app.services.chat_agents.llm_service.generate_answer",
            return_value="直接结论：当前未检索到足够证据。\n\n依据说明：暂无相关企业记录。\n\n补充说明：请补充企业名称或统一社会信用代码。",
        ):
            result = EnterpriseAgent().run(
                db=MagicMock(),
                user=self.user,
                question="查一下这家公司",
                top_k=5,
                history_messages=[],
                decision=decision,
            )

        self.assertTrue(result.conservative)
        self.assertEqual(result.domain, "enterprise")
        self.assertEqual(result.citations, [])

    def test_cross_domain_orchestrator_queries_multiple_domains(self):
        policy_item = RetrievedItem(
            domain="policy",
            record_id=11,
            title="招标投标法实施条例",
            score=0.95,
            summary="规定招标投标活动适用的法律法规要求。",
            publish_date=None,
            key_fields={"region": "全国", "scope": "招标投标"},
            source_fields=["content"],
        )
        tender_item = RetrievedItem(
            domain="tender",
            record_id=12,
            title="某项目招标公告",
            score=0.81,
            summary="展示实际招标项目的公告信息。",
            publish_date=None,
            key_fields={"project_name": "示例项目", "region": "全国"},
            source_fields=["content_summary"],
        )
        decision = AgentDecision(
            domain="policy",
            intent="fact",
            reason="test",
            confidence=0.92,
            cross_domain_candidate=True,
            candidate_domains=("policy", "tender"),
        )
        judge_agent = MagicMock()
        judge_agent.judge.return_value = decision
        orchestrator = ChatOrchestrator(judge_agent=judge_agent)

        def _search_side_effect(*, db, domain, user, q, top_k):
            _ = db, user, q, top_k
            return [policy_item] if domain == "policy" else [tender_item]

        with patch("app.services.chat_agents.search_domain", side_effect=_search_side_effect), patch(
            "app.services.chat_agents.get_attachments", return_value=[]
        ), patch(
            "app.services.chat_agents.llm_service.generate_answer",
            return_value="直接结论：该问题需要同时参考政策规定和招标业务事实。",
        ) as mocked_generate:
            result = orchestrator.orchestrate(
                db=MagicMock(),
                user=self.user,
                question="招标投标活动适用什么法律法规",
                preferred_domain=None,
                top_k=5,
                history_messages=[],
            )

        self.assertEqual(result.domain, "policy")
        self.assertFalse(result.conservative)
        self.assertEqual([citation.domain for citation in result.citations], ["policy", "tender"])
        self.assertIn("跨域特征", result.answer)
        mocked_generate.assert_called_once()
        contexts = mocked_generate.call_args[0][3]
        self.assertEqual(len(contexts), 2)
        self.assertTrue(contexts[0]["title"].startswith("[policy]"))
        self.assertTrue(contexts[1]["title"].startswith("[tender]"))


if __name__ == "__main__":
    unittest.main()
