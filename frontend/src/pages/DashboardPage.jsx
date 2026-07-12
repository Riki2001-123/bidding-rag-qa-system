import { Card, Col, Row, Statistic, Tag, Typography } from "antd";
import {
  ApartmentOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";

const cards = [
  { title: "政策业务", desc: "政策法规、适用范围、生效时间", tag: "policy", icon: <FileSearchOutlined /> },
  { title: "招标业务", desc: "项目公告、中标信息、采购内容", tag: "tender", icon: <ApartmentOutlined /> },
  { title: "企业业务", desc: "企业画像、行业地区、经营范围", tag: "enterprise", icon: <SafetyCertificateOutlined /> }
];

export default function DashboardPage() {
  return (
    <div className="page-shell">
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>系统总览</Typography.Title>
          <Typography.Text type="secondary">MySQL 主数据驱动的招投标采购知识问答</Typography.Text>
        </div>
        <Tag color="processing" icon={<DatabaseOutlined />}>xunfei07_rag_db</Tag>
      </div>

      <Row gutter={[16, 16]} className="metric-row">
        <Col xs={24} md={8}>
          <Card className="metric-card">
            <Statistic title="数据源" value="MySQL" prefix={<DatabaseOutlined />} />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card className="metric-card">
            <Statistic title="召回链路" value="BM25 + FAISS" />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card className="metric-card">
            <Statistic title="业务域" value={3} suffix="个" />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} className="domain-grid">
        {cards.map((item) => (
          <Col xs={24} md={8} key={item.title}>
            <Card className="domain-card">
              <div className="domain-card-icon">{item.icon}</div>
              <Typography.Title level={4}>{item.title}</Typography.Title>
              <Typography.Text type="secondary">{item.desc}</Typography.Text>
              <div className="domain-card-footer">
                <Tag>{item.tag}</Tag>
              </div>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  );
}
