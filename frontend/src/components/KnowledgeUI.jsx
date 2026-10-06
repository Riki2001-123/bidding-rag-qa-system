import { useState } from "react";
import { Button, Drawer, Grid, message } from "antd";
import { CheckOutlined, CopyOutlined, DownloadOutlined, FileTextOutlined, ArrowRightOutlined } from "@ant-design/icons";
import { useLocale } from "../i18n";
import { apiFetch } from "../api/client";

export function DomainTag({ domain }) {
  const { domainLabel } = useLocale();
  return <span className={`domain-pill ${domain}`}>{domainLabel(domain)}</span>;
}

export function KnowledgeMessage({ content, citations = [], domain, onSource, sample = false }) {
  const { tr } = useLocale();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(content); setCopied(true); }
    catch { message.error(tr("复制失败，请手动选择文字。", "Copy failed. Please select the text manually.")); }
  };
  return <article className="knowledge-message">
    <div className="answer-byline"><span className="answer-mark">✦</span><strong>Riki AI</strong>{domain && <DomainTag domain={domain} />}
      {sample && <span className="muted small">{tr("预设样例回答", "Preset sample answer")}</span>}</div>
    <div className="answer-body">{content}</div>
    {citations.length > 0 && <div className="source-list"><span className="eyebrow">{tr("依据来源", "SOURCES")}</span>
      {citations.map((source, i) => <button className="source-link" key={`${source.domain}-${source.record_id}-${i}`} onClick={() => onSource?.(source)}>
        <span className="source-number">{String(i + 1).padStart(2, "0")}</span><FileTextOutlined /><span>{source.title}</span><ArrowRightOutlined />
      </button>)}
    </div>}
    <Button type="text" size="small" icon={copied ? <CheckOutlined /> : <CopyOutlined />} onClick={copy}>
      {copied ? tr("已复制", "Copied") : tr("复制回答", "Copy answer")}</Button>
  </article>;
}

export function SourcePanel({ source, onClose, sample = false }) {
  const { tr, domainLabel } = useLocale();
  const screens = Grid.useBreakpoint();
  const [downloading, setDownloading] = useState(null);
  async function download(attachment) {
    setDownloading(attachment.id);
    try {
      const blob = await apiFetch(`/attachments/${attachment.id}/download`);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = attachment.original_name; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { message.error(tr("下载失败，文件可能不可用或无访问权限。", "Download failed. The file may be unavailable or access denied.")); }
    finally { setDownloading(null); }
  }
  return <Drawer open={Boolean(source)} onClose={onClose} title={tr("来源详情", "Source details")} width={screens.xl ? 420 : "min(100vw, 420px)"}
    mask={!screens.xl} rootClassName="source-drawer">
    {source && <>
      {sample && <div className="sample-note">{tr("虚构演示资料 · 非正式业务文件", "Fictional sample · Not a business document")}</div>}
      <DomainTag domain={source.domain} /><h2>{source.title}</h2>
      <p className="muted">{domainLabel(source.domain)} / {source.record_id}</p>
      <dl className="source-fields">{Object.entries(source.key_fields || {}).filter(([, v]) => v != null && v !== "").map(([key, value]) =>
        <div key={key}><dt>{key}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
      <h3>{tr("证据内容", "Evidence")}</h3>
      <p className="source-excerpt">{source.excerpt || source.summary || tr("当前接口未提供原文片段；请根据记录信息或附件核验。", "This response has no original excerpt. Check the record information or attachments.")}</p>
      {source.source_fields?.length > 0 && <p className="muted small">{tr("来源字段：", "Source fields: ")}{source.source_fields.join(", ")}</p>}
      {!sample && source.attachments?.length > 0 && <><h3>{tr("附件", "Attachments")}</h3>{source.attachments.map((a) =>
        <Button key={a.id} icon={<DownloadOutlined />} loading={downloading === a.id} onClick={() => download(a)} className="attachment-button">{a.original_name}</Button>)}</>}
    </>}
  </Drawer>;
}

export function SearchResults({ items, onSource, emptyText }) {
  const { tr } = useLocale();
  if (!items.length) return <div className="empty-state"><FileTextOutlined /><h3>{emptyText || tr("没有找到匹配记录", "No matching records")}</h3><p>{tr("尝试其他关键词或切换领域。", "Try another keyword or domain.")}</p></div>;
  return <div className="record-list">{items.map((record) => <button className="record-row" key={`${record.domain}-${record.record_id}`} onClick={() => onSource(record)}>
    <span className="record-icon"><FileTextOutlined /></span><span className="record-content"><span className="record-meta"><DomainTag domain={record.domain} /><span>{record.record_id}</span></span>
      <strong>{record.title}</strong><span className="record-summary">{record.summary || tr("查看记录详情", "View record details")}</span>
      <span className="record-fields">{Object.entries(record.key_fields || {}).filter(([, v]) => v != null && v !== "").slice(0, 3).map(([k, v]) => <span key={k}>{k} · {typeof v === "object" ? JSON.stringify(v) : String(v)}</span>)}</span>
    </span><ArrowRightOutlined /></button>)}</div>;
}
