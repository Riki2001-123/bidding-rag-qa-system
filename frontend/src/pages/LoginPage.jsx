import { Button, Card, Form, Input, Typography, message } from "antd";
import { useNavigate } from "react-router-dom";
import { apiFetch, setToken } from "../api/client";

export default function LoginPage() {
  const navigate = useNavigate();

  const onFinish = async (values) => {
    try {
      const data = await apiFetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values)
      });
      setToken(data.access_token);
      message.success("登录成功");
      navigate("/");
    } catch (error) {
      message.error(String(error));
    }
  };

  return (
    <div className="login-page">
      <Card className="login-card">
        <div style={{ textAlign: "center", marginBottom: 20 }}>
          <div className="login-logo">
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" width={36} height={36}>
              <defs>
                <linearGradient id="loginGrad" x1="4" y1="4" x2="20" y2="20" gradientUnits="userSpaceOnUse">
                  <stop stopColor="#2563eb" />
                  <stop offset="1" stopColor="#7c3aed" />
                </linearGradient>
              </defs>
              <circle cx="12" cy="12" r="10" stroke="url(#loginGrad)" strokeWidth="1.5" fill="none" />
              <circle cx="12" cy="12" r="3" fill="url(#loginGrad)" />
              <path d="M12 2a10 10 0 0110 10" stroke="url(#loginGrad)" strokeWidth="1.5" strokeLinecap="round" fill="none" />
            </svg>
          </div>
        </div>
        <Typography.Title level={3} style={{ textAlign: "center", marginBottom: 4, color: "#111827", fontWeight: 600 }}>
          招投标采购智能问答
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: "center", marginBottom: 28, color: "#6b7280", fontSize: 14 }}>
          招标、政策、企业信息一站式查询
        </Typography.Paragraph>
        <Form layout="vertical" onFinish={onFinish}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input placeholder="请输入用户名" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password placeholder="请输入密码" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block style={{ height: 44, borderRadius: 10, fontWeight: 500 }}>
            登录
          </Button>
        </Form>
        <Typography.Paragraph style={{ textAlign: "center", fontSize: 12, color: "#9ca3af", marginTop: 20 }}>
          默认账号：admin / internal / supplier
        </Typography.Paragraph>
      </Card>
    </div>
  );
}
