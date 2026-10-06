import { useMemo, useState } from "react";
import { Alert, Button, Input, Select } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { apiFetch } from "../api/client";
import { SearchResults, SourcePanel } from "../components/KnowledgeUI";
import { useLocale } from "../i18n";
export default function SearchPage() {
  const { tr, domainLabel } = useLocale();
  const [query, setQuery] = useState(""); const [topK, setTopK] = useState(10);
  const [items, setItems] = useState([]); const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("all"); const [source, setSource] = useState(null);
  const [error, setError] = useState(false); const [searched, setSearched] = useState(false);
  const filtered = useMemo(() => items.filter((r) => filter === "all" || r.domain === filter), [items, filter]);
  async function search(e) {
    e.preventDefault(); setLoading(true); setError(false); setSource(null); setItems([]);
    try {
      const params = new URLSearchParams({ top_k: String(topK) }); if (query.trim()) params.set("q", query.trim());
      const data = await apiFetch(`/search/all?${params}`); setItems(data.items || []); setFilter("all"); setSearched(true);
    } catch { setError(true); } finally { setLoading(false); }
  }
  return <main className="live-content"><div className="eyebrow">BUSINESS RECORDS</div><h1>{tr("统一智能检索", "Unified knowledge search")}</h1><p className="muted">{tr("同时检索项目、政策和企业数据；来源详情使用真实接口返回的信息。", "Search projects, policies and companies together. Source details show information returned by the live API.")}</p>
    <form className="live-search-form" onSubmit={search}><Input size="large" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={tr("企业名称、项目名称或政策关键词", "Company, project or policy keyword")} aria-label={tr("搜索真实数据", "Search live records")} prefix={<SearchOutlined />} allowClear />
      <Select aria-label={tr("每领域结果数", "Results per domain")} value={topK} onChange={setTopK} options={[5, 10, 20, 50].map((value) => ({ value, label: tr(`每域 ${value} 条`, `${value} per domain`) }))} /><Button type="primary" htmlType="submit" size="large" loading={loading}>{tr("搜索", "Search")}</Button></form>
    <div className="filter-bar">{["all", "policy", "tender", "enterprise"].map((d) => <button key={d} className={filter === d ? "active" : ""} onClick={() => setFilter(d)} aria-pressed={filter === d}>{domainLabel(d)}<span>{items.filter((r) => d === "all" || r.domain === d).length}</span></button>)}</div>
    {error ? <Alert type="error" showIcon message={tr("检索失败，请检查后端连接或登录状态后重试。", "Search failed. Check the backend connection or sign-in state and retry.")} /> :
      <SearchResults items={filtered} onSource={setSource} emptyText={!searched ? tr("输入关键词，开始查找业务资料", "Enter a keyword to explore business records") : undefined} />}
    <SourcePanel source={source} onClose={() => setSource(null)} />
  </main>;
}
