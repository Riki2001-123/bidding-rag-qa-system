import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Input, Modal, Tag, Tooltip, Typography, message } from "antd";
import {
  AppstoreOutlined,
  CheckOutlined,
  CopyOutlined,
  DeleteOutlined,
  DislikeOutlined,
  ExportOutlined,
  FileTextOutlined,
  LikeOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  PlusOutlined,
  SearchOutlined,
  SendOutlined,
  SettingOutlined,
  StopOutlined,
} from "@ant-design/icons";

const { Text, Title } = Typography;
const STORAGE_KEY = "rag_chat_history";

/* === 领域映射 === */
const DOMAIN_LABELS = {
  tender: "招标",
  policy: "政策",
  enterprise: "企业",
};
const DOMAIN_COLORS = {
  tender: "#f59e0b",
  policy: "#2563eb",
  enterprise: "#10b981",
};

/* === 建议问题 === */
const SUGGESTIONS = [
  { icon: <FileTextOutlined />, text: "这家公司最近中标过哪些项目？" },
  { icon: <AppstoreOutlined />, text: "政府采购法对供应商资格条件有哪些要求？" },
  { icon: <MessageOutlined />, text: "帮我概括某企业的基本信息和经营范围。" },
  { icon: <SearchOutlined />, text: "最近有哪些预算金额较高的软件采购项目？" },
];

/* === AI 头像 SVG === */
function AiAvatar({ size = 40 }) {
  return (
    <div
      className="ai-avatar"
      style={{ width: size, height: size }}
    >
      <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="aiGrad" x1="4" y1="4" x2="20" y2="20" gradientUnits="userSpaceOnUse">
            <stop stopColor="#2563eb" />
            <stop offset="1" stopColor="#7c3aed" />
          </linearGradient>
        </defs>
        <path
          d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm-1-13h2v6h-2V7zm0 8h2v2h-2v-2z"
          fill="url(#aiGrad)"
        />
        <circle cx="12" cy="12" r="3" fill="url(#aiGrad)" />
        <path d="M12 3a9 9 0 019 9" stroke="url(#aiGrad)" strokeWidth="2" strokeLinecap="round" fill="none" />
      </svg>
    </div>
  );
}

/* === 用户头像 === */
function UserAvatar({ name = "U", size = 32 }) {
  const initial = name.charAt(0).toUpperCase();
  return (
    <div className="user-avatar" style={{ width: size, height: size }}>
      {initial}
    </div>
  );
}

/* === 工具函数 === */
function createSession() {
  return {
    id: Date.now(),
    title: "新对话",
    messages: [],
    serverSessionId: null,
  };
}

function normalizeSessions(list) {
  if (!Array.isArray(list)) return [];
  return list.map((session) => ({
    id: session.id ?? Date.now(),
    title: session.title || "新对话",
    serverSessionId: session.serverSessionId ?? null,
    messages: Array.isArray(session.messages)
      ? session.messages.map((item) => ({
          role: item.role,
          content: item.content || "",
          citations: Array.isArray(item.citations) ? item.citations : [],
          domain: item.domain || "",
        }))
      : [],
  }));
}

function loadHistory() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? normalizeSessions(JSON.parse(raw)) : [];
  } catch {
    return [];
  }
}

function saveHistory(list) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
}

/* === 消息操作栏（始终可见） === */
function MessageActions({ content }) {
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      message.error("复制失败");
    }
  };

  return (
    <div className="msg-actions">
      <Tooltip title={copied ? "已复制" : "复制"}>
        <Button type="text" size="small" icon={copied ? <CheckOutlined /> : <CopyOutlined />} onClick={onCopy} />
      </Tooltip>
      <Tooltip title="有帮助">
        <Button type="text" size="small" icon={<LikeOutlined />} />
      </Tooltip>
      <Tooltip title="待改进">
        <Button type="text" size="small" icon={<DislikeOutlined />} />
      </Tooltip>
    </div>
  );
}

