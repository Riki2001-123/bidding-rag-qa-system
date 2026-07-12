"""
边界条件测试：P1 语义相似度门控 + P2 实体记忆。

测试场景：
1. session_id = None：所有模块应优雅降级
2. 空历史消息：应跳过改写、全新检索
3. 首轮查询：无上轮缓存，门控应返回 full_search
4. 连续相似查询：门控应返回 reuse
5. 不同域查询：门控应返回 full_search
6. 实体提取：书名号、引号、正则匹配
7. 实体数量限制：超过 30 个应截断
8. LRU 淘汰：超过 500 个 session 应清理
9. 线程安全：并发访问不应崩溃
"""

import sys
import os
import threading
import time

# 确保可以导入项目模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_entity_memory_none_session():
    """session_id=None 应返回临时空记忆，不报错。"""
    from app.services.conversation_memory import conversation_memory, EntityMemory

    result = conversation_memory.get_entity_context(None)
    assert result == "", f"Expected empty string, got: {result}"

    conversation_memory.update_after_answer(None, "测试问题", "测试回答")  # 不应报错

    mem = conversation_memory.get_memory(None)
    assert isinstance(mem, EntityMemory)
    assert len(mem.entities) == 0

    print("[PASS] test_entity_memory_none_session")


def test_entity_extraction():
    """测试实体提取规则。"""
    from app.services.conversation_memory import conversation_memory

    # 书名号
    conversation_memory.update_after_answer(99999, "《政府采购法》的适用范围", "回答内容")
    mem = conversation_memory.get_memory(99999)
    entities = mem.entities
    assert "政府采购法" in entities, f"Expected '政府采购法', got: {entities}"
    assert entities["政府采购法"] == "policy"

    # 引号
    conversation_memory.update_after_answer(99999, '"华为技术有限公司"中标了什么', "回答")
    mem = conversation_memory.get_memory(99999)
    assert "华为技术有限公司" in mem.entities
    assert mem.entities["华为技术有限公司"] == "enterprise"

    conversation_memory.clear_session(99999)
    print("[PASS] test_entity_extraction")


def test_entity_trimming():
    """超过 30 个实体应截断。"""
    from app.services.conversation_memory import conversation_memory

    session_id = 88888
    for i in range(40):
        conversation_memory.update_after_answer(session_id, f"测试项目{i}", "回答")

    mem = conversation_memory.get_memory(session_id)
    assert len(mem.entities) <= 30, f"Expected <= 30 entities, got: {len(mem.entities)}"

    conversation_memory.clear_session(session_id)
    print("[PASS] test_entity_trimming")


def test_retrieval_gate_no_cache():
    """无上轮缓存时，门控应返回 full_search。"""
    from app.services.retrieval_gate import check_retrieval_gate, RetrievalGateResult
    import numpy as np

    session_id = 77777
    dummy_embedding = np.random.randn(1024).astype("float32")
    dummy_embedding /= np.linalg.norm(dummy_embedding)

    result = check_retrieval_gate(session_id, "测试查询", dummy_embedding)
    assert result.action == "full_search", f"Expected full_search, got: {result.action}"

    print("[PASS] test_retrieval_gate_no_cache")


def test_retrieval_gate_none_session():
    """session_id=None 时门控应返回 full_search。"""
    from app.services.retrieval_gate import check_retrieval_gate
    import numpy as np

    dummy_embedding = np.random.randn(1024).astype("float32")
    result = check_retrieval_gate(None, "测试", dummy_embedding)
    assert result.action == "full_search"

    print("[PASS] test_retrieval_gate_none_session")


