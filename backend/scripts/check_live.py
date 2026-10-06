import json, time, sys
from pathlib import Path
import httpx
root=Path(__file__).resolve().parents[2]
out=root/'dist/live-system-tests'
out.mkdir(parents=True,exist_ok=True)
client=httpx.Client(base_url='http://127.0.0.1:8000/api',trust_env=False,timeout=httpx.Timeout(180,connect=5))
report={'phase':'after-fix','checks':[]}
def check(name, passed, **detail):
    report['checks'].append({'name':name,'status':'passed' if passed else 'failed',**detail})
    (out/'api-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(('PASS ' if passed else 'FAIL ')+name,flush=True)
for attempt in range(30):
    try:
        if client.get('/auth/me',timeout=2).status_code==401: break
    except httpx.HTTPError: pass
    time.sleep(1)
else: raise RuntimeError('Backend did not become available')
check('unauthenticated API denied',client.get('/search/policy').status_code==401)
check('invalid token denied',client.get('/auth/me',headers={'Authorization':'Bearer invalid-token'}).status_code==401)
check('wrong password denied',client.post('/auth/login',json={'username':'admin','password':'wrong-live-test'}).status_code==401)
tokens={}
for username in ['admin','internal','supplier']:
    res=client.post('/auth/login',json={'username':username,'password':username+'123'})
    check('real login '+username,res.status_code==200,status_code=res.status_code)
    if res.status_code==200: tokens[username]=res.json()['access_token']
headers={'Authorization':'Bearer '+tokens['admin']}
report['searches']={}
for domain,query in [('policy','政府采购中小企业'),('tender','软件'),('enterprise','科技')]:
    t0=time.time()
    res=client.get('/search/'+domain,params={'q':query,'top_k':5},headers=headers)
    data=res.json()
    items=data.get('items',[])
    report['searches'][domain]=items
    check('real '+domain+' search',res.status_code==200 and len(items)>0,status_code=res.status_code,count=len(items),seconds=round(time.time()-t0,2))
res=client.get('/search/enterprise',params={'q':'zzzzLIVEACCEPTANCE20261006NONEXISTENT','top_k':5},headers=headers)
check('real enterprise empty search',res.status_code==200 and res.json().get('items')==[],status_code=res.status_code)
for role in ['internal','supplier']:
    rh={'Authorization':'Bearer '+tokens[role]}
    for domain in ['policy','tender','enterprise']:
        res=client.get('/search/'+domain,params={'top_k':5},headers=rh)
        report.setdefault('role_results',{})[role+'_'+domain]=res.json().get('items',[])
        check(role+' '+domain+' search API',res.status_code==200,status_code=res.status_code,count=len(res.json().get('items',[])))
check('attachment access without token denied',client.get('/attachments/2147483647/download').status_code==401)
check('missing attachment returns 404',client.get('/attachments/2147483647/download',headers=headers).status_code==404)
question='【联调20261006】政府采购法的适用范围是什么？请简要说明并列出来源。'
report['question']=question
report['stream_events']=[]
t0=time.time()
with client.stream('POST','/chat/query/stream',headers=headers,json={'question':question,'domain':'policy','top_k':3}) as res:
    check('real SSE response',res.status_code==200 and 'text/event-stream' in res.headers.get('content-type',''),status_code=res.status_code)
    for line in res.iter_lines():
        if line.startswith('data:'):
            event=json.loads(line[5:].strip())
            report['stream_events'].append({'elapsed_seconds':round(time.time()-t0,2),**event})
events=report['stream_events']
check('real stream has chunks and completed answer',any(e['type']=='chunk' for e in events) and any(e['type']=='done' and e.get('answer') for e in events),event_types=[e['type'] for e in events],seconds=round(time.time()-t0,2))
check('real stream has citations',any(e['type']=='meta' and e.get('citations') for e in events))
check('stream returns persisted session ID',any(e['type']=='done' and e.get('session_id') for e in events))
sid=next((e.get('session_id') for e in events if e['type']=='done'),None)
if sid:
    session=client.get('/chat/sessions/'+str(sid),headers=headers).json()
    report['session_id']=sid
    check('first round persisted',len(session.get('messages',[]))==2)
    follow=[]
    t0=time.time()
    with client.stream('POST','/chat/query/stream',headers=headers,json={'question':'请把刚才的适用范围归纳成两点。','domain':'policy','session_id':sid,'top_k':3}) as res:
        for line in res.iter_lines():
            if line.startswith('data:'): follow.append(json.loads(line[5:].strip()))
    report['followup_events']=follow
    check('second round returns same session ID',any(e['type']=='done' and e.get('session_id')==sid for e in follow),seconds=round(time.time()-t0,2))
    session=client.get('/chat/sessions/'+str(sid),headers=headers).json()
    report['session_message_count']=len(session.get('messages',[]))
    check('two rounds persisted with correct roles', [m['role'] for m in session.get('messages',[])]==['user','assistant','user','assistant'])
    check('foreign session read denied',client.get('/chat/sessions/'+str(sid),headers={'Authorization':'Bearer '+tokens['supplier']}).status_code==404)
from dotenv import dotenv_values
from sqlalchemy import create_engine,text
engine=create_engine(dotenv_values(root/'.env')['DATABASE_URL'])
tables={'policy':'policy_records','tender':'tender_records','enterprise':'enterprise_records'}
with engine.connect() as db:
    for domain,items in report['searches'].items():
        title_field='enterprise_name' if domain=='enterprise' else 'title'
        valid=bool(items)
        for item in items:
            row=db.execute(text(f'SELECT {title_field} FROM {tables[domain]} WHERE id=:id'),{'id':item['record_id']}).first()
            valid=valid and bool(row and row[0]==item['title'])
        check(domain+' results match MySQL source titles',valid)
    for key,items in report.get('role_results',{}).items():
        role,domain=key.split('_',1)
        grants=set(db.execute(text('SELECT project_id FROM user_project_grants g JOIN users u ON u.id=g.user_id WHERE u.username=:role'),{'role':role}).scalars())
        valid=True
        for item in items:
            row=db.execute(text(f'SELECT access_level,project_id FROM {tables[domain]} WHERE id=:id'),{'id':item['record_id']}).first()
            valid=valid and bool(row and row[0] in (['public'] if role=='supplier' else ['public','internal']) and (row[1] is None or row[1] in grants))
        check(key+' results obey existing permissions',valid,coverage='only existing public/null levels; no private/internal business records available')
    citations=next((e.get('citations',[]) for e in reversed(events) if e['type']=='meta'),[])
    valid=bool(citations)
    for citation in citations:
        domain=citation['domain']
        title_field='enterprise_name' if domain=='enterprise' else 'title'
        row=db.execute(text(f'SELECT {title_field} FROM {tables[domain]} WHERE id=:id'),{'id':citation['record_id']}).first()
        valid=valid and bool(row and row[0]==citation['title'])
    check('stream citations match existing MySQL records',valid)
res=client.get('/search/tender',params={'stage':'中标','top_k':3},headers=headers)
check('nonempty stage filter preserved',res.status_code==200 and bool(res.json().get('items')) and all(i['key_fields'].get('stage')=='中标' for i in res.json().get('items',[])))
runtime_log=(out/'backend.stdout.log').read_text(encoding='utf-8')
report['llm_stream_completed_log']=runtime_log.count('[LLM] stream_answer 完成')+runtime_log.count('[PolicyGrounding] stream completed')
check('remote LLM stream actually completed',report['llm_stream_completed_log']>=2)
engine.dispose()
client.close()
