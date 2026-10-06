import { useLocale } from "../i18n";
import { Link } from "react-router-dom";
import { ArrowRightOutlined } from "@ant-design/icons";
export default function DashboardPage() {
  const { tr } = useLocale();
  return <main className="live-content"><div className="eyebrow">SYSTEM OVERVIEW</div><h1>{tr("理解系统的工作方式。", "Understand how the system works.")}</h1><p className="muted">{tr("以下为实现架构说明，不代表实时健康状态或性能统计。", "This describes the implementation architecture, not live health or performance metrics.")}</p>
    <div className="overview-grid">{[
      [tr("业务数据", "Business data"), "MySQL", tr("政策、项目、企业及角色权限", "Policies, tenders, companies and role-based access")],
      [tr("混合检索", "Hybrid retrieval"), "Milvus + BM25 + SQL", tr("融合语义、关键词与结构化结果", "Combine semantic, keyword and structured results")],
      [tr("回答生成", "Answer generation"), "LLM + SSE", tr("来源引用与流式问答；模型取决于配置", "Cited, streamed answers; models depend on configuration")],
    ].map(([title, value, description]) => <article key={title}><div className="eyebrow">{title}</div><h2>{value}</h2><p>{description}</p></article>)}</div>
    <section className="overview-domains"><h2>{tr("三个相互连接的业务领域", "Three connected business domains")}</h2>{[
      [tr("政策", "Policies"), tr("条款内容、适用范围与发布时间", "Clauses, scope and publication dates")],
      [tr("项目", "Tenders"), tr("采购内容、预算及中标信息", "Procurement scope, budgets and awards")],
      [tr("企业", "Companies"), tr("地区、行业与经营范围", "Regions, industries and business scope")],
    ].map(([label, detail], i) => <div key={label}><span>0{i + 1}</span><strong>{label}</strong><p>{detail}</p></div>)}</section><Link className="text-link" to="/chat">{tr("开始问答", "Start a conversation")}<ArrowRightOutlined /></Link>
  </main>;
}
