// Opt-in: real backend/model, test-browser storage only, screenshots on D: dist.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require=createRequire(path.join(process.env.PLAYWRIGHT_MODULE_DIR,'package.json'));
const {chromium}=require('playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const output=path.join(root,'dist/answer-quality-tests');
const report=JSON.parse(await fs.readFile(path.join(output,'real-qa.json'),'utf8'));
const sessionId=Number(process.env.RAG_QUALITY_SESSION||report.runs[0].session_id);
for(let attempt=0;attempt<45;attempt++){
 try{if((await fetch('http://127.0.0.1:8000/api/health')).ok)break;}catch{}
 if(attempt===44)throw new Error('Backend unavailable');
 await new Promise(resolve=>setTimeout(resolve,1000));
}
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const page=await context.newPage();
page.setDefaultTimeout(30000);
const results=[],errors=[],requests=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('request',r=>{if(r.url().includes('/chat/query/stream'))requests.push(r.postDataJSON());});
async function check(name,fn){try{await fn();results.push({name,status:'passed'});console.log('PASS',name);}catch(e){results.push({name,status:'failed',error:e.message});console.log('FAIL',name,e.message);}await fs.writeFile(path.join(output,'browser-quality.json'),JSON.stringify({sessionId,results,errors,requests},null,2));}
async function api(route){return page.evaluate(async(route)=>{const r=await fetch('http://127.0.0.1:8000/api'+route,{headers:{Authorization:'Bearer '+localStorage.getItem('token')}});if(!r.ok)throw new Error('Backend status '+r.status);return r.json();},route);}
async function openSource(){await page.locator('.source-link').last().focus();await page.keyboard.press('Enter');await page.locator('.ant-drawer-title').filter({hasText:'来源详情'}).waitFor();await page.waitForFunction(()=>{const e=document.querySelector('.ant-drawer-open .ant-drawer-content-wrapper');return e&&e.getBoundingClientRect().right<=innerWidth+1;});}
try{
 console.log('Opening production login');
 await page.goto('http://127.0.0.1:4173/login');
 await page.getByLabel('用户名',{exact:true}).fill(process.env.RAG_TEST_USER||'admin');
 await page.getByLabel('密码',{exact:true}).fill(process.env.RAG_TEST_PASSWORD||'admin123');
 await page.getByRole('button',{name:'进入工作台'}).click();await page.waitForURL('**/chat');
 console.log('Real browser login completed');
 const session=await api('/chat/sessions/'+sessionId);
 const messages=session.messages.map(m=>({role:m.role,content:m.content,domain:m.question_domain,citations:JSON.parse(m.citations_json||'[]')}));
 await page.evaluate(({sessionId,messages})=>localStorage.setItem('rag_chat_history',JSON.stringify([{id:'quality-acceptance',title:'政府采购法原文核验',serverSessionId:sessionId,messages}])),{sessionId,messages});
 await page.reload();
 await check('desktop keyboard source details show the actual quoted excerpt',async()=>{
   const source=messages.filter(m=>m.role==='assistant').at(-1).citations.at(-1);
   assert.ok(source?.excerpt);await openSource();
   assert.equal(await page.locator('.source-excerpt').innerText(),source.excerpt);
   await page.screenshot({path:path.join(output,'policy-source-desktop.png'),fullPage:true,animations:'disabled'});
   await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('.ant-drawer-open'));
 });
 await check('real browser implicit followup streams validated quotes and persists',async()=>{
   await page.getByRole('textbox',{name:'真实问答问题',exact:true}).fill('请把刚才的适用范围归纳成两点。');
   await page.keyboard.press('Enter');await page.getByRole('button',{name:'停止生成',exact:true}).waitFor({state:'hidden',timeout:150000});
   assert.equal(await page.locator('.message-error').count(),0);
   const state=await page.evaluate(()=>JSON.parse(localStorage.getItem('rag_chat_history'))[0]);
   assert.equal(state.serverSessionId,sessionId);assert.equal(requests[0].session_id,sessionId);
   const answer=state.messages.at(-1);assert.ok(answer.citations.length);assert.ok(answer.citations.every(c=>c.excerpt&&c.excerpt.split('\n\n').every(q=>answer.content.includes(q))));
   const dbSession=await api('/chat/sessions/'+sessionId);assert.equal(dbSession.messages.at(-1).content,answer.content);
   assert.ok((answer.content.match(/^\d+\. /gm)||[]).length<=2);
   await page.screenshot({path:path.join(output,'policy-chat-desktop.png'),fullPage:true,animations:'disabled'});
 });
 await check('language switch keeps original policy quotation',async()=>{
   const before=await page.locator('.answer-body').last().innerText();await page.getByRole('button',{name:'切换到英文'}).click();
   assert.equal(await page.locator('.answer-body').last().innerText(),before);await page.getByRole('button',{name:'Switch to Chinese'}).click();
 });
 await check('mobile policy quote and drawer fit viewport',async()=>{
   await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await page.screenshot({path:path.join(output,'policy-chat-mobile.png'),fullPage:false,animations:'disabled'});
   await openSource();assert.ok(await page.locator('.source-excerpt').innerText());
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await page.screenshot({path:path.join(output,'policy-source-mobile.png'),fullPage:false,animations:'disabled'});
   await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('.ant-drawer-open'));
 });
 await check('stopping a real policy request shows stopped status without completed answer',async()=>{
   const count=(await api('/chat/sessions/'+sessionId)).messages.length;
   await page.getByRole('textbox',{name:'真实问答问题',exact:true}).fill('请把上一轮的回答总结成两点。');
   await page.getByRole('button',{name:'发送问题',exact:true}).click();await page.getByRole('button',{name:'停止生成',exact:true}).click();
   await page.getByText('已停止生成，已收到的内容保留。',{exact:true}).waitFor();
   assert.equal(await page.getByRole('button',{name:'停止生成',exact:true}).count(),0);
   await page.waitForTimeout(2500);
   assert.equal((await api('/chat/sessions/'+sessionId)).messages.length,count);
 });
 await check('browser has no unhandled errors',async()=>assert.deepEqual(errors,[]));
}finally{await browser.close();}
if(results.some(r=>r.status==='failed'))process.exitCode=1;
