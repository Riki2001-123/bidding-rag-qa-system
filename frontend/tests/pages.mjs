import assert from "node:assert/strict";
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { fileURLToPath } from "node:url";
import { cases } from "../src/data/demo.js";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const out = path.join(root, "dist/pages-deployment");
const site = path.join(out, "site");
const prefix = "/bidding-rag-qa-system/";
let server;
let base = process.env.PAGES_TEST_URL;
if (!base) {
  server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, "http://localhost");
      if (!url.pathname.startsWith(prefix)) { response.writeHead(404); response.end(); return; }
      const file = path.resolve(site, decodeURIComponent(url.pathname.slice(prefix.length)));
      if (file !== site && !file.startsWith(site + path.sep)) { response.writeHead(404); response.end(); return; }
      let stat;
      try { stat = await fs.stat(file); } catch { response.writeHead(404); response.end(await fs.readFile(path.join(site, "404.html"))); return; }
      let target = file;
      if (stat.isDirectory()) {
        if (!url.pathname.endsWith("/")) { response.writeHead(301, { Location: url.pathname + "/" + url.search }); response.end(); return; }
        target = path.join(file, "index.html");
      }
      const type = { ".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[path.extname(target)] || "application/octet-stream";
      response.writeHead(200, { "Content-Type": type }); response.end(await fs.readFile(target));
    } catch { response.writeHead(500); response.end(); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}${prefix}`;
}
base = base.replace(/\/$/, "") + "/";
const phase = process.env.PAGES_TEST_URL ? "public" : "local";
const require = createRequire(process.env.PLAYWRIGHT_MODULE_DIR ? path.join(process.env.PLAYWRIGHT_MODULE_DIR, "package.json") : import.meta.url);
const { chromium } = require("playwright");
const browser = await chromium.launch({ channel: "msedge", headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
await context.addInitScript(() => {
  localStorage.setItem("token", "preserve-test-token");
  localStorage.setItem("rag_chat_history", "preserve-test-history");
  const get = Storage.prototype.getItem;
  window.privateReads = [];
  Storage.prototype.getItem = function(key) { if (["token", "rag_chat_history"].includes(key)) window.privateReads.push(key); return get.call(this, key); };
});
const page = await context.newPage();
page.setDefaultTimeout(30000);
const results = [], errors = [], badResponses = [], apiRequests = [];
page.on("pageerror", e => errors.push(e.message));
page.on("response", r => { if (r.status() >= 400) badResponses.push({ url: r.url(), status: r.status() }); });
page.on("request", r => { if (/\/api\/|127\.0\.0\.1:8000/.test(r.url())) apiRequests.push(r.url()); });
async function save() { await fs.writeFile(path.join(out, `${phase}-report.json`), JSON.stringify({ base, results, errors, badResponses, apiRequests }, null, 2)); }
async function check(name, fn) { try { await fn(); results.push({ name, status: "passed" }); console.log("PASS", name); } catch(e) { results.push({ name, status: "failed", error: e.message }); console.log("FAIL", name, e.message); } await save(); }
async function navigate(route) { const response = await page.goto(base + route); assert.equal(response.status(), 200); await page.locator("h1,h2").first().waitFor(); }
async function privateState() { assert.deepEqual(await page.evaluate(() => window.privateReads), []); assert.deepEqual(await page.evaluate(() => [localStorage.token, localStorage.rag_chat_history]), ["preserve-test-token", "preserve-test-history"]); }
try {
  await check("project-subdirectory home and verified links", async () => {
    await navigate(""); await page.getByRole("heading", { level: 1 }).waitFor();
    assert.equal(await page.getByRole("link", { name: "查看源码" }).getAttribute("href"), "https://github.com/Riki2001-123/bidding-rag-qa-system");
    assert.equal(await page.locator(".hero-actions .primary").getAttribute("href"), prefix + "demo");
    await privateState(); await page.screenshot({ path: path.join(out, `${phase}-home.png`), fullPage: true, animations: "disabled" });
  });
  await check("direct demo route, query parameter and refresh succeed", async () => {
    await navigate("demo?case=association"); await page.locator(".answer-body").waitFor();
    assert.equal(await page.locator(".answer-body").innerText(), cases[3].answer.zh);
    const response = await page.reload(); assert.equal(response.status(), 200); await page.locator(".answer-body").waitFor();
    await privateState();
  });
  await check("four bilingual cases and preset followups remain exact", async () => {
    for (const language of ["zh", "en"]) {
      if (language === "en") await page.getByRole("button", { name: "切换到英文" }).click();
      for (const item of cases) {
        await page.locator(".case-button").filter({ hasText: item.label[language] }).click();
        assert.equal(await page.locator(".answer-body").innerText(), item.answer[language]);
        await page.locator(".followup-button").click(); assert.equal(await page.locator(".answer-body").last().innerText(), item.followup.answer[language]);
      }
    }
    await page.reload(); assert.equal(await page.locator("html").getAttribute("lang"), "en");
    await page.getByRole("button", { name: "Switch to Chinese" }).click();
  });
  await check("unknown question never adds an answer", async () => {
    const before = await page.locator(".answer-body").count();
    await page.getByRole("textbox", { name: "样例问题", exact: true }).fill("客户真实营业额是多少？");
    await page.getByRole("button", { name: "发送样例问题" }).click(); await page.getByRole("status").waitFor();
    assert.equal(await page.locator(".answer-body").count(), before);
  });
  await check("sample search supports filters and empty results", async () => {
    await page.getByRole("tab", { name: "资料检索" }).click(); assert.equal(await page.locator(".record-row").count(), 4);
    await page.getByRole("textbox", { name: "搜索样例资料" }).fill("Yunchuan"); assert.equal(await page.locator(".record-row").count(), 2);
    await page.locator(".filter-bar button").filter({ hasText: "企业" }).click(); assert.equal(await page.locator(".record-row").count(), 1);
    await page.getByRole("textbox", { name: "搜索样例资料" }).fill("no-such-record"); await page.getByRole("heading", { name: "没有找到匹配记录" }).waitFor();
  });
  await check("desktop source opens with keyboard and shows fictional evidence", async () => {
    await navigate("demo/?case=association"); await page.locator(".source-link").first().focus(); await page.keyboard.press("Enter");
    await page.locator(".ant-drawer-title").filter({ hasText: "来源详情" }).waitFor();
    assert.match(await page.locator(".source-excerpt").innerText(), /虚构/);
    await page.keyboard.press("Escape"); await page.waitForFunction(() => !document.querySelector(".ant-drawer-open"));
  });
  await check("every real-route entry is a static explanation with no login form", async () => {
    for (const route of ["login", "chat", "search", "dashboard"]) {
      await navigate(route); await page.getByRole("heading", { name: "真实系统另行接入" }).waitFor();
      assert.equal(await page.locator("form").count(), 0); await privateState();
      const response = await page.reload(); assert.equal(response.status(), 200);
    }
  });
  await check("mobile demo and source fit viewport", async () => {
    await page.setViewportSize({ width: 390, height: 844 }); await navigate("demo/?case=policy");
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({ path: path.join(out, `${phase}-demo-mobile.png`), fullPage: false, animations: "disabled" });
    await page.locator(".source-link").first().click(); await page.locator(".ant-drawer-title").filter({ hasText: "来源详情" }).waitFor();
    await page.waitForFunction(() => { const e = document.querySelector(".ant-drawer-open .ant-drawer-content-wrapper"); return e && e.getBoundingClientRect().right <= innerWidth + 1; });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({ path: path.join(out, `${phase}-source-mobile.png`), fullPage: false, animations: "disabled" });
  });
  await check("zero business API requests, resource errors or private state access", async () => {
    await privateState(); assert.deepEqual(apiRequests, []); assert.deepEqual(badResponses, []); assert.deepEqual(errors, []);
  });
} finally { await save(); await browser.close(); if (server) await new Promise(resolve => server.close(resolve)); }
if (results.some(r => r.status === "failed")) process.exitCode = 1;
