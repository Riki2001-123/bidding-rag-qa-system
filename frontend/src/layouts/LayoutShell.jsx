import { Avatar, Button, Dropdown, Layout, Typography, message } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import {
  AppstoreOutlined,
  CommentOutlined,
  LogoutOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { apiFetch, getToken, setToken } from "../api/client";

const { Header } = Layout;

export default function LayoutShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");

  useEffect(() => {
    const token = getToken();
    if (!token) {
      return;
    }

    apiFetch("/auth/me")
      .then((data) => setUsername(data.display_name || data.username || ""))
      .catch(() => {
        setToken("");
        navigate("/login", { replace: true });
      });
  }, [navigate]);

  const handleLogout = () => {
    setToken("");
    message.success("已退出登录。");
    navigate("/login", { replace: true });
  };

  const navItems = [
    { path: "/chat", label: "智能问答", icon: <CommentOutlined /> },
    { path: "/search", label: "数据检索", icon: <SearchOutlined /> },
    { path: "/dashboard", label: "系统总览", icon: <AppstoreOutlined /> },
  ];

  const activeKey =
    navItems.find(
      (item) =>
        location.pathname === item.path ||
        (location.pathname === "/" && item.path === "/chat")
    )?.path || "/chat";

  const userMenuItems = {
    items: [
      {
        key: "logout",
        icon: <LogoutOutlined />,
        label: "退出登录",
        onClick: handleLogout,
      },
    ],
  };

  // 用户首字母
  const initial = (username || "U").charAt(0).toUpperCase();

  return (
    <Layout style={{ minHeight: "100vh", background: "#fafafa" }}>
      <Header
        className="app-header"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 32px",
          height: 60,
          background: "#ffffff",
          borderBottom: "1px solid #f3f4f6",
          position: "sticky",
          top: 0,
          zIndex: 100,
          lineHeight: "60px",
        }}
      >
        <div className="header-left">
          <div className="header-logo" />
          <Typography.Text strong className="header-brand">
            招投标智能问答
          </Typography.Text>
          <Typography.Text className="header-badge">
            RAG + LLM
          </Typography.Text>
        </div>

        <div className="nav-tabs">
          {navItems.map((item) => (
            <Button
              key={item.path}
              type="text"
              icon={item.icon}
              className={`nav-tab ${activeKey === item.path ? "active" : ""}`}
              onClick={() => navigate(item.path)}
            >
              {item.label}
            </Button>
          ))}
        </div>

        <Dropdown menu={userMenuItems} placement="bottomRight">
          <div className="header-user">
            <div className="header-avatar-text">{initial}</div>
            <span className="header-username">{username}</span>
          </div>
        </Dropdown>
      </Header>

      <Layout style={{ background: "transparent" }}>
        <Outlet />
      </Layout>
    </Layout>
  );
}
