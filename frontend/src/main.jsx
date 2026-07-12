import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          // === 浅色配色体系 ===
          colorPrimary: "#2563eb",
          colorBgContainer: "#ffffff",
          colorBgElevated: "#ffffff",
          colorBgLayout: "#fafafa",
          colorBorder: "#e5e7eb",
          colorBorderSecondary: "#f3f4f6",
          borderRadius: 10,
          // === 文字层级 ===
          colorText: "#111827",
          colorTextSecondary: "#6b7280",
          colorTextTertiary: "#9ca3af",
          colorTextQuaternary: "#d1d5db",
          // === Geist 字体 ===
          fontFamily:
            "'Geist Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
          fontSize: 14,
          lineHeight: 1.7,
        },
        components: {
          Input: {
            colorBgContainer: "#ffffff",
            activeBorderColor: "#2563eb",
            hoverBorderColor: "#93c5fd",
            colorBorder: "#e5e7eb",
          },
          Button: {
            borderRadius: 10,
            primaryShadow: "0 1px 3px rgba(37,99,235,0.3)",
          },
          Tag: {
            borderRadiusSM: 6,
          },
          List: {
            colorBgContainer: "transparent",
          },
          Empty: {
            colorText: "#9ca3af",
          },
          Dropdown: {
            colorBgElevated: "#ffffff",
          },
          Modal: {
            colorBgElevated: "#ffffff",
          },
          Message: {
            colorBgElevated: "#ffffff",
          },
        },
      }}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>
);
