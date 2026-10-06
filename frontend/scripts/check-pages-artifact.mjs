import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
const output = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../dist/pages-deployment/site");
async function inspect(directory) {
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) { await inspect(file); continue; }
    assert.ok(entry.isFile(), "Publish artifact must contain regular files only");
    assert.ok(entry.name === ".nojekyll" || /\.(html|js|css|svg|woff2?)$/.test(entry.name), "Unexpected published file: " + entry.name);
    assert.ok(!/^(ChatPage|LoginPage|SearchPage|DashboardPage|LayoutShell)-/.test(entry.name), "Live component in public artifact");
    if (/\.(html|js)$/.test(entry.name)) {
      const content = await fs.readFile(file, "utf8");
      assert.ok(!/127\.0\.0\.1:8000|rag_chat_history|\/auth\/login|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY/.test(content), "Private/live payload in public artifact: " + entry.name);
    }
  }
}
await inspect(output);
for (const route of ["", "demo", "login", "chat", "search", "dashboard"]) {
  await fs.access(path.join(output, route, "index.html"));
}
console.log("PASS public-only artifact and direct-route entry points");