/* === AI 消息行 === */
function AssistantRow({ item, isStreaming, isLast, onStop }) {
  return (
    <div className="msg-row assistant">
      <AiAvatar size={36} />
      <div className="bubble-wrapper">
        {item.domain && (
          <Tag
            className="domain-tag-sm"
            style={{
              background: `${DOMAIN_COLORS[item.domain]}10`,
              borderColor: `${DOMAIN_COLORS[item.domain]}25`,
              color: DOMAIN_COLORS[item.domain],
            }}
          >
            {DOMAIN_LABELS[item.domain] || item.domain}
          </Tag>
        )}
        <div className="bubble-text">{item.content}</div>
        {item.citations?.length > 0 && (
          <div className="citations">
            <Text type="secondary" style={{ fontSize: 12, marginRight: 4 }}>
              引用来源：
            </Text>
            {item.citations.map((c, ci) => (
              <Tooltip key={ci} title={`[${c.domain}] ${c.title}`}>
                <Tag className="citation-tag">
                  {c.title?.length > 16 ? `${c.title.slice(0, 16)}...` : c.title}
                </Tag>
              </Tooltip>
            ))}
          </div>
        )}
        <MessageActions content={item.content} />
        {isStreaming && isLast && (
          <div className="msg-actions">
            <Tooltip title="停止生成">
              <Button
                type="text"
                size="small"
                icon={<StopOutlined />}
                onClick={onStop}
                className="stop-btn-inner"
              />
            </Tooltip>
          </div>
        )}
      </div>
    </div>
  );
}

/* === 用户消息行 === */
function UserRow({ item }) {
  return (
    <div className="msg-row user">
      <div className="bubble-wrapper">
        <div className="bubble-user">
          <span>{item.content}</span>
        </div>
      </div>
      <UserAvatar size={32} />
    </div>
  );
}

