import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts.evaluate_retrieval_recall import RecallAccumulator, StageResult, parse_top_k
from scripts.evaluate_retrieval_recall import report_has_errors, target_summary
import scripts.remap_recall_gold as remap_gold
from scripts.remap_recall_gold import select_best_candidate, summarize
from app.services.retrieval import _RecordCandidate, add_rrf_candidate, extract_retrieval_keywords, rerank_retrieved_items


class RecallGoldRemapTestCase(unittest.TestCase):
    def test_selects_highest_overlap_candidate(self):
        item = {"source_text": "资本市场服务企业注册信息"}
        candidates = [
            SimpleNamespace(id=1, record_id=101, source_field="summary", content="农业企业注册信息"),
            SimpleNamespace(id=2, record_id=202, source_field="summary", content="资本市场服务企业注册信息及经营范围"),
        ]

        candidate, reason = select_best_candidate(item, candidates)

        self.assertEqual(reason, "matched")
        self.assertEqual(candidate.chunk_id, 2)
        self.assertEqual(candidate.record_id, 202)
        self.assertEqual(candidate.score, 1.0)

    def test_unmatched_when_score_is_too_low(self):
        item = {"source_text": "abcdef"}
        candidates = [
            SimpleNamespace(id=1, record_id=101, source_field="content", content="uvwxyz"),
        ]

        candidate, reason = select_best_candidate(item, candidates, min_score=0.8)

        self.assertIsNone(candidate)
        self.assertEqual(reason, "low_score")

    def test_unmatched_when_candidates_are_ambiguous(self):
        item = {"source_text": "abcdef"}
        candidates = [
            SimpleNamespace(id=1, record_id=101, source_field="content", content="abcde"),
            SimpleNamespace(id=2, record_id=202, source_field="content", content="abcdf"),
        ]

        candidate, reason = select_best_candidate(item, candidates, min_score=0.7)

        self.assertIsNone(candidate)
        self.assertEqual(reason, "ambiguous")

    def test_summary_reports_unmatched_reason_distribution(self):
        summary = summarize([
            {"domain": "policy", "gold_status": "matched"},
            {"domain": "policy", "gold_status": "unmatched", "gold_unmatched_reason": "no_candidate"},
            {"domain": "tender", "gold_status": "unmatched", "gold_unmatched_reason": "low_score"},
        ])

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["unmatched_reasons"], {"no_candidate": 1, "low_score": 1})

    def test_structured_gold_uses_record_level_match_without_source_text(self):
        original_detect = remap_gold.detect_structured_intent
        original_query = remap_gold._query_structured_records
        try:
            remap_gold.detect_structured_intent = lambda question, domain: {"entity": "agency", "field": "agency"}
            remap_gold._query_structured_records = (
                lambda db, domain, source_field, entity, max_candidates: [SimpleNamespace(id=77)]
            )

            candidate, reason = remap_gold.match_structured_record_gold(
                object(),
                {"domain": "tender", "question": "q", "source_field": "agency"},
            )
        finally:
            remap_gold.detect_structured_intent = original_detect
            remap_gold._query_structured_records = original_query

        self.assertEqual(reason, "matched")
        self.assertEqual(candidate.chunk_id, 0)
        self.assertEqual(candidate.record_id, 77)
        self.assertEqual(candidate.method, "structured_record_match")

    def test_contextual_enterprise_chunk_is_not_record_gold(self):
        original_detect = remap_gold.detect_structured_intent
        try:
            remap_gold.detect_structured_intent = lambda question, domain: {"intent": "", "entity": "", "field": ""}
            self.assertTrue(remap_gold.is_contextual_enterprise_item({
                "domain": "enterprise",
                "source_field": "business_scope",
                "chunk_id": 123,
                "question": "该企业是否有资格参与皮革加工相关的工程或服务投标？",
            }))
        finally:
            remap_gold.detect_structured_intent = original_detect


