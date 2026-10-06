import { Link } from "react-router-dom";
import { ArrowRightOutlined, ArrowUpOutlined, CheckOutlined, FileTextOutlined, GithubOutlined, NodeIndexOutlined, SearchOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import PublicHeader, { PROFILE_URL, REPO_URL } from "../components/PublicHeader";
import { useLocale } from "../i18n";

function ProductPreview() {
  const { tr } = useLocale();
  return <div className="product-preview" aria-label={tr("样例产品预览", "Sample product preview")}>
    <div className="preview-toolbar"><span className="preview-dots"><i /><i /><i /></span><span>knowledge.workspace</span><span className="preview-live">{tr("样例预览", "SAMPLE")}</span></div>
    <div className="preview-layout"><div className="preview-rail"><span className="mini-logo">r·</span><SearchOutlined /><FileTextOutlined /><NodeIndexOutlined /></div>
      <div className="preview-conversation"><div className="preview-section-label">{tr("跨领域 · 证据关联", "CROSS-DOMAIN · EVIDENCE")}</div>
        <div className="preview-question">{tr("云川科技是否符合样例采购要求？", "Does Yunchuan meet the sample requirements?")}</div>
        <div className="preview-answer"><span className="answer-mark">✦</span><h3>{tr("让结论有据可查。", "An answer you can inspect.")}</h3><p>{tr("有同类项目记录，但主体证明与服务方案缺失。当前证据不足以确认资格。", "A comparable project is recorded, but registration and service documents are missing. Eligibility cannot be confirmed.")}</p></div>
        <div className="preview-source"><FileTextOutlined /><span>{tr("采购指引 · 第 3 条", "Procurement guide · Clause 3")}</span><span>01</span></div>
        <div className="preview-source"><FileTextOutlined /><span>{tr("企业档案与项目公告", "Company profile & award")}</span><span>02</span></div>
        <div className="preview-composer">{tr("继续探索你的业务知识…", "Explore your business knowledge…")}<ArrowRightOutlined /></div>
      </div></div>
    <div className="preview-caption"><span className="status-dot" />{tr("虚构样例 · 预设答案 · 可查看引用", "Fictional sample · Preset answer · Inspectable sources")}</div>
  </div>;
}

export default function ShowcasePage() {
  const { tr } = useLocale();
  const capabilities = [
    ["01", tr("问业务，也问知识。", "Business data meets knowledge."), tr("在政策、项目与企业三个领域中查询，结合结构化检索和文本检索回答问题。", "Query policies, tenders and companies using structured records and text retrieval."), <SearchOutlined />],
    ["02", tr("答案背后，有证据。", "Evidence behind every answer."), tr("回答携带来源记录与关键字段。证据不足时保守回答，方便回到原始资料核验。", "Answers carry source records and key fields. Conservative answers flag missing evidence for review."), <SafetyCertificateOutlined />],
    ["03", tr("从单次提问到连续探索。", "From a question to a conversation."), tr("支持会话管理、流式输出和业务角色权限，让技术成为可使用的产品。", "Conversation management, streamed output and role-based access turn a retrieval pipeline into a usable product."), <NodeIndexOutlined />],
  ];
  return <div className="showcase"><PublicHeader />
    <main>
      <section className="hero section-width">
        <div className="hero-copy"><div className="eyebrow"><span className="status-dot" />{tr("企业 AI 应用 / 工程案例", "ENTERPRISE AI / ENGINEERING CASE STUDY")}</div>
          <h1>{tr("把企业知识", "Your knowledge.")}<br />{tr("变成", "Your data.")}<span>{tr("有依据的答案。", "Clear answers.")}</span></h1>
          <p className="hero-description">{tr("让政策、项目与企业数据连接起来。一个集问答、检索与来源核验于一体的 RAG 应用，展示从 AI 能力到业务产品的实现。", "Connect policies, projects and company records. A RAG application that brings together Q&A, search and source inspection — from an AI pipeline to a business product.")}</p>
          <div className="hero-actions"><Link className="button primary" to="/demo">{tr("体验交互演示", "Explore the demo")}<ArrowRightOutlined /></Link><a className="button secondary" href={REPO_URL} target="_blank" rel="noreferrer"><GithubOutlined />{tr("查看源码", "View source")}</a></div>
          <p className="hero-footnote">{tr("无需登录或 API 密钥 · 双语样例体验", "No sign-in or API key · Bilingual sample experience")}</p>
          <div className="hero-author"><span className="author-avatar">R</span><span><strong>{tr("由 Riki 设计与开发", "Designed & developed by Riki")}</strong><small>{tr("企业知识库 · RAG 应用 · 后端集成", "Knowledge systems · RAG applications · Backend integration")}</small></span></div>
        </div><div className="hero-visual"><div className="visual-label">PRODUCT IN PRACTICE <span>001 / RAG</span></div><ProductPreview /><div className="visual-bottom"><span>{tr("招投标采购 / 示例业务领域", "PROCUREMENT / EXAMPLE DOMAIN")}</span><span>↗</span></div></div>
      </section>
      <div className="stack-strip"><div className="section-width"><span>{tr("实现技术", "BUILT WITH")}</span>{["React", "FastAPI", "MySQL", "Milvus", "BM25 + RRF", "LLM"].map((tech) => <strong key={tech}>{tech}</strong>)}</div></div>
      <section className="capability-section section-width" id="capabilities"><div className="section-heading"><div><div className="eyebrow">01 / {tr("已实现能力", "IMPLEMENTED CAPABILITIES")}</div><h2>{tr("从查找信息，到理解业务。", "Less searching. More understanding.")}</h2></div><p>{tr("招投标是这个案例的场景。\n知识、数据与证据的连接，是它的核心。", "Procurement is the setting.\nConnecting knowledge, data and evidence is the core.")}</p></div>
        <div className="capability-grid">{capabilities.map(([number, title, description, icon]) => <article key={number}><div className="capability-top"><span>{number}</span>{icon}</div><h3>{title}</h3><p>{description}</p></article>)}</div>
      </section>
      <section className="scenarios-section"><div className="section-width scenario-layout"><div><div className="eyebrow">02 / {tr("四种业务视角", "FOUR BUSINESS PERSPECTIVES")}</div><h2>{tr("让客户看到", "See the product")}<br />{tr("问题如何被解决。", "solve a real-shaped question.")}</h2><p>{tr("用小而完整的虚构资料集，体验查询、筛选、关联与证据不足时的回答。", "A small, complete fictional dataset illustrates search, filtering, connections and an honest answer when evidence is missing.")}</p><Link className="text-link" to="/demo">{tr("打开样例工作台", "Open the sample workspace")}<ArrowRightOutlined /></Link></div>
        <div className="scenario-list">{[
          [tr("政策解读", "Policy questions"), tr("软件采购供应商需要准备什么？", "What must a software supplier prepare?"), "policy"],
          [tr("项目筛选", "Project filtering"), tr("预算超过 100 万的项目有哪些？", "Which budgets exceed CNY 1M?"), "tender"],
          [tr("企业查询", "Company lookup"), tr("这家企业有哪些业务？", "What does this company do?"), "enterprise"],
          [tr("跨领域关联", "Cross-domain reasoning"), tr("哪些证据支持，哪些还缺失？", "What is supported, and what is missing?"), "association"],
        ].map(([label, question, id], i) => <Link key={id} to={`/demo?case=${id}`} className="scenario-row"><span>0{i + 1}</span><div><small>{label}</small><strong>{question}</strong></div><ArrowRightOutlined /></Link>)}</div>
      </div></section>
      <section className="architecture-section section-width"><div className="section-heading"><div><div className="eyebrow">03 / {tr("技术路径", "THE ENGINEERING")}</div><h2>{tr("界面之下，一条完整链路。", "A complete pipeline underneath.")}</h2></div><a className="text-link" href={REPO_URL} target="_blank" rel="noreferrer">{tr("查看实现", "Inspect the code")}<ArrowUpOutlined /></a></div>
        <div className="pipeline">{[
          ["01", tr("理解问题", "Understand"), "Domain · Intent · Rewrite"],
          ["02", tr("混合召回", "Retrieve"), "SQL · Milvus · BM25"],
          ["03", tr("融合与校验", "Rank & validate"), "RRF · Optional reranker"],
          ["04", tr("生成与引用", "Answer & cite"), "LLM · SSE · Sources"],
        ].map(([n, title, detail]) => <div key={n}><span>{n}</span><h3>{title}</h3><p>{detail}</p></div>)}</div><p className="architecture-note">{tr("真实后端包含多 Agent 编排与复杂问题的 ReAct 路由。具体模型及重排能力取决于部署配置；公开演示使用预设数据，不运行该链路。", "The backend includes multi-agent orchestration and ReAct routing for complex questions. Models and reranking depend on deployment configuration. The public demo uses preset data and does not run this pipeline.")}</p>
      </section>
      <section className="contact-section section-width"><div><div className="eyebrow">04 / {tr("可定制服务", "CUSTOM DEVELOPMENT")}</div><h2>{tr("你的业务，下一步。", "Your business. The next step.")}</h2><p>{tr("可洽谈企业知识库问答、业务 API 集成、RAG 检索优化与产品界面开发。文档接入、私有化部署等需求需单独确定范围与验收。", "Available for knowledge Q&A, business API integration, RAG retrieval improvements and product interfaces. Document ingestion and private deployment require a separate scope and acceptance plan.")}</p><div className="service-tags">{[tr("知识库问答", "Knowledge Q&A"), tr("后端集成", "Backend integration"), tr("检索优化", "Retrieval tuning")].map((s) => <span key={s}><CheckOutlined />{s}</span>)}</div></div><a className="contact-link" href={PROFILE_URL} target="_blank" rel="noreferrer"><span>{tr("与 Riki 联系", "Get in touch with Riki")}</span><ArrowUpOutlined /><small>{tr("通过 GitHub 主页了解与联系", "Find Riki on GitHub")}</small></a></section>
    </main><footer className="public-footer section-width"><span>© 2026 Riki · KNOWLEDGE AI</span><span>{tr("工程案例 · 公开样例", "Engineering portfolio · Public samples")}</span><Link to="/login">{tr("真实系统登录", "Live system sign-in")}<ArrowRightOutlined /></Link></footer>
  </div>;
}