/* === 主组件 === */
export default function ChatPage() {
  const initialSessionsRef = useRef(null);
  if (initialSessionsRef.current === null) {
    const saved = loadHistory();
    initialSessionsRef.current = saved.length > 0 ? saved : [createSession()];
  }

  const [sessions, setSessions] = useState(() => initialSessionsRef.current);
  const [activeId, setActiveId] = useState(() => initialSessionsRef.current[0].id);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searchValue, setSearchValue] = useState("");
  const abortRef = useRef(null);
  const messagesEndRef = useRef(null);

  const activeSession = sessions.find((s) => s.id === activeId) || sessions[0];
  const latestAssistantDomain = useMemo(() => {
    const assistantMessages = (activeSession?.messages || []).filter(
      (item) => item.role === "assistant" && item.domain
    );
    return assistantMessages.at(-1)?.domain || "";
  }, [activeSession]);

  const filteredSessions = useMemo(() => {
    if (!searchValue.trim()) return sessions;
    return sessions.filter((s) =>
      s.title.toLowerCase().includes(searchValue.toLowerCase())
    );
  }, [sessions, searchValue]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeSession?.messages, loading]);

  useEffect(() => {
    saveHistory(sessions);
  }, [sessions]);

  const updateSession = (id, patch) => {
    setSessions((prev) =>
      prev.map((session) => (session.id === id ? { ...session, ...patch } : session))
    );
  };

  const onSend = async () => {
    const question = input.trim();
    if (!question || loading || streaming) return;

    setInput("");
    setLoading(true);

    const userMessage = { role: "user", content: question };
    const nextMessages = [...(activeSession.messages || []), userMessage];
    const title =
      activeSession.messages?.length === 0
        ? question.slice(0, 20) + (question.length > 20 ? "..." : "")
        : activeSession.title;

    updateSession(activeSession.id, { messages: nextMessages, title });

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000/api";
      const token = localStorage.getItem("token") || "";

      const resp = await fetch(`${API_BASE}/chat/query/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          question,
          session_id: activeSession.serverSessionId,
          top_k: 5,
        }),
        signal: controller.signal,
      });

      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(text || `HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let accumulatedContent = "";
      let metaDomain = "";
      let metaCitations = [];

      const updateStreamMessage = (content, domain, citations) => {
        updateSession(activeSession.id, {
          messages: [
            ...nextMessages,
            { role: "assistant", content, citations, domain },
          ],
        });
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data: ")) continue;

          try {
            const payload = JSON.parse(line.slice(6));
            if (payload.type === "meta") {
              metaDomain = payload.domain || "";
              metaCitations = payload.citations || [];
              setLoading(false);
              setStreaming(true);
            } else if (payload.type === "chunk") {
              accumulatedContent += payload.content || "";
              updateStreamMessage(accumulatedContent, metaDomain, metaCitations);
            } else if (payload.type === "done") {
              accumulatedContent = payload.answer || accumulatedContent;
              updateStreamMessage(accumulatedContent, metaDomain, metaCitations);
            }
          } catch {
            // 忽略解析失败的行
          }
        }
      }
    } catch (error) {
      if (error.name === "AbortError") {
        updateSession(activeSession.id, {
          messages: [...nextMessages, { role: "assistant", content: "已停止生成。" }],
        });
      } else {
        message.error(`请求失败：${String(error)}`);
        updateSession(activeSession.id, {
          messages: [
            ...nextMessages,
            { role: "assistant", content: "抱歉，请求出现问题，请稍后再试。" },
          ],
        });
      }
    } finally {
      setLoading(false);
      setStreaming(false);
      abortRef.current = null;
    }
  };

  const onStop = useCallback(() => {
    if (abortRef.current) abortRef.current.abort();
  }, []);

  const onNew = () => {
    const newSession = createSession();
    setSessions((prev) => [newSession, ...prev]);
    setActiveId(newSession.id);
  };

  const onDelete = (id, event) => {
    event.stopPropagation();
    Modal.confirm({
      title: "删除对话",
      content: "确定要删除这条对话吗？",
      okText: "删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: () => {
        setSessions((prev) => {
          const next = prev.filter((session) => session.id !== id);
          if (next.length === 0) {
            const fresh = createSession();
            setActiveId(fresh.id);
            return [fresh];
          }
          if (activeId === id) setActiveId(next[0].id);
          return next;
        });
      },
    });
  };

  const onExportSession = () => {
    if (!activeSession?.messages?.length) {
      message.info("当前对话没有内容。");
      return;
    }
    let content = `# ${activeSession.title}\n\n`;
    activeSession.messages.forEach((item) => {
      const label = item.role === "user" ? "用户" : "助手";
      content += `**${label}**\n${item.content}\n\n`;
      if (item.domain) {
        content += `识别领域：${DOMAIN_LABELS[item.domain] || item.domain}\n\n`;
      }
      if (item.citations?.length) {
        content += "引用：\n";
        item.citations.forEach((c) => {
          content += `- [${c.domain}] ${c.title}\n`;
        });
        content += "\n";
      }
    });
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${activeSession.title}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
    message.success("导出成功。");
  };

  const handleKeyDown = (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };

  const isEmpty = !activeSession?.messages?.length;

  return (
    <div className={`chat-container ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      {/* ===== 侧边栏 ===== */}
      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="sidebar-brand">
            <AiAvatar size={28} />
            {!sidebarCollapsed && (
              <span className="sidebar-brand-text">智能问答</span>
            )}
          </div>
          <Tooltip title={sidebarCollapsed ? "展开" : "收起"} placement="right">
            <Button
              type="text"
              size="small"
              icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setSidebarCollapsed((v) => !v)}
              className="sidebar-toggle"
            />
          </Tooltip>
        </div>

        {!sidebarCollapsed && (
          <>
            <Button
              onClick={onNew}
              block
              icon={<PlusOutlined />}
              className="new-chat-btn"
            >
              新对话
            </Button>
            <div className="sidebar-search">
              <Input
                placeholder="搜索对话..."
                value={searchValue}
                onChange={(e) => setSearchValue(e.target.value)}
                allowClear
                size="small"
                prefix={
                  <SearchOutlined style={{ color: "#9ca3af" }} />
                }
              />
            </div>
          </>
        )}

        <div className="sidebar-sessions">
          {filteredSessions.map((session) => (
            <div
              key={session.id}
              className={`session-item ${session.id === activeId ? "active" : ""}`}
              onClick={() => setActiveId(session.id)}
            >
              {sidebarCollapsed ? (
                <Tooltip title={session.title} placement="right">
                  <FileTextOutlined style={{ fontSize: 16 }} />
                </Tooltip>
              ) : (
                <>
                  <FileTextOutlined className="session-icon" />
                  <span className="session-title">{session.title}</span>
                  <DeleteOutlined
                    className="session-delete"
                    onClick={(e) => onDelete(session.id, e)}
                  />
                </>
              )}
            </div>
          ))}
        </div>

        {!sidebarCollapsed && (
          <div className="sidebar-footer">
            <div className="sidebar-footer-text">系统会自动识别问题所属领域</div>
          </div>
        )}
      </aside>

      {/* ===== 主区域 ===== */}
      <main className="chat-main">
        {/* 顶部状态栏 */}
        <div className="inner-topbar">
          <div className="inner-topbar-left">
            {latestAssistantDomain ? (
              <Tag
                className="domain-tag"
                style={{
                  borderColor: `${DOMAIN_COLORS[latestAssistantDomain]}30`,
                  color: DOMAIN_COLORS[latestAssistantDomain],
                  background: `${DOMAIN_COLORS[latestAssistantDomain]}08`,
                }}
              >
                {DOMAIN_LABELS[latestAssistantDomain] || latestAssistantDomain}
              </Tag>
            ) : (
              <Text type="secondary" style={{ fontSize: 13, color: "#9ca3af" }}>
                自动领域识别问答
              </Text>
            )}
          </div>
          <div className="inner-topbar-right">
            <Tooltip title="导出对话">
              <Button
                type="text"
                size="small"
                icon={<ExportOutlined />}
                onClick={onExportSession}
              />
            </Tooltip>
          </div>
        </div>

        {/* ===== 消息列表 / 空状态 ===== */}
        <div className="messages">
          {isEmpty ? (
            <div className="welcome-page">
              <div className="welcome-content">
                <div className="welcome-avatar">
                  <AiAvatar size={56} />
                </div>
                <Title level={3} className="welcome-title">
                  你好，有什么可以帮你？
                </Title>
                <Text type="secondary" className="welcome-subtitle">
                  直接提问即可，系统会在招标、政策、企业三类数据中自动选择最合适的领域并返回引用依据。
                </Text>
                <div className="suggestion-grid">
                  {SUGGESTIONS.map((item) => (
                    <div
                      key={item.text}
                      className="suggestion-card"
                      onClick={() => setInput(item.text)}
                    >
                      <span className="suggestion-card-icon">{item.icon}</span>
                      <span className="suggestion-card-text">{item.text}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            activeSession.messages.map((item, index) =>
              item.role === "assistant" ? (
                <AssistantRow
                  key={index}
                  item={item}
                  isStreaming={streaming}
                  isLast={index === activeSession.messages.length - 1}
                  onStop={onStop}
                />
              ) : (
                <UserRow key={index} item={item} />
              )
            )
          )}

          {/* 加载态 */}
          {loading && !streaming && (
            <div className="msg-row assistant">
              <AiAvatar size={36} />
              <div className="bubble-wrapper">
                <Tag className="domain-tag-sm" color="processing">
                  识别中...
                </Tag>
                <div className="typing-indicator">
                  <span />
                  <span />
                  <span />
                </div>
                <div className="msg-actions">
                  <Tooltip title="停止生成">
                    <Button
                      type="text"
                      size="small"
                      icon={<StopOutlined />}
                      onClick={onStop}
                      className="stop-btn-inner"
                    />
                  </Tooltip>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* ===== 底部输入区 ===== */}
        <div className="input-float-area">
          <div className="input-float-box">
            <Input.TextArea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入你的问题，按 Enter 发送..."
              autoSize={{ minRows: 1, maxRows: 5 }}
              disabled={loading || streaming}
              variant="borderless"
              className="float-textarea"
            />
            <div className="input-float-actions">
              <Button
                type="text"
                size="small"
                icon={<SettingOutlined />}
                className="input-icon-btn"
              />
              <Button
                onClick={onSend}
                disabled={!input.trim() || loading || streaming}
                className="float-send-btn"
                icon={<SendOutlined />}
                type="primary"
                shape="circle"
              />
            </div>
          </div>
          <div className="input-float-hint">
            回答仅供参考，重要信息请以原始文件和正式发布内容为准。
          </div>
        </div>
      </main>
    </div>
  );
}
