import test from "node:test";
import assert from "node:assert/strict";
import { cases, records, matchQuestion, searchRecords, localizeRecord } from "../src/data/demo.js";
import { consumeStream } from "../src/api/stream.js";

test("every bilingual case and follow-up resolves to existing, bilingual evidence", () => {
  for (const item of cases) {
    for (const language of ["zh", "en"]) {
      for (const entry of [item, item.followup]) {
        const match = matchQuestion(entry.question[language]);
        assert.equal(match.answer[language], entry.answer[language]);
        assert.ok(match.sources.length);
        for (const id of match.sources) {
          const source = records.find((r) => r.id === id); assert.ok(source, id);
          const view = localizeRecord(source, language);
          assert.ok(view.title && view.excerpt && Object.keys(view.key_fields).length);
        }
      }
    }
  }
});
test("free text cannot accidentally produce a canned eligibility or factual answer", () => {
  for (const value of ["", "who won a real project?", "云川科技去年营收是多少", "not supplier requirements", "资格判断是不是真的"]) assert.equal(matchQuestion(value), null);
  assert.equal(matchQuestion(" SUPPLIER requirements? ").id, "policy");
});
test("search supports both languages, domain filters and empty results", () => {
  assert.deepEqual(searchRecords("云川").map((r) => r.id), ["T-001", "E-001"]);
  assert.deepEqual(searchRecords("Yunchuan", "enterprise").map((r) => r.id), ["E-001"]);
  assert.equal(searchRecords("", "tender").length, 2);
  assert.equal(searchRecords("no such company").length, 0);
  assert.equal(searchRecords("").length, 4);
});
function byteStream(text, size = 1) {
  const bytes = new TextEncoder().encode(text); let offset = 0;
  return new ReadableStream({ pull(controller) {
    if (offset >= bytes.length) { controller.close(); return; }
    controller.enqueue(bytes.slice(offset, offset + size)); offset += size;
  } });
}
test("SSE handles split UTF-8, CRLF, comments and final unterminated frames", async () => {
  const result = [];
  await consumeStream(byteStream(': keepalive\r\n\r\ndata: {"type":"meta","domain":"policy"}\r\n\r\ndata: {"type":"chunk","content":"中文证据"}\n\ndata: {"type":"done","answer":"完整回答"}'), (p) => result.push(p));
  assert.equal(result.length, 3); assert.equal(result[1].content, "中文证据"); assert.equal(result[2].answer, "完整回答");
});
test("SSE reports malformed payloads and releases readers after errors", async () => {
  const stream = byteStream("data: not-json\n\n");
  await assert.rejects(consumeStream(stream, () => {}), SyntaxError);
  assert.equal(stream.locked, false);
  const next = byteStream('data: {"type":"error"}\n\n');
  await assert.rejects(consumeStream(next, () => { throw new Error("API failed"); }), /API failed/);
  assert.equal(next.locked, false);
});
