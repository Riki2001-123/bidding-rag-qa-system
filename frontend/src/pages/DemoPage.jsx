import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Button, Input } from "antd";
import { ArrowRightOutlined, ReloadOutlined, SearchOutlined, SendOutlined } from "@ant-design/icons";
import PublicHeader from "../components/PublicHeader";
import { KnowledgeMessage, SearchResults, SourcePanel } from "../components/KnowledgeUI";
import { useLocale } from "../i18n";
import { cases, records, matchQuestion, searchRecords, localizeRecord } from "../data/demo";

export default function DemoPage() {
  const { language, tr, domainLabel } = useLocale();
  const [params] = useSearchParams();
  const [tab, setTab] = useState("chat");
  const [conversation, setConversation] = useState([]);
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [domain, setDomain] = useState("all");
  const [sourceId, setSourceId] = useState(null);
  const [notice, setNotice] = useState(false);
  const endRef = useRef(null);
  const lastId = conversation.at(-1)?.id;
  const lastCase = cases.find((c) => c.id === lastId);
  function runQuestion(question) {
    const matched = matchQuestion(question);
    if (!matched) { setNotice(true); return; }
    setNotice(false); setConversation((prev) => [...prev, matched]); setInput(""); setTab("chat");
  }
  useEffect(() => {
    const item = cases.find((c) => c.id === params.get("case"));
    if (item) { setConversation([item]); setNotice(false); setTab("chat"); }
  }, [params]);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest" }); }, [conversation.length]);
  const visibleRecords = searchRecords(query, domain).map((r) => localizeRecord(r, language));
  const selectedSource = records.find((r) => r.id === sourceId);
  return <div className={`demo-page ${sourceId ? "source-open" : ""}`}><PublicHeader />
    <div className="sample-banner"><span className="status-dot" /><strong>{tr("样例演示", "SAMPLE DEMO")}</strong><span>{tr("虚构数据与预设回答 · 不调用模型或真实业务 API", "Fictional data & preset answers · No model or business API calls")}</span><Link to="/login">{tr("真实系统", "Live system")}<ArrowRightOutlined /></Link></div>
    <main className="demo-workspace">
      <aside className="demo-sidebar"><div className="eyebrow">WORKSPACE / 001</div><h2>{tr("知识，连接起来。", "Knowledge, connected.")}</h2><p>{tr("选择一个问题，查看答案与依据。", "Choose a question. Inspect the answer and its sources.")}</p>
        <div className="case-list">{cases.map((item, i) => <button className={`case-button ${lastId === item.id ? "selected" : ""}`} key={item.id} onClick={() => { setConversation([item]); setNotice(false); setInput(""); setSourceId(null); setTab("chat"); }}>
          <span className="case-index">0{i + 1}</span><span><strong>{item.label[language]}</strong><small>{item.question[language]}</small></span><ArrowRightOutlined /></button>)}</div>
        <div className="sample-dataset"><span className="eyebrow">{tr("样例资料集", "SAMPLE DATASET")}</span><div><strong>04</strong><span>{tr("条虚构记录", "fictional records")}</span></div><p>{tr("1 份指引 / 2 个项目 / 1 家企业", "1 guide / 2 projects / 1 company")}</p><button className="text-link" onClick={() => setTab("search")}>{tr("浏览样例资料", "Browse the sample records")}<ArrowRightOutlined /></button></div>
      </aside>
      <section className="demo-main"><div className="workspace-topbar"><div className="workspace-tabs" role="tablist" aria-label={tr("演示功能", "Demo views")}>
        <button role="tab" aria-selected={tab === "chat"} onClick={() => setTab("chat")}>{tr("智能问答", "Ask a question")}</button>
        <button role="tab" aria-selected={tab === "search"} onClick={() => setTab("search")}>{tr("资料检索", "Search records")}</button>
      </div><Button type="text" icon={<ReloadOutlined />} aria-label={tr("重置", "Reset")} onClick={() => { setConversation([]); setSourceId(null); setInput(""); setQuery(""); setDomain("all"); setNotice(false); }}>{tr("重置", "Reset")}</Button></div>
        {tab === "chat" ? <>
          <div className="demo-messages" role="tabpanel">
            {!conversation.length && <div className="demo-welcome"><span className="welcome-symbol">✦</span><div className="eyebrow">ANSWERS WITH CONTEXT</div><h2>{tr("从一个好问题开始。", "Start with a good question.")}</h2><p>{tr("试着询问规则、筛选项目，或连接企业与采购要求。每个样例答案都附有可查看的依据。", "Explore requirements, filter projects or connect companies to procurement rules. Every sample answer includes sources you can inspect.")}</p><Button type="primary" size="large" onClick={() => runQuestion(cases[3].question[language])}>{tr("试试跨领域关联", "Try a cross-domain question")}<ArrowRightOutlined /></Button></div>}
            {conversation.map((item, i) => <div className="demo-turn" key={i}><div className="question-bubble">{item.question[language]}</div><KnowledgeMessage content={item.answer[language]} citations={item.sources.map((id) => localizeRecord(records.find((r) => r.id === id), language))} domain={item.domain} sample onSource={(r) => setSourceId(r.record_id)} /></div>)}
            {conversation.length > 0 && lastCase && <button className="followup-button" onClick={() => runQuestion(lastCase.followup.question[language])}>{tr("继续探索：", "Follow up: ")}{lastCase.followup.question[language]}<ArrowRightOutlined /></button>}
            <div ref={endRef} />
          </div>
          <div className="demo-input-area">{notice && <div className="input-notice" role="status">{tr("这个问题不在预设案例中。请选择案例或使用其问题文字；样例不会生成新答案。", "That question is outside the preset examples. Choose a case or use its exact question; this demo does not generate new answers.")}</div>}
            <form className="demo-composer" onSubmit={(e) => { e.preventDefault(); runQuestion(input); }}><Input value={input} onChange={(e) => setInput(e.target.value)} placeholder={tr("输入样例问题，如：供应商资格", "Enter a sample question, e.g. supplier requirements")} aria-label={tr("样例问题", "Sample question")} variant="borderless" /><Button htmlType="submit" type="primary" icon={<SendOutlined />} disabled={!input.trim()} aria-label={tr("发送样例问题", "Send sample question")} /></form>
            <p>{tr("预设答案直接呈现 · 非实时 AI 推理 · 刷新清空演示会话", "Preset answers shown directly · No live AI inference · Reload clears this conversation")}</p></div>
        </> : <div className="demo-search" role="tabpanel"><div className="eyebrow">EXPLORE THE EVIDENCE</div><h2>{tr("答案来自这些资料。", "The records behind the answers.")}</h2><p className="muted">{tr("搜索虚构样例中的标题、摘要与关键字段。支持中英文关键词。", "Search titles, summaries and key fields in fictional records. Chinese and English keywords are supported.")}</p>
          <Input size="large" prefix={<SearchOutlined />} value={query} onChange={(e) => setQuery(e.target.value)} placeholder={tr("搜索：云川 / 软件 / 120", "Search: Yunchuan / software / 1.2M")} aria-label={tr("搜索样例资料", "Search sample records")} allowClear />
          <div className="filter-bar">{["all", "policy", "tender", "enterprise"].map((d) => <button className={domain === d ? "active" : ""} key={d} onClick={() => setDomain(d)} aria-pressed={domain === d}>{domainLabel(d)}<span>{searchRecords(query, d).length}</span></button>)}</div>
          <SearchResults items={visibleRecords} onSource={(r) => setSourceId(r.record_id)} />
        </div>}
      </section>
    </main><SourcePanel sample source={selectedSource ? localizeRecord(selectedSource, language) : null} onClose={() => setSourceId(null)} />
  </div>;
}
