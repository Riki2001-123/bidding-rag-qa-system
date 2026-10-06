import assert from "node:assert/strict";
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { cases } from "../src/data/demo.js";
import http from "node:http";

const require = createRequire(process.env.PLAYWRIGHT_MODULE_DIR ? path.join(process.env.PLAYWRIGHT_MODULE_DIR, "package.json") : import.meta.url);
const { chromium } = require("playwright");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const output = path.join(root, "dist/showcase-tests");
await fs.mkdir(output, { recursive: true });
const base = process.env.SHOWCASE_TEST_URL || "http://127.0.0.1:4173";
const browser = await chromium.launch({ channel: process.env.SHOWCASE_BROWSER || "msedge", headless: true });
const results = []; const failures = []; const consoleErrors = [];
async function check(name, callback) {
  try { await callback(); results.push({ name, status: "passed" }); console.log("PASS", name); }
  catch (error) { results.push({ name, status: "failed", error: error.message }); failures.push(name); console.log("FAIL", name, error.message); }
}
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ["clipboard-read", "clipboard-write"] });
const page = await context.newPage();
page.setDefaultTimeout(10000);
page.on("pageerror", (error) => consoleErrors.push(error.message));
const apiRequests = [];
page.on("request", (r) => { if (new URL(r.url()).pathname.startsWith("/api/")) apiRequests.push(r.url()); });
try {
  await check("public home renders, scrolls, and exposes verified public links", async () => {
    await page.goto(base); await page.getByRole("heading", { level: 1 }).waitFor();
    assert.match(await page.locator("h1").innerText(), /有依据的答案/);
    assert.equal(await page.getByRole("link", { name: "查看源码" }).getAttribute("href"), "https://github.com/Riki2001-123/bidding-rag-qa-system");
    assert.equal(await page.getByRole("link", { name: /与 Riki 联系/ }).getAttribute("href"), "https://github.com/Riki2001-123");
    assert.ok(await page.evaluate(() => document.documentElement.scrollHeight > innerHeight));
    await page.screenshot({ path: path.join(output, "home-zh.png"), fullPage: true });
  });
  await check("language persists across reload and public routes", async () => {
    await page.getByRole("button", { name: "切换到英文" }).click(); await page.reload();
    await page.getByRole("heading", { name: /Your knowledge/ }).waitFor();
    assert.equal(await page.locator("html").getAttribute("lang"), "en");
    await page.screenshot({ path: path.join(output, "home-en.png"), fullPage: true });
  });
  await check("all four bilingual cases and eight preset follow-ups", async () => {
    await page.goto(base + "/demo");
    for (const language of ["en", "zh"]) {
      if (language === "zh") await page.getByRole("button", { name: "Switch to Chinese" }).click();
      for (const item of cases) {
        await page.locator(".case-button").filter({ hasText: item.label[language] }).click();
        await page.locator(".answer-body").last().waitFor();
        assert.equal(await page.locator(".answer-body").last().innerText(), item.answer[language]);
        await page.locator(".followup-button").click();
        assert.equal(await page.locator(".answer-body").last().innerText(), item.followup.answer[language]);
      }
      if (language === "en") {
        await page.goto(base + "/demo?case=association"); await page.locator(".answer-body").waitFor();
        await page.screenshot({ path: path.join(output, "demo-chat-en.png"), fullPage: true });
      }
    }
    await page.goto(base + "/demo?case=association"); await page.locator(".answer-body").waitFor();
    await page.screenshot({ path: path.join(output, "demo-chat-zh.png"), fullPage: true });
  });
  await check("unknown inputs do not add fabricated answers", async () => {
    const count = await page.locator(".answer-body").count();
    await page.getByRole("textbox", { name: "样例问题", exact: true }).fill("这家公司真实营收是多少？");
    await page.getByRole("button", { name: "发送样例问题" }).click();
    await page.getByRole("status").waitFor(); assert.equal(await page.locator(".answer-body").count(), count);
  });
  await check("source evidence, close via Escape, and keyboard operation", async () => {
    await page.goto(base + "/demo?case=association"); await page.locator(".source-link").first().focus(); await page.keyboard.press("Enter");
    await page.locator(".ant-drawer-title").filter({ hasText: "来源详情" }).waitFor();
    await page.waitForFunction(() => document.querySelector(".ant-drawer-content-wrapper").getBoundingClientRect().right <= innerWidth + 1);
    assert.match(await page.locator(".source-excerpt").innerText(), /虚构/);
    await page.screenshot({ path: path.join(output, "demo-source-zh.png"), fullPage: true });
    await page.keyboard.press("Escape"); await page.waitForFunction(() => !document.querySelector(".ant-drawer-open"));
  });
  await check("answer copy preserves the sample text", async () => {
    const content = await page.locator(".answer-body").innerText();
    await page.locator(".knowledge-message > button").click();
    const clipboard = await page.evaluate(() => navigator.clipboard.readText());
    assert.equal(clipboard.replace(/\r\n/g, "\n"), content);
  });
  await check("bilingual record search, domain filters, and empty state", async () => {
    await page.getByRole("tab", { name: "资料检索" }).click();
    assert.equal(await page.locator(".record-row").count(), 4);
    await page.getByRole("textbox", { name: "搜索样例资料" }).fill("Yunchuan"); assert.equal(await page.locator(".record-row").count(), 2);
    await page.locator(".filter-bar button").filter({ hasText: "企业" }).click(); assert.equal(await page.locator(".record-row").count(), 1);
    await page.getByRole("textbox", { name: "搜索样例资料" }).fill("no such record"); await page.getByRole("heading", { name: "没有找到匹配记录" }).waitFor();
    await page.getByRole("textbox", { name: "搜索样例资料" }).fill(""); await page.locator(".filter-bar button").first().click();
    await page.screenshot({ path: path.join(output, "demo-search-zh.png"), fullPage: true });
  });
  await check("demo reset, refresh and token/history isolation", async () => {
    assert.equal(await page.evaluate(() => localStorage.getItem("token")), null);
    assert.equal(await page.evaluate(() => localStorage.getItem("rag_chat_history")), null);
    await page.getByRole("button", { name: "重置", exact: true }).click(); await page.getByRole("tab", { name: "智能问答" }).click();
    assert.equal(await page.locator(".answer-body").count(), 0);
    await page.locator(".case-button").first().click(); await page.reload();
    await page.getByRole("heading", { name: "从一个好问题开始。" }).waitFor();
    assert.equal(apiRequests.length, 0, JSON.stringify(apiRequests));
  });
  await check("public demo never reads or changes pre-existing real credentials or history", async () => {
    await page.evaluate(() => { localStorage.setItem("token", "keep-existing-token"); localStorage.setItem("rag_chat_history", "keep-existing-history"); });
    await page.addInitScript(() => {
      const original = Storage.prototype.getItem;
      window.privateStorageReads = [];
      Storage.prototype.getItem = function (key) {
        if (["token", "rag_chat_history"].includes(key)) window.privateStorageReads.push(key);
        return original.call(this, key);
      };
    });
    await page.goto(base + "/demo?case=policy"); await page.locator(".answer-body").waitFor();
    assert.deepEqual(await page.evaluate(() => window.privateStorageReads), []);
    const stored = await page.evaluate(() => ({ token: localStorage.token, history: localStorage.rag_chat_history }));
    assert.deepEqual(stored, { token: "keep-existing-token", history: "keep-existing-history" });
    await page.evaluate(() => { localStorage.removeItem("token"); localStorage.removeItem("rag_chat_history"); });
  });
  await check("all protected deep links redirect anonymous users", async () => {
    for (const route of ["chat", "search", "dashboard"]) {
      await page.goto(base + "/" + route); await page.waitForURL("**/login");
      await page.getByRole("heading", { name: "登录真实系统" }).waitFor();
    }
  });
  await check("mobile home, demo search and source drawer fit viewport", async () => {
    await page.setViewportSize({ width: 390, height: 844 }); await page.goto(base);
    await page.getByRole("heading", { level: 1 }).waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, "mobile-home-zh.png"), fullPage: true });
    await page.goto(base + "/demo?case=association"); await page.locator(".answer-body").waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, "mobile-demo-zh.png"), fullPage: true });
    await page.locator(".source-link").first().click(); await page.locator(".source-excerpt").waitFor();
    await page.waitForFunction(() => document.querySelector(".ant-drawer-content-wrapper").getBoundingClientRect().right <= innerWidth + 1);
    assert.ok(await page.locator(".ant-drawer-mask").isVisible());
    await page.screenshot({ path: path.join(output, "mobile-source-zh.png"), fullPage: true });
    await page.keyboard.press("Escape");
  });

  // Controlled responses validate frontend contracts; they are not live backend evidence.
  const live = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const lp = await live.newPage(); let mode = "success"; const downloads = [];
  lp.on("pageerror", (error) => consoleErrors.push(error.message));
  const server = http.createServer((req, res) => {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Access-Control-Allow-Origin": "*" });
    res.write('data: {"type":"meta","domain":"policy","citations":[]}\n\ndata: {"type":"chunk","content":"已收到的部分回答"}\n\n');
    const timer = setTimeout(() => res.end('data: {"type":"done","answer":"完整回答"}\n\n'), 10000);
    res.on("close", () => clearTimeout(timer));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const streamPort = server.address().port;
  await lp.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "frontend-test-token" } });
    if (url.pathname.endsWith("/auth/me")) return route.fulfill({ json: { username: "test", display_name: "Test account" } });
    if (url.pathname.includes("/attachments/")) { downloads.push(route.request().headers().authorization); return route.fulfill({ contentType: "text/plain", body: "test evidence" }); }
    if (url.pathname.endsWith("/search/all")) {
      if (mode === "searchError") return route.fulfill({ status: 503, body: "Unavailable" });
      return route.fulfill({ json: { items: [{ domain: "tender", record_id: 11, title: "真实记录原文", summary: "业务数据不翻译", key_fields: { budget: 100 }, attachments: [] }] } });
    }
    if (url.pathname.endsWith("/chat/query/stream")) {
      if (mode === "slow") return route.continue({ url: `http://127.0.0.1:${streamPort}/stream` });
      if (mode === "httpError") return route.fulfill({ status: 503, body: "Unavailable" });
      const citation = { domain: "policy", record_id: 9, title: "真实来源原文", key_fields: { clause: 3 }, source_fields: ["content"], attachments: [{ id: 3, original_name: "evidence.txt" }] };
      const meta = `data: ${JSON.stringify({ type: "meta", domain: "policy", citations: [citation] })}\r\n\r\n`;
      const chunk = 'data: {"type":"chunk","content":"真实回答原文"}\n\n';
      return route.fulfill({ contentType: "text/event-stream", body: meta + chunk + (mode === "incomplete" ? "" : 'data: {"type":"done","answer":"真实回答原文"}\n\n') });
    }
    return route.fulfill({ status: 404, body: "Unknown test endpoint" });
  });
  await check("controlled login, real answer text, citation and authorized attachment", async () => {
    await lp.goto(base + "/login"); await lp.getByLabel("用户名").fill("test"); await lp.getByLabel("密码", { exact: true }).fill("test-password");
    await lp.getByRole("button", { name: "进入工作台" }).click(); await lp.waitForURL("**/chat");
    await lp.getByRole("textbox", { name: "真实问答问题" }).fill("测试问题"); await lp.getByRole("button", { name: "发送问题" }).click();
    await lp.getByText("真实回答原文", { exact: true }).waitFor();
    await lp.getByRole("button", { name: "切换到英文" }).click(); assert.equal(await lp.locator(".answer-body").innerText(), "真实回答原文");
    await lp.locator(".source-link").click(); await lp.getByText(/no original excerpt/).waitFor();
    const download = lp.waitForEvent("download"); await lp.getByRole("button", { name: "evidence.txt" }).click();
    await (await download).saveAs(path.join(output, "test-evidence.txt")); assert.deepEqual(downloads, ["Bearer frontend-test-token"]);
    await lp.keyboard.press("Escape");
  });
  await check("controlled HTTP error and incomplete SSE preserve explicit failure", async () => {
    for (const nextMode of ["httpError", "incomplete"]) {
      mode = nextMode; await lp.getByRole("textbox", { name: "Live question" }).fill(nextMode); await lp.getByRole("button", { name: "Send question", exact: true }).click();
      await lp.locator(".message-error").last().waitFor();
      assert.ok((await lp.locator(".message-error").last().innerText()).includes("request failed"));
    }
  });
  await check("controlled request cancellation and re-enabled composer", async () => {
    mode = "slow"; await lp.getByRole("textbox", { name: "Live question" }).fill("stop this request"); await lp.getByRole("button", { name: "Send question", exact: true }).click();
    await lp.getByText("已收到的部分回答", { exact: true }).waitFor();
    await lp.getByRole("button", { name: "Stop generation" }).click(); await lp.getByText("Generation stopped. Received content is preserved.").waitFor();
    assert.equal(await lp.locator(".answer-body").last().innerText(), "已收到的部分回答");
    assert.equal(await lp.getByRole("textbox", { name: "Live question" }).isEnabled(), true);
    await lp.evaluate(() => {
      const sessions = JSON.parse(localStorage.getItem("rag_chat_history"));
      sessions[0].messages.at(-1).status = "pending";
      localStorage.setItem("rag_chat_history", JSON.stringify(sessions));
    });
    await lp.reload(); await lp.getByText("Generation stopped. Received content is preserved.").waitFor();
    assert.equal(await lp.getByRole("textbox", { name: "Live question" }).isEnabled(), true);
  });
  await check("controlled real search, untranslated records, source detail and failure", async () => {
    mode = "success"; await lp.getByRole("link", { name: "Search", exact: true }).click();
    await lp.getByRole("textbox", { name: "Search live records" }).fill("software"); await lp.getByRole("button", { name: "Search", exact: true }).click();
    await lp.getByText("真实记录原文", { exact: true }).waitFor(); await lp.locator(".record-row").click(); await lp.getByText("业务数据不翻译", { exact: true }).last().waitFor(); await lp.keyboard.press("Escape");
    mode = "searchError"; await lp.getByRole("button", { name: "Search", exact: true }).click(); await lp.getByText(/Search failed/).waitFor();
    assert.equal(await lp.locator(".record-row").count(), 0);
  });
  await check("no browser runtime errors on public or controlled live routes", async () => assert.deepEqual(consoleErrors, []));
  await live.close();
  server.closeAllConnections(); await new Promise((resolve) => server.close(resolve));
} finally {
  await fs.writeFile(path.join(output, "browser-report.json"), JSON.stringify({ base, results, publicApiRequests: apiRequests, consoleErrors, liveBackendVerified: false }, null, 2));
  await browser.close();
}
if (failures.length) process.exitCode = 1;
