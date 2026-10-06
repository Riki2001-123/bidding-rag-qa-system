import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import Base
from app.models.entities import ChatMessage, ChatSession, PolicyRecord, User
from app.schemas.common import CitationOut
from app.services.chat import answer_question, stream_answer_question
from app.services.chat_agents import ChatOrchestrator, PolicyAgent, _quoted_evidence, _policy_excerpt
from app.services.policy_grounding import (
    PolicyQuote, QuoteDecoder, validate_quote, point_limit, render_answer,
    generate_quotes, stream_quotes, NO_EVIDENCE,
    evidence_passages,
)
from app.services.query_rewriter import rewrite_query

TEXT = '第二条 国家机关使用财政性资金采购目录以内的货物、工程和服务，适用本办法。'
NEGATIVE = '第三条 未经主管部门批准的项目，不得按本办法获得财政补助支持。'
CONTEXTS = [{'title': '测试政策', 'evidence_text': TEXT + '\n' + NEGATIVE}]


@pytest.mark.parametrize('payload', [
    {'source': 1, 'quote': TEXT.replace('财政性资金', '100万元资金')},
    {'source': 9, 'quote': TEXT},
    {'source': True, 'quote': TEXT},
    {'source': 1, 'quote': TEXT, 'explanation': '伪造推断'},
    {'source': 1, 'quote': NEGATIVE[NEGATIVE.index('按本办法'):]},
    {'source': 1, 'quote': TEXT[4:]},
    {'source': 1, 'quote': TEXT[:30]},
])
def test_rejects_fabrication_and_incomplete_qualifiers(payload):
    assert validate_quote(payload, CONTEXTS) is None


def test_decoder_accepts_split_frames_deduplicates_and_limits():
    data = '\n'.join(json.dumps(p, ensure_ascii=False) for p in [
        {'source': 1, 'quote': TEXT}, {'source': 1, 'quote': TEXT},
        {'source': 1, 'quote': NEGATIVE}, {'done': True},
    ])
    decoder = QuoteDecoder(CONTEXTS, 1)
    accepted = []
    for char in data:
        accepted.extend(decoder.feed(char))
    accepted.extend(decoder.feed('', final=True))
    assert accepted == [PolicyQuote(1, TEXT)]


def test_numbered_passages_retain_original_whitespace_and_whole_articles():
    context = {'title': '测试政策', 'evidence_text': TEXT + '  ' + NEGATIVE}
    assert evidence_passages(context) == [TEXT, NEGATIVE]
    decoder = QuoteDecoder([context], 2)
    selected = decoder.feed('{"source":1,"passage_id":1}\n{"source":1,"passage_id":99}\n{"source":true,"passage_id":1}\n{"done":true}', final=True)
    assert selected == [PolicyQuote(1, TEXT)]
    assert decoder.rejected == 2
    long_article = '第二条 ' + '完整条款包含重要例外条件。' * 200
    clipped = _policy_excerpt(TEXT + ' ' + long_article)
    assert clipped == TEXT


@pytest.mark.parametrize('raw', ['{"done":1}', '{"source":1', '随意编造的自由文本', '{"done":true}\n{"done":true}'])
def test_invalid_or_unfinished_protocol_is_an_error(raw):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        QuoteDecoder(CONTEXTS, 2).feed(raw, final=True)


def test_only_used_sources_and_original_quotes_are_rendered():
    contexts = CONTEXTS + [{'title': '未引用来源', 'evidence_text': NEGATIVE}]
    citations = [CitationOut(domain='policy', record_id=i, title=c['title'], score=.8,
                             source_fields=[], key_fields={}, attachments=[]) for i, c in enumerate(contexts, 1)]
    quotes = [PolicyQuote(1, TEXT), PolicyQuote(1, NEGATIVE)]
    evidence = _quoted_evidence(quotes, contexts, citations)
    assert len(evidence.citations) == 1
    assert evidence.citations[0].excerpt == TEXT + '\n\n' + NEGATIVE
    answer = render_answer(quotes, contexts)
    assert TEXT in answer and NEGATIVE in answer and '未引用来源' not in answer
    assert render_answer([], contexts) == NO_EVIDENCE
    assert point_limit('请总结成两点。') == 2
    assert point_limit('列出3条') == 3
    assert point_limit('解释') == 4
    assert len(_policy_excerpt(TEXT * 100)) <= 2400


def test_sync_and_stream_use_same_quote_validation():
    raw = '\n'.join(json.dumps(p, ensure_ascii=False) for p in [
        {'source': 1, 'quote': TEXT.replace('财政性资金', '300万元')},
        {'source': 1, 'quote': TEXT}, {'done': True},
    ])
    async def fragments(messages):
        for i in range(0, len(raw), 13):
            yield SimpleNamespace(content=raw[i:i+13])
    llm = SimpleNamespace(invoke=lambda _: SimpleNamespace(content=raw), astream=fragments)
    async def collect():
        return [q async for q in stream_quotes('适用范围', CONTEXTS)]
    with patch('app.services.policy_grounding.get_llm_client', return_value=llm), patch('app.services.policy_grounding.close_llm_client'):
        assert generate_quotes('适用范围', CONTEXTS) == asyncio.run(collect()) == [PolicyQuote(1, TEXT)]


