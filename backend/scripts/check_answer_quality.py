"""Opt-in real integration checks. Calls configured LLM and creates test conversations.

Run with MySQL/Milvus/backend available; --restore-session ID verifies after restart.
Reports contain business evidence and stay in ignored dist/answer-quality-tests.
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

root = Path(__file__).resolve().parents[2]
out = root / "dist/answer-quality-tests"
out.mkdir(parents=True, exist_ok=True)
parser = argparse.ArgumentParser()
parser.add_argument("--restore-session", type=int)
args = parser.parse_args()
report = {"phase": "after-restart" if args.restore_session else "initial", "checks": [], "runs": []}
report_file = out / ("restart-qa.json" if args.restore_session else "real-qa.json")


def save():
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def check(name, passed, **detail):
    report["checks"].append({"name": name, "status": "passed" if passed else "failed", **detail})
    save()
    print(("PASS " if passed else "FAIL ") + name, flush=True)


with httpx.Client(base_url="http://127.0.0.1:8000/api", trust_env=False,
                  timeout=httpx.Timeout(180, connect=5)) as client:
    for attempt in range(45):
        try:
            if client.get("/health", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Backend unavailable")
    login = client.post("/auth/login", json={"username": os.getenv("RAG_TEST_USER", "admin"),
                                             "password": os.getenv("RAG_TEST_PASSWORD", "admin123")})
    check("real login", login.status_code == 200)
    login.raise_for_status()
    client.headers["Authorization"] = "Bearer " + login.json()["access_token"]

    def stream(question, session_id=None):
        events = []
        start = time.monotonic()
        with client.stream("POST", "/chat/query/stream", json={"question": question, "domain": "policy",
                                                             "session_id": session_id, "top_k": 3}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))
        meta = next((e for e in reversed(events) if e["type"] == "meta"), {})
        done = next((e for e in events if e["type"] == "done"), {})
        run = {"question": question, "seconds": round(time.monotonic() - start, 2), "events": events,
               "answer": done.get("answer", ""), "session_id": done.get("session_id"),
               "citations": meta.get("citations", []), "conservative": meta.get("conservative")}
        report["runs"].append(run)
        check("stream completes " + str(len(report["runs"])), bool(done.get("session_id"))
              and not any(e["type"] == "error" for e in events), seconds=run["seconds"])
        return run

    if args.restore_session:
        previous = client.get("/chat/sessions/" + str(args.restore_session))
        previous.raise_for_status()
        last = next(m for m in reversed(previous.json()["messages"]) if m["role"] == "assistant")
        previous_ids = {c["record_id"] for c in json.loads(last["citations_json"])}
        follow = stream("请把刚才的适用范围归纳成两点。", args.restore_session)
        check("restart restores persisted sources", bool(follow["citations"])
              and {c["record_id"] for c in follow["citations"]} <= previous_ids
              and follow["session_id"] == args.restore_session)
        response = client.post("/chat/query", json={"question": "请把上一轮的回答总结成两点。",
                                                    "domain": "policy", "session_id": args.restore_session, "top_k": 3})
        check("synchronous policy answer completes", response.status_code == 200)
        response.raise_for_status()
        sync = response.json()
        report["runs"].append({**sync, "question": "请把上一轮的回答总结成两点。"})
        check("sync uses the same sources", bool(sync["citations"])
              and {c["record_id"] for c in sync["citations"]} <= previous_ids)
        response = client.post("/chat/query", json={"question": "政府采购法是否统一规定免审核金额为999999999亿元？只引用这一金额对应的法条。",
                                                    "domain": "policy", "top_k": 3})
        response.raise_for_status()
        unsupported = response.json()
        report["unsupported"] = unsupported
        check("unsupported amount is not invented", unsupported["conservative"]
              and not unsupported["citations"] and "暂时无法确认" in unsupported["answer"])
    else:
        first = stream("政府采购法的适用范围是什么？请简要说明并列出来源。")
        check("first answer has source quotations", bool(first["citations"])
              and all(c.get("excerpt") for c in first["citations"]))
        follow = stream("请把刚才的适用范围归纳成两点。", first["session_id"])
        check("implicit followup uses same session", follow["session_id"] == first["session_id"])
        check("implicit followup only uses previous sources", bool(follow["citations"])
              and {c["record_id"] for c in follow["citations"]} <= {c["record_id"] for c in first["citations"]})
        previous_quotes = {c["record_id"]: c.get("excerpt", "") for c in first["citations"]}
        check("format followup cannot introduce new clauses", bool(follow["citations"]) and all(
            q in previous_quotes.get(c["record_id"], "") for c in follow["citations"] for q in c.get("excerpt", "").split("\n\n")))
    check("two-point request is bounded", 1 <= len(re.findall(r"^\d+\. ", follow["answer"], re.M)) <= 2)
    engine = create_engine(dotenv_values(root / ".env")["DATABASE_URL"])
    with engine.connect() as db:
        for idx, run in enumerate(report["runs"], 1):
            valid = bool(run["citations"])
            for citation in run["citations"]:
                row = db.execute(text("SELECT title,content FROM policy_records WHERE id=:id"),
                                 {"id": citation["record_id"]}).first()
                quotes = citation.get("excerpt", "").split("\n\n")
                valid = valid and bool(row and row[0] == citation["title"] and all(
                    q and q in (row[1] or "") and q in run["answer"] for q in quotes))
            check("quotations exactly match MySQL " + str(idx), valid)
        sid = args.restore_session or first["session_id"]
        report["sessions"] = [dict(r._mapping) for r in db.execute(text(
            "SELECT s.id,s.title,COUNT(m.id) AS messages FROM chat_sessions s LEFT JOIN chat_messages m "
            "ON m.session_id=s.id WHERE s.id=:id GROUP BY s.id,s.title"), {"id": sid})]
    engine.dispose()
save()
if any(c["status"] != "passed" for c in report["checks"]):
    raise SystemExit(1)
