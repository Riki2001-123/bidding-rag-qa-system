import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.sql_agent import detect_sql_intent
from app.services.retrieval import extract_relevant_snippet
from app.services.structured_retrieval import detect_structured_intent, extract_entity, extract_industry


class StructuredRetrievalRulesTestCase(unittest.TestCase):
    def test_extracts_company_entity(self):
        entity = extract_entity("请介绍一下“中建海峡建设发展有限公司”这家企业的基本信息。")
        self.assertEqual(entity, "中建海峡建设发展有限公司")

    def test_extracts_hospital_entity(self):
        entity = extract_entity("蚌埠医学院第一附属医院中标了哪些项目？")
        self.assertEqual(entity, "蚌埠医学院第一附属医院")

    def test_extracts_research_institute_entity(self):
        entity = extract_entity("安徽省现代农业工程设计研究院代理过哪些项目？中标方分别是谁？")
        self.assertEqual(entity, "安徽省现代农业工程设计研究院")

    def test_tender_agency_intent(self):
        intent = detect_structured_intent("安徽省交控建设管理有限公司代理过哪些项目？中标方分别是谁？", "tender")
        self.assertEqual(intent["intent"], "tender_by_agency")
        self.assertEqual(intent["field"], "agency")
        self.assertEqual(intent["entity"], "安徽省交控建设管理有限公司")

    def test_tender_winner_intent(self):
        intent = detect_structured_intent("蚌埠医学院第一附属医院中标了哪些项目？", "tender")
        self.assertEqual(intent["intent"], "tender_by_winner")
        self.assertEqual(intent["field"], "winner")

    def test_tender_tenderer_intent(self):
        intent = detect_structured_intent("安徽农业大学作为采购人发布过哪些项目？", "tender")
        self.assertEqual(intent["intent"], "tender_by_tenderer")
        self.assertEqual(intent["field"], "tenderer")

    def test_quoted_tender_title_beats_agency_or_winner_keywords(self):
        intent = detect_structured_intent('标书"安徽职业技术学院2021图书馆电子资源采购"的中标方和代理机构分别是谁？', "tender")
        self.assertEqual(intent["intent"], "tender_detail")
        self.assertEqual(intent["field"], "project")
        self.assertEqual(intent["entity"], "安徽职业技术学院2021图书馆电子资源采购")

    def test_enterprise_scope_intent(self):
        intent = detect_structured_intent("中建海峡建设发展有限公司的主要业务领域是什么？它擅长哪些方向？", "enterprise")
        self.assertEqual(intent["intent"], "enterprise_scope_match")
        self.assertEqual(intent["field"], "business_scope")

    def test_enterprise_detail_intent(self):
        intent = detect_structured_intent("请介绍一下中建海峡建设发展有限公司这家企业的基本信息。", "enterprise")
        self.assertEqual(intent["intent"], "enterprise_detail")
        self.assertEqual(intent["field"], "enterprise_name")

    def test_enterprise_industry_intent(self):
        intent = detect_structured_intent("列举几家资本市场服务行业的企业。", "enterprise")
        self.assertEqual(intent["intent"], "enterprise_by_industry")
        self.assertEqual(intent["field"], "industry")
        self.assertEqual(intent["entity"], "资本市场服务")

    def test_generic_enterprise_question_does_not_extract_entity(self):
        intent = detect_structured_intent("基于给定的信息，请描述这家公司的主要业务领域及其专长方向。", "enterprise")
        self.assertEqual(intent["intent"], "")
        self.assertEqual(intent["entity"], "")

    def test_extract_industry(self):
        self.assertEqual(extract_industry("目前系统中收录了多少家资本市场服务行业的企业？"), "资本市场服务")

    def test_sql_tender_count_uses_winner_for_bid_question(self):
        intent = detect_sql_intent("蚌埠医学院第一附属医院中标了几个项目？", "tender")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["type"], "count")
        self.assertEqual(intent["filter_field"], "winner")
        self.assertEqual(intent["filter_value"], "蚌埠医学院第一附属医院")
        self.assertEqual(intent["extra_filters"]["stage"], "中标")

    def test_sql_tender_list_uses_winner_for_bid_projects_question(self):
        intent = detect_sql_intent("蚌埠医学院第一附属医院中标了哪些项目？分别是什么类型的？", "tender")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["type"], "list")
        self.assertEqual(intent["filter_field"], "tenderer")
        self.assertEqual(intent["filter_value"], "蚌埠医学院第一附属医院")

    def test_sql_enterprise_count_uses_industry(self):
        intent = detect_sql_intent("目前系统中收录了多少家资本市场服务行业的企业？", "enterprise")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["type"], "count")
        self.assertEqual(intent["filter_field"], "industry")
        self.assertEqual(intent["filter_value"], "资本市场服务")

    def test_policy_snippet_prefers_question_keyword(self):
        content = "开头无关内容。" * 30 + "第七十二条 供应商违法的，处5万元以上25万元以下罚款。" + "结尾无关内容。" * 30
        snippet = extract_relevant_snippet(content, "违反政府采购法第七十二条罚款金额是多少？")
        self.assertIn("第七十二条", snippet)
        self.assertIn("5万元以上25万元以下", snippet)


if __name__ == "__main__":
    unittest.main()