def test_retrieval_gate_similarity():
    """相似度计算应正确。"""
    from app.services.retrieval_gate import _cosine_similarity, check_retrieval_gate, update_retrieval_cache
    import numpy as np

    session_id = 66666

    # 创建两个相同向量
    vec = np.random.randn(1024).astype("float32")
    vec /= np.linalg.norm(vec)

    # 先缓存一个查询
    update_retrieval_cache(session_id, "初始查询", vec, [{"mock": "data"}])

    # 用相同向量查询 → 相似度应为 1.0，触发 reuse
    result = check_retrieval_gate(session_id, "初始查询", vec)
    assert result.action == "reuse", f"Expected reuse (sim=1.0), got: {result.action} (sim={result.similarity:.4f})"

    # 用正交向量查询 → 相似度应为 ~0，触发 full_search
    orthogonal = np.random.randn(1024).astype("float32")
    orthogonal -= np.dot(orthogonal, vec) * vec  # Gram-Schmidt
    orthogonal /= np.linalg.norm(orthogonal)
    result2 = check_retrieval_gate(session_id, "完全不相关", orthogonal)
    assert result2.action == "full_search", f"Expected full_search (sim~0), got: {result2.action} (sim={result2.similarity:.4f})"

    from app.services.conversation_memory import conversation_memory
    conversation_memory.clear_session(session_id)
    print("[PASS] test_retrieval_gate_similarity")


def test_cosine_similarity_edge_cases():
    """余弦相似度边界条件。"""
    from app.services.retrieval_gate import _cosine_similarity
    import numpy as np

    # 零向量
    zero = np.zeros(10, dtype="float32")
    vec = np.ones(10, dtype="float32")
    assert _cosine_similarity(zero, vec) == 0.0

    # 两个零向量
    assert _cosine_similarity(zero, zero) == 0.0

    # 相同向量
    assert abs(_cosine_similarity(vec, vec) - 1.0) < 1e-6

    print("[PASS] test_cosine_similarity_edge_cases")


def test_lru_eviction():
    """超过 500 个 session 应淘汰最早的。"""
    from app.services.conversation_memory import ConversationMemoryManager

    manager = ConversationMemoryManager()
    # 手动填充 501 个 session
    for i in range(501):
        manager.get_memory(i)

    assert len(manager._memories) == 500, f"Expected 500, got: {len(manager._memories)}"
    # 最早的 session 0 应该被淘汰
    assert 0 not in manager._memories, "Session 0 should have been evicted"
    assert 500 in manager._memories, "Session 500 should exist"

    print("[PASS] test_lru_eviction")


def test_thread_safety():
    """并发访问不应崩溃。"""
    from app.services.conversation_memory import ConversationMemoryManager

    manager = ConversationMemoryManager()
    errors = []

    def worker(session_id):
        try:
            for _ in range(50):
                ctx = manager.get_entity_context(session_id)
                manager.update_after_answer(session_id, f"问题{session_id}", f"回答{session_id}")
                manager.get_memory(session_id)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i % 20,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Thread safety errors: {errors}"
    print("[PASS] test_thread_safety")


def test_query_rewriter_entity_context():
    """rewrite_query 接受 entity_context 参数不应报错。"""
    from app.services.query_rewriter import rewrite_query

    # 无历史 + 有 entity_context → 应跳过改写
    result = rewrite_query("测试问题", None, "已识别的实体：\n- 华为（enterprise）")
    assert result.rewritten == "测试问题"
    assert result.is_coreference is False

    # 无历史 + 无 entity_context → 正常
    result2 = rewrite_query("测试问题", None)
    assert result2.rewritten == "测试问题"

    print("[PASS] test_query_rewriter_entity_context")


def test_retrieval_gate_dataclass_import():
    """确保 chat_agents.py 能正确导入 _evidence_items_from_evidence。"""
    try:
        from app.services.chat_agents import _evidence_items_from_evidence
        print("[PASS] test_retrieval_gate_dataclass_import")
    except ImportError as e:
        print(f"[FAIL] test_retrieval_gate_dataclass_import: {e}")
        raise


if __name__ == "__main__":
    tests = [
        test_entity_memory_none_session,
        test_entity_extraction,
        test_entity_trimming,
        test_retrieval_gate_no_cache,
        test_retrieval_gate_none_session,
        test_retrieval_gate_similarity,
        test_cosine_similarity_edge_cases,
        test_lru_eviction,
        test_thread_safety,
        test_query_rewriter_entity_context,
        test_retrieval_gate_dataclass_import,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"测试结果: {passed} passed, {failed} failed, {passed + failed} total")
    if failed == 0:
        print("所有测试通过!")
    else:
        sys.exit(1)