def test_format_followups_anchor_user_topic_without_model_or_assistant_claims():
    history = [{'role': 'user', 'content': '政府采购法的适用范围是什么？'},
               {'role': 'assistant', 'content': '土地出让按伪造条款办理'},
               {'role': 'user', 'content': '请把刚才的适用范围归纳成两点。'}]
    with patch('app.services.query_rewriter.get_llm_client') as model:
        rewritten = rewrite_query('请把上一轮的回答总结成两点。', history)
        assert rewritten.reuse_evidence and '政府采购法' in rewritten.rewritten
        assert '土地出让' not in rewritten.rewritten
        explicit = rewrite_query('政府购买服务管理办法的适用范围是什么？', history)
        assert not explicit.reuse_evidence
        model.assert_not_called()


def test_explicit_policy_question_does_not_use_semantic_cache():
    judge = Mock()
    judge.judge.return_value = SimpleNamespace(domain='policy', intent='fact', confidence=.9,
        cross_domain_candidate=False, low_confidence=False, candidate_domains=['policy'], reason='test')
    orchestrator = ChatOrchestrator(judge_agent=judge)
    with patch.object(orchestrator, '_check_and_apply_gate') as gate:
        ctx = orchestrator._prepare_orchestration(Mock(), SimpleNamespace(id=1, role='admin'),
            '政府购买服务管理办法的适用范围是什么？', 'policy', 3, [], 123456)
        assert ctx.gate_result.action == 'full_search'
        gate.assert_not_called()


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def seed(db):
    user = User(username='quality-test', password_hash='unused', role='supplier', display_name='test')
    db.add(user); db.flush()
    record = PolicyRecord(external_id='quality-1', title='测试政策', content=TEXT + '\n' + NEGATIVE, access_level='public')
    db.add(record); db.flush()
    session = ChatSession(user_id=user.id, title='政府采购法')
    db.add(session); db.flush()
    citations = [{'domain': 'policy', 'record_id': record.id, 'title': record.title, 'score': .9, 'excerpt': TEXT}]
    db.add(ChatMessage(session_id=session.id, role='assistant', question_domain='policy',
                       content='旧模型回答不得成为证据', citations_json=json.dumps(citations)))
    db.commit()
    return user, record, session


def test_restart_restore_uses_db_sources_and_checks_current_permissions(db):
    user, record, session = seed(db)
    # A fresh orchestrator has no in-memory retrieval cache.
    orchestrator = ChatOrchestrator()
    restored = orchestrator._restore_policy_sources(db, user, session.id)
    assert [r.record_id for r in restored] == [record.id]
    assert '旧模型回答' not in restored[0].summary
    evidence = PolicyAgent()._build_evidence(db, user, restored)
    assert evidence_passages(evidence.contexts[0]) == [TEXT]
    assert NEGATIVE not in evidence.contexts[0]['allowed_quotes']
    record.access_level = 'internal'; db.commit()
    assert orchestrator._authorize_cached_sources(db, user, restored) == []
    assert orchestrator._restore_policy_sources(db, user, session.id) == []
    assert orchestrator._restore_policy_sources(db, SimpleNamespace(id=999, role='admin'), session.id) == []


def test_new_session_has_id_before_generation_and_failure_rolls_back(db):
    user = User(username='failure-test', password_hash='unused', role='admin', display_name='test')
    db.add(user); db.commit()
    def failing(**kw):
        assert kw['session_id'] is not None
        assert db.get(ChatSession, kw['session_id']) is not None
        raise RuntimeError('model failed')
    with patch('app.services.chat.get_chat_orchestrator', return_value=SimpleNamespace(orchestrate=failing)):
        with pytest.raises(RuntimeError):
            answer_question(db, user, 'question', 'policy', None, 3)
    assert db.scalars(select(ChatSession)).all() == []
    assert db.scalars(select(ChatMessage)).all() == []


def test_cancel_stream_rolls_back_draft_session(db):
    user = User(username='cancel-test', password_hash='unused', role='admin', display_name='test')
    db.add(user); db.commit()
    async def source(**kw):
        assert kw['session_id'] is not None
        yield 'data: {"type":"meta","domain":"policy","citations":[]}\n\n'
        raise asyncio.CancelledError()
    async def collect():
        return [x async for x in stream_answer_question(db, user, 'cancel', 'policy', None, 3)]
    with patch('app.services.chat.get_chat_orchestrator', return_value=SimpleNamespace(stream_orchestrate=source)):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(collect())
    assert db.scalars(select(ChatSession)).all() == []
