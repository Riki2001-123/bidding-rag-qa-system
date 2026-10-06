import { useEffect, useRef, useState } from "react";
import { Button, Input, Modal, message } from "antd";
import { DeleteOutlined, ExportOutlined, PlusOutlined, SendOutlined, StopOutlined } from "@ant-design/icons";
import { API_BASE, getToken } from "../api/client";
import { consumeStream } from "../api/stream";
import { KnowledgeMessage, SourcePanel } from "../components/KnowledgeUI";
import { useLocale } from "../i18n";

const STORAGE_KEY = "rag_chat_history";
const fresh = () => ({ id: crypto.randomUUID(), title: "", messages: [], serverSessionId: null });
function loadSessions() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    if (Array.isArray(saved) && saved.length) return saved.map((s) => ({
      id: s.id || crypto.randomUUID(), title: s.title || "", serverSessionId: s.serverSessionId || null,
      messages: Array.isArray(s.messages) ? s.messages.filter((m) => m && ["user", "assistant"].includes(m.role)).map((m) => ({ ...m, status: m.status === "pending" ? "stopped" : m.status, content: String(m.content || ""), citations: Array.isArray(m.citations) ? m.citations : [] })) : [],
    }));
  } catch { /* Keep working if old storage is invalid. */ }
  return [fresh()];
}
export default function ChatPage() {
  const { tr, domainLabel } = useLocale();
  const [sessions, setSessions] = useState(loadSessions);
  const [activeId, setActiveId] = useState(null);
  const [input, setInput] = useState(""); const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false); const [source, setSource] = useState(null);
  const abortRef = useRef(null); const endRef = useRef(null); const mounted = useRef(true);
  const active = sessions.find((s) => s.id === activeId) || sessions[0];
  useEffect(() => { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions)); } catch { /* Storage may be full or blocked. */ } }, [sessions]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; abortRef.current?.abort(); }; }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest" }); }, [active?.messages]);
  function update(id, patch) { if (mounted.current) setSessions((prev) => prev.map((s) => s.id === id ? { ...s, ...patch } : s)); }
  async function send(e) {
    e?.preventDefault(); const question = input.trim(); if (!question || busy) return;
    const id = active.id; const messages = [...active.messages, { role: "user", content: question }];
    let answer = ""; let citations = []; let domain = ""; let serverId = active.serverSessionId; let complete = false;
    const assistant = (status = "") => ({ role: "assistant", content: answer, citations, domain, status });
    update(id, { title: active.messages.length ? active.title : question.slice(0, 36), messages: [...messages, assistant("pending")] });
    setInput(""); setBusy(true); setSource(null);
    const controller = new AbortController(); abortRef.current = controller;
    try {
      const response = await fetch(`${API_BASE}/chat/query/stream`, {
        method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({ question, session_id: serverId, top_k: 5 }), signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error("Stream request failed");
      await consumeStream(response.body, (payload) => {
        if (payload.type === "error") throw new Error("Stream error");
        if (payload.session_id) serverId = payload.session_id;
        if (payload.type === "meta") { domain = payload.domain || ""; citations = payload.citations || []; }
        if (payload.type === "chunk") answer += payload.content || "";
        if (payload.type === "done") { answer = payload.answer ?? answer; complete = true; }
        update(id, { messages: [...messages, assistant(complete ? "" : "pending")], serverSessionId: serverId });
      });
      if (!complete) throw new Error("Incomplete stream");
    } catch (error) {
      update(id, { messages: [...messages, assistant(error.name === "AbortError" ? "stopped" : "error")], serverSessionId: serverId });
    } finally { if (mounted.current) setBusy(false); abortRef.current = null; }
  }
  function newSession() { const item = fresh(); setSessions((prev) => [item, ...prev]); setActiveId(item.id); setSource(null); }
  function remove(id) {
    Modal.confirm({ title: tr("删除对话？", "Delete conversation?"), content: tr("该对话将从此浏览器移除。", "This removes the conversation from this browser."),
      okText: tr("删除", "Delete"), cancelText: tr("取消", "Cancel"), okButtonProps: { danger: true },
      onOk: () => { setSessions((prev) => { const next = prev.filter((s) => s.id !== id); return next.length ? next : [fresh()]; }); if (active.id === id) setActiveId(null); },
    });
  }
  function exportSession() {
    const body = active.messages.map((m) => `**${m.role === "user" ? tr("用户", "User") : tr("助手", "Assistant")}**\n${m.content}\n${m.status ? tr("状态：", "Status: ") + m.status : ""}\n${(m.citations || []).map((c) => "- " + c.title).join("\n")}`).join("\n\n");
    const url = URL.createObjectURL(new Blob(["# " + (active.title || tr("对话", "Conversation")) + "\n\n" + body], { type: "text/markdown;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = "conversation.md"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    message.success(tr("已导出对话", "Conversation exported"));
  }
  const suggestions = [
    tr("政府采购供应商资格需要哪些材料？", "What documents establish supplier eligibility?"),
    tr("最近有哪些软件采购项目？", "What software procurement projects are available?"),
    tr("查询一家企业的经营范围。", "Look up a company's business scope."),
  ];
  return <main className="live-chat"><aside className="live-chat-sidebar"><details open className="session-details"><summary>{tr("会话记录", "Conversations")}</summary>
    <Button onClick={newSession} icon={<PlusOutlined />} disabled={busy} block>{tr("新对话", "New conversation")}</Button>
    <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={tr("搜索对话", "Search conversations")} aria-label={tr("搜索对话", "Search conversations")} />
    <div className="live-sessions">{sessions.filter((s) => s.title.toLowerCase().includes(query.toLowerCase())).map((s) => <div className={s.id === active.id ? "active" : ""} key={s.id}>
      <button disabled={busy} onClick={() => { setActiveId(s.id); setSource(null); }}>{s.title || tr("新对话", "New conversation")}</button>
      <Button size="small" type="text" disabled={busy} icon={<DeleteOutlined />} aria-label={tr("删除对话", "Delete conversation")} onClick={() => remove(s.id)} />
    </div>)}</div></details></aside>
    <section className="live-chat-main"><div className="workspace-topbar"><span className="muted small">{tr("真实数据 · 原文回答", "Live data · Original answer text")}</span><Button type="text" icon={<ExportOutlined />} disabled={!active.messages.length} onClick={exportSession}>{tr("导出", "Export")}</Button></div>
      <div className="live-messages" aria-live="polite">{!active.messages.length && <div className="demo-welcome"><span className="welcome-symbol">✦</span><h2>{tr("你好，想了解什么？", "What would you like to explore?")}</h2><p>{tr("在项目、政策与企业资料中查找答案，查看相关来源。", "Find answers across projects, policies and company records, then inspect their sources.")}</p><div className="live-suggestions">{suggestions.map((q) => <button key={q} onClick={() => setInput(q)}>{q}</button>)}</div></div>}
        {active.messages.map((item, i) => item.role === "user" ? <div className="question-bubble" key={i}>{item.content}</div> : <div key={i}>
          {item.content && <KnowledgeMessage content={item.content} domain={item.domain} citations={item.citations} onSource={setSource} />}
          {item.status === "pending" && <p className="message-status">{tr("正在检索与生成…", "Retrieving and generating…")}</p>}
          {item.status === "stopped" && <p className="message-status">{tr("已停止生成，已收到的内容保留。", "Generation stopped. Received content is preserved.")}</p>}
          {item.status === "error" && <p className="message-error" role="alert">{tr("请求失败或连接中断。请检查后端与登录状态后重试；以上可能是不完整回答。", "The request failed or the connection ended. Check the backend and sign-in state, then retry. Any answer above may be incomplete.")}</p>}
        </div>)}<div ref={endRef} />
      </div><div className="demo-input-area"><form className="demo-composer" onSubmit={send}><Input.TextArea value={input} onChange={(e) => setInput(e.target.value)} placeholder={tr("输入问题，Enter 发送，Shift + Enter 换行", "Enter to send, Shift + Enter for a new line")} aria-label={tr("真实问答问题", "Live question")} autoSize={{ minRows: 1, maxRows: 5 }} variant="borderless" disabled={busy} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
        {busy ? <Button icon={<StopOutlined />} danger onClick={() => abortRef.current?.abort()} aria-label={tr("停止生成", "Stop generation")} /> : <Button htmlType="submit" type="primary" icon={<SendOutlined />} disabled={!input.trim()} aria-label={tr("发送问题", "Send question")} />}</form><p>{tr("重要信息请核验来源原文；业务数据与模型回复不会随界面语言自动翻译。", "Verify important information against original sources. Business data and model answers are not automatically translated.")}</p></div>
    </section><SourcePanel source={source} onClose={() => setSource(null)} />
  </main>;
}
