import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.chat import stream_answer_question
from app.services.llm import LLMService
from app.services.reranker import RerankerService
from app.services.retrieval import search_domain

def frame(payload):
    return 'data: '+json.dumps(payload)+'\n\n'

class StreamAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, orchestrator, persist):
        db=Mock()
        with patch('app.services.chat._get_owned_session',return_value=None), patch('app.services.chat._get_or_create_session',return_value=SimpleNamespace(id=42)), patch('app.services.chat._load_history_messages',return_value=[]), patch('app.services.chat.get_chat_orchestrator',return_value=orchestrator), patch('app.services.chat._persist_answer',persist):
            events=[json.loads(item[6:]) async for item in stream_answer_question(db,SimpleNamespace(id=1),'question','policy',None,3)]
        return events,db

    async def test_done_is_sent_after_persistence_and_contains_session_id(self):
        state=[]
        async def source(**kw):
            yield frame({'type':'meta','domain':'policy','citations':[]})
            yield frame({'type':'chunk','content':'真实答案'})
            yield frame({'type':'done','answer':'真实答案'})
            state.append('source_completed')
        def persist(**kw):
            self.assertEqual(state,['source_completed'])
            self.assertEqual(kw['answer'],'真实答案')
            return {'session_id':42}
        events,db=await self.collect(SimpleNamespace(stream_orchestrate=source),Mock(side_effect=persist))
        self.assertEqual([e['type'] for e in events],['meta','chunk','done'])
        self.assertEqual(events[-1]['session_id'],42)
        db.rollback.assert_not_called()

    async def test_stream_failure_preserves_partial_text_without_persisting(self):
        async def source(**kw):
            yield frame({'type':'meta','domain':'policy','citations':[]})
            yield frame({'type':'chunk','content':'已收到片段'})
            raise RuntimeError('sensitive provider failure')
        persist=Mock()
        events,db=await self.collect(SimpleNamespace(stream_orchestrate=source),persist)
        self.assertEqual([e['type'] for e in events],['meta','chunk','error'])
        self.assertNotIn('sensitive',events[-1]['message'])
        persist.assert_not_called()
        db.rollback.assert_called_once()

    async def test_save_failure_cannot_emit_done(self):
        async def source(**kw):
            yield frame({'type':'meta','domain':'policy','citations':[]})
            yield frame({'type':'done','answer':'answer'})
        events,db=await self.collect(SimpleNamespace(stream_orchestrate=source),Mock(side_effect=RuntimeError('database failed')))
        self.assertEqual(events[-1]['type'],'error')
        self.assertNotIn('done',[e['type'] for e in events])

    async def test_cancellation_never_persists_an_incomplete_answer(self):
        async def source(**kw):
            yield frame({'type':'meta','domain':'policy','citations':[]})
            yield frame({'type':'chunk','content':'partial'})
            raise asyncio.CancelledError()
        persist=Mock()
        with self.assertRaises(asyncio.CancelledError):
            await self.collect(SimpleNamespace(stream_orchestrate=source),persist)
        persist.assert_not_called()

    async def test_missing_llm_is_an_error_not_fallback(self):
        service=LLMService()
        with patch('app.services.llm.get_llm_client',return_value=None), patch.object(service,'_fallback_answer') as fallback:
            with self.assertRaises(RuntimeError):
                _=[chunk async for chunk in service.stream_answer('question','policy','admin',[])]
            fallback.assert_not_called()

    async def test_llm_failure_after_a_chunk_is_not_appended_fallback(self):
        service=LLMService()
        async def source(messages):
            yield SimpleNamespace(content='partial')
            raise RuntimeError('upstream error')
        llm=SimpleNamespace(astream=source)
        seen=[]
        with patch('app.services.llm.get_llm_client',return_value=llm), patch.object(service,'_build_messages',return_value=[]), patch.object(service,'_log_llm_error'), patch.object(service,'_fallback_answer') as fallback:
            with self.assertRaises(RuntimeError):
                async for chunk in service.stream_answer('question','policy','admin',[]): seen.append(chunk)
            self.assertEqual(seen,['partial'])
            fallback.assert_not_called()

class RerankerBatchTests(unittest.TestCase):
    def test_bounded_batch_keeps_all_candidates_and_ranks_by_score(self):
        service=RerankerService()
        def score(pairs,normalize,batch_size):
            self.assertEqual(len(pairs),7)
            self.assertTrue(normalize)
            self.assertEqual(batch_size,2)
            return [0.1,0.4,0.2,0.9,0.3,0.8,0.7]
        with patch.object(service,'_load_model',return_value=SimpleNamespace(compute_score=score)), patch.dict('os.environ',{'RERANKER_BATCH_SIZE':'2'}):
            result=service.rerank('q',[str(i) for i in range(7)],top_k=3)
        self.assertEqual([r['index'] for r in result],[3,5,6])

class SearchRoutingTests(unittest.TestCase):
    def test_none_router_filters_use_existing_high_recall_path(self):
        db=Mock()
        user=SimpleNamespace(role='admin')
        result=SimpleNamespace(items=['actual-candidate'])
        with patch('app.services.retrieval.high_recall_search',return_value=result) as recall:
            items=search_domain(db,'tender',user,q='软件',region=None,stage=None,tenderer=None,top_k=5)
        self.assertEqual(items,['actual-candidate'])
        recall.assert_called_once_with(db=db,domain='tender',user=user,query_text='软件',top_k=5)
        db.scalars.assert_not_called()

    def test_real_filter_does_not_use_unfiltered_high_recall_path(self):
        db=Mock()
        db.scalars.return_value.all.return_value=[]
        with patch('app.services.retrieval.high_recall_search') as recall, patch('app.services.retrieval.merge_with_semantic_hits',return_value=[]):
            search_domain(db,'tender',SimpleNamespace(role='admin'),q='软件',stage='中标',region=None)
        recall.assert_not_called()
        statement=db.scalars.call_args[0][0]
        self.assertEqual(statement.compile().params['stage_1'],'%中标%')