class RetrievalRecallMetricTestCase(unittest.TestCase):
    def test_parse_top_k_sorts_and_deduplicates(self):
        self.assertEqual(parse_top_k("10,1,5,5"), [1, 5, 10])

    def test_accumulator_calculates_chunk_and_record_recall_separately(self):
        accumulator = RecallAccumulator([1, 3])

        accumulator.add(
            "policy",
            "embedding",
            gold_chunk_id=11,
            gold_record_id=101,
            result=StageResult(chunk_ids=[9, 11], record_ids=[202, 101]),
        )
        accumulator.add(
            "policy",
            "embedding",
            gold_chunk_id=12,
            gold_record_id=102,
            result=StageResult(chunk_ids=[12], record_ids=[999]),
        )

        metrics = accumulator.report()
        policy = metrics["policy"]["embedding"]
        overall = metrics["overall"]["embedding"]

        self.assertEqual(policy["n"], 2)
        self.assertEqual(policy["chunk_recall"], {"R@1": 50.0, "R@3": 100.0})
        self.assertEqual(policy["record_recall"], {"R@1": 0.0, "R@3": 50.0})
        self.assertEqual(overall["chunk_recall"]["R@3"], 100.0)

    def test_accumulator_tracks_answer_only_sql_results(self):
        accumulator = RecallAccumulator([1])

        accumulator.add(
            "enterprise",
            "final",
            gold_chunk_id=11,
            gold_record_id=101,
            result=StageResult(answer_only=True),
        )

        metrics = accumulator.report()["enterprise"]["final"]
        self.assertEqual(metrics["n"], 1)
        self.assertEqual(metrics["answer_only"], 1)
        self.assertEqual(metrics["chunk_recall"], {"R@1": 0.0})
        self.assertEqual(metrics["record_recall"], {"R@1": 0.0})

    def test_report_is_invalid_when_any_stage_has_errors(self):
        metrics = {
            "overall": {
                "final": {
                    "record_recall": {"R@20": 95.0},
                    "errors": 0,
                },
                "embedding": {
                    "record_recall": {"R@20": 0.0},
                    "errors": 1,
                },
            }
        }

        self.assertTrue(report_has_errors(metrics))
        target = target_summary(metrics)
        self.assertFalse(target["valid"])
        self.assertFalse(target["passed"])
        self.assertEqual(target["invalid_reason"], "stage_errors_present")

    def test_report_is_invalid_when_vector_index_is_incomplete(self):
        metrics = {
            "overall": {
                "final": {
                    "record_recall": {"R@20": 95.0},
                    "errors": 0,
                }
            }
        }
        vector_index = {"enterprise": {"complete": False}}

        target = target_summary(metrics, vector_index)

        self.assertFalse(target["valid"])
        self.assertFalse(target["passed"])
        self.assertEqual(target["invalid_reason"], "vector_index_incomplete")

    def test_enterprise_target_does_not_require_complete_vector_index(self):
        metrics = {
            "overall": {
                "final": {
                    "record_recall": {"R@20": 95.0},
                    "errors": 0,
                }
            }
        }
        vector_index = {"enterprise": {"complete": False}, "policy": {"complete": True}}

        target = target_summary(metrics, vector_index, domain="enterprise")

        self.assertTrue(target["valid"])
        self.assertTrue(target["passed"])
        self.assertEqual(target["vector_required_domains"], [])


class HighRecallRetrievalTestCase(unittest.TestCase):
    def test_rrf_candidate_aggregates_sources_and_fields(self):
        candidates = {}
        diagnostics = {"sources": {}}

        add_rrf_candidate(candidates, diagnostics, 101, "vector", 1, source_fields=["content"], chunk_id=11)
        add_rrf_candidate(candidates, diagnostics, 101, "structured", 1, source_fields=["winner"])

        candidate = candidates[101]
        self.assertGreater(candidate.score, 0)
        self.assertEqual(candidate.sources, ["vector", "structured"])
        self.assertEqual(candidate.source_fields, ["content", "winner"])
        self.assertEqual(candidate.chunk_ids, [11])
        self.assertEqual(diagnostics["sources"], {"vector": 1, "structured": 1})

    def test_structured_candidate_weight_beats_same_rank_vector(self):
        candidates = {}
        diagnostics = {"sources": {}}

        add_rrf_candidate(candidates, diagnostics, 101, "vector", 1)
        add_rrf_candidate(candidates, diagnostics, 202, "structured", 1)

        ranked = sorted(candidates.values(), key=lambda item: item.score, reverse=True)
        self.assertEqual([item.record_id for item in ranked], [202, 101])

    def test_keyword_extraction_includes_long_phrases_and_ngrams(self):
        keywords = extract_retrieval_keywords("第七十二条规定15个工作日内应当公示哪些内容？")

        self.assertTrue(any("第七十二条" in keyword for keyword in keywords))
        self.assertTrue(any(len(keyword) >= 4 for keyword in keywords))

    def test_rerank_disabled_keeps_fused_top_k(self):
        items = [
            SimpleNamespace(record_id=index, title=f"title {index}", summary="", score=1.0, source_fields=[])
            for index in range(30)
        ]

        selected = rerank_retrieved_items("query", items, top_k=20, use_reranker=False)

        self.assertEqual(len(selected), 20)
        self.assertEqual([item.record_id for item in selected[:3]], [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
