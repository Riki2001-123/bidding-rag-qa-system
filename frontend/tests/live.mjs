import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require=createRequire(path.join(process.env.PLAYWRIGHT_MODULE_DIR,'package.json'));
const {chromium}=require('playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const output=path.join(root,'dist/live-system-tests');
await fs.mkdir(output,{recursive:true});
const base='http://127.0.0.1:4173';
const results=[],errors=[],network=[],streams=[],chatRequests=[];
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const page=await context.newPage();
page.setDefaultTimeout(150000);
page.on('pageerror',e=>errors.push(e.message));
page.on('request',r=>{if(r.url().includes('/api/chat/query/stream')) chatRequests.push(r.postDataJSON());});
page.on('response',r=>{
  if(r.url().includes('/api/')) network.push({url:r.url(),status:r.status(),method:r.request().method()});
  if(r.url().endsWith('/chat/query/stream')) r.text().then(body=>streams.push(body)).catch(()=>{});
});
async function save(){await fs.writeFile(path.join(output,process.env.LIVE_REPORT_FILE||'browser-report.json'),JSON.stringify({results,errors,network,streams,chatRequests},null,2));}
async function check(name,fn){if(process.env.LIVE_CHECK_NAMES&&!process.env.LIVE_CHECK_NAMES.split("|").includes(name))return;try{await fn();results.push({name,status:'passed'});console.log('PASS',name);}catch(e){results.push({name,status:'failed',error:e.message});console.log('FAIL',name,e.message);}await save();}
async function send(question){await page.getByRole('textbox',{name:'真实问答问题',exact:true}).fill(question);await page.getByRole('button',{name:'发送问题',exact:true}).click();await page.getByRole('button',{name:'停止生成',exact:true}).waitFor({state:'hidden'});assert.equal(await page.locator('.message-error').count(),0);}
if(process.env.LIVE_CHECK_NAMES){await page.goto(base+'/login');await page.getByLabel('用户名',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill('admin123');await page.getByRole('button',{name:'进入工作台'}).click();await page.waitForURL('**/chat');}
try {
await check('production public routes make no business API requests',async()=>{
 await page.goto(base+'/');await page.locator('h1').waitFor();await page.goto(base+'/demo');await page.locator('.case-button').first().click();await page.locator('.answer-body').waitFor();assert.equal(network.length,0);
});
await check('three protected production routes redirect to login',async()=>{for(const route of ['/chat','/search','/dashboard']){await page.goto(base+route);await page.waitForURL('**/login');}});
await check('incorrect real password shows login error',async()=>{
 await page.getByLabel('用户名',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill('wrong-live-test');await page.getByRole('button',{name:'进入工作台'}).click();await page.getByRole('alert').filter({hasText:'用户名或密码不正确'}).waitFor();
});
await check('real browser login and authenticated navigation',async()=>{
 await page.getByLabel('密码',{exact:true}).fill('admin123');await page.getByRole('button',{name:'进入工作台'}).click();await page.waitForURL('**/chat');await page.getByText('系统管理员',{exact:true}).waitFor();
});
await check('real UI stream completes with persisted session and citations',async()=>{
 await send('【网页联调20261006】政府采购法的适用范围是什么？请简要说明并列出来源。');
 assert.ok((await page.locator('.answer-body').last().innerText()).length>20);assert.ok(await page.locator('.source-link').count()>0);
 const state=await page.evaluate(()=>JSON.parse(localStorage.getItem('rag_chat_history')));assert.ok(state[0].serverSessionId);
 await page.screenshot({path:path.join(output,'live-chat-desktop.png'),fullPage:true,animations:'disabled'});
});
await check('source drawer opens with keyboard and uses real returned fields',async()=>{
 const title=await page.locator('.source-link').first().innerText();await page.locator('.source-link').first().focus();await page.keyboard.press('Enter');await page.locator('.ant-drawer-title').filter({hasText:'来源详情'}).waitFor();await page.waitForFunction(()=>{const el=document.querySelector('.ant-drawer-open .ant-drawer-content-wrapper');return el&&el.getBoundingClientRect().right<=innerWidth+1;});
 assert.ok(title.includes(await page.locator('.source-drawer h2').innerText()));assert.ok(await page.locator('.source-excerpt').innerText());
 await page.screenshot({path:path.join(output,'live-source-desktop.png'),fullPage:true,animations:'disabled'});await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('.ant-drawer-open'));
});
await check('real UI follow-up reuses persisted session',async()=>{
 const prior=await page.evaluate(()=>JSON.parse(localStorage.getItem('rag_chat_history'))[0].serverSessionId);
 await send('请把刚才政府采购法的适用范围归纳成两点。');
 const state=await page.evaluate(()=>JSON.parse(localStorage.getItem('rag_chat_history'))[0]);assert.equal(state.serverSessionId,prior);assert.equal(state.messages.length,4);assert.equal(chatRequests[1].session_id,prior);
 const response=await page.evaluate(async(id)=>{const r=await fetch('http://127.0.0.1:8000/api/chat/sessions/'+id,{headers:{Authorization:'Bearer '+localStorage.getItem('token')}});return await r.json();},prior);
 assert.deepEqual(response.messages.map(m=>m.role),['user','assistant','user','assistant']);
});
await check('language switching preserves real answer and server session',async()=>{
 const before=await page.locator('.answer-body').last().innerText();await page.getByRole('button',{name:'切换到英文'}).click();assert.equal(await page.locator('.answer-body').last().innerText(),before);await page.getByRole('button',{name:'Switch to Chinese'}).click();
});
await check('stop real generation preserves received chunks',async()=>{
 await page.getByRole('button',{name:/新对话/}).first().click();await page.getByRole('textbox',{name:'真实问答问题',exact:true}).fill('【停止联调20261006】政府采购法的适用范围是什么？请详细解释并列出十项注意事项。');await page.getByRole('button',{name:'发送问题',exact:true}).click();
 await page.waitForFunction(()=>{const el=document.querySelector('.answer-body');return el&&el.textContent.length>0;});
 await page.getByRole('button',{name:'停止生成',exact:true}).click();await page.getByText('已停止生成，已收到的内容保留。',{exact:true}).waitFor();assert.ok((await page.locator('.answer-body').innerText()).length>0);assert.equal(await page.getByRole('button',{name:'停止生成'}).count(),0);
 await page.screenshot({path:path.join(output,'live-stopped.png'),fullPage:true,animations:'disabled'});
});
await check('real unified search, domain filters and original source summary',async()=>{
 await page.goto(base+'/search');await page.getByRole('textbox',{name:'搜索真实数据'}).fill('科技');await page.locator('.live-search-form button[type="submit"]').click();await page.waitForFunction(()=>document.querySelectorAll('.record-row').length>0);assert.equal(await page.getByRole('alert').count(),0);
 await page.locator('.filter-bar button').filter({hasText:'企业'}).click();assert.ok(await page.locator('.record-row').count()>0);await page.locator('.record-row').first().focus();await page.keyboard.press('Enter');await page.locator('.ant-drawer-title').filter({hasText:'来源详情'}).waitFor();await page.waitForFunction(()=>{const el=document.querySelector('.ant-drawer-open .ant-drawer-content-wrapper');return el&&el.getBoundingClientRect().right<=innerWidth+1;});assert.ok((await page.locator('.source-excerpt').innerText()).length>0);
 await page.screenshot({path:path.join(output,'live-search-desktop.png'),fullPage:false,animations:'disabled'});await page.keyboard.press('Escape');
});
await check('production mobile layout and real source drawer',async()=>{
 await page.setViewportSize({width:390,height:844});await page.locator('.record-row').first().click();await page.locator('.ant-drawer-title').filter({hasText:'来源详情'}).waitFor();await page.waitForFunction(()=>{const el=document.querySelector('.ant-drawer-open .ant-drawer-content-wrapper');return el&&el.getBoundingClientRect().right<=innerWidth+1;});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.screenshot({path:path.join(output,'live-source-mobile.png'),fullPage:false,animations:'disabled'});await page.keyboard.press('Escape');await page.goto(base+'/chat');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(output,'live-chat-mobile.png'),fullPage:true,animations:'disabled'});
});
await check('real dashboard direct route and return-to-home link',async()=>{await page.goto(base+'/dashboard');await page.locator('h1').waitFor();await page.getByRole('button',{name:'返回展示首页'}).click();await page.waitForURL(base+'/');});
await check('browser has no unhandled errors',async()=>assert.deepEqual(errors,[]));
} finally {await save();await browser.close();}
if(results.some(r=>r.status==='failed'))process.exitCode=1;
