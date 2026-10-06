import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert, Button, Form, Input } from "antd";
import { ArrowRightOutlined } from "@ant-design/icons";
import PublicHeader from "../components/PublicHeader";
import { apiFetch, setToken } from "../api/client";
import { useLocale } from "../i18n";
export default function LoginPage() {
  const navigate = useNavigate(); const { tr } = useLocale();
  const [loading, setLoading] = useState(false); const [error, setError] = useState("");
  async function submit(values) {
    setLoading(true); setError("");
    try {
      const data = await apiFetch("/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
      if (!data.access_token) throw new Error("No access token");
      setToken(data.access_token); navigate("/chat");
    } catch (e) { setError(e.status === 401 ? tr("用户名或密码不正确。", "Incorrect username or password.") : tr("登录失败。请确认后端可用及账号信息正确。", "Sign-in failed. Check backend availability and your credentials.")); }
    finally { setLoading(false); }
  }
  return <div className="login-view"><PublicHeader /><main className="login-layout"><div className="login-story"><div className="eyebrow">CONNECTED TO YOUR BUSINESS</div><h1>{tr("进入你的", "Step into your")}<br /><span>{tr("知识工作台。", "knowledge workspace.")}</span></h1><p>{tr("这是连接真实业务数据与模型服务的系统入口。只想看看产品？免登录演示随时可以体验。", "This workspace connects to real business data and model services. Just exploring? Try the sample demo without signing in.")}</p><Link className="text-link" to="/demo">{tr("先体验公开样例", "Explore public samples first")}<ArrowRightOutlined /></Link></div>
    <section className="signin-card"><span className="eyebrow">LIVE WORKSPACE</span><h2>{tr("登录真实系统", "Sign in to the live system")}</h2><p className="muted">{tr("需要运行中的后端和已配置的账号。", "Requires a running backend and a configured account.")}</p>{error && <Alert type="error" showIcon message={error} />}
      <Form layout="vertical" onFinish={submit}><Form.Item name="username" label={tr("用户名", "Username")} rules={[{ required: true, message: tr("请输入用户名", "Enter your username") }]}><Input size="large" autoComplete="username" /></Form.Item><Form.Item name="password" label={tr("密码", "Password")} rules={[{ required: true, message: tr("请输入密码", "Enter your password") }]}><Input.Password size="large" autoComplete="current-password" /></Form.Item><Button htmlType="submit" type="primary" size="large" block loading={loading}>{tr("进入工作台", "Open workspace")}<ArrowRightOutlined /></Button></Form>
    </section></main></div>;
}
