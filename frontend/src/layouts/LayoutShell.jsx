import { Button, Dropdown } from "antd";
import { Outlet, NavLink, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { LogoutOutlined, ArrowLeftOutlined } from "@ant-design/icons";
import { apiFetch, setToken } from "../api/client";
import { Brand } from "../components/PublicHeader";
import { LanguageSwitch, useLocale } from "../i18n";
export default function LayoutShell() {
  const navigate = useNavigate();
  const { tr } = useLocale();
  const [username, setUsername] = useState("");
  const [authError, setAuthError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    apiFetch("/auth/me", { signal: controller.signal }).then((data) => { setUsername(data.display_name || data.username); setAuthError(false); })
      .catch((error) => {
        if (error.name === "AbortError") return;
        if (error.status === 401 || error.status === 403) { setToken(""); navigate("/login", { replace: true }); }
        else setAuthError(true);
      });
    return () => controller.abort();
  }, [navigate]);
  const logout = () => { setToken(""); navigate("/login", { replace: true }); };
  return <div className="live-shell"><header className="live-header"><Brand />
    <nav aria-label={tr("业务导航", "Application navigation")}><NavLink to="/chat">{tr("智能问答", "Chat")}</NavLink><NavLink to="/search">{tr("数据检索", "Search")}</NavLink><NavLink to="/dashboard">{tr("系统总览", "Overview")}</NavLink></nav>
    <div className="live-user"><LanguageSwitch /><Dropdown menu={{ items: [{ key: "logout", icon: <LogoutOutlined />, label: tr("退出登录", "Sign out"), onClick: logout }] }}><Button>{username || tr("用户菜单", "User menu")}</Button></Dropdown></div>
  </header><div className="live-mode-bar"><span>{tr("真实业务系统 · 需连接后端与模型服务", "LIVE SYSTEM · Requires backend and model services")}</span><button onClick={() => navigate("/")}><ArrowLeftOutlined />{tr("返回展示首页", "Back to showcase")}</button></div>
    {authError && <div role="alert" className="input-notice">{tr("暂时无法连接后端，请检查服务后刷新。", "The backend is unavailable. Check the service and reload.")}</div>}<Outlet />
  </div>;
}
