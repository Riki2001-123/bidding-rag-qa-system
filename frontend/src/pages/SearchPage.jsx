import { Button, Card, Input, Space, Table, Tag, Typography, message } from "antd";
import { DatabaseOutlined, SearchOutlined } from "@ant-design/icons";
import { useMemo, useState } from "react";
import { apiFetch } from "../api/client";

const DOMAIN_LABELS = {
  all: "全部",
  tender: "招标",
  policy: "政策",
  enterprise: "企业",
};

const DOMAIN_COLORS = {
  tender: "gold",
  policy: "blue",
  enterprise: "green",
};

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(10);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [activeFilter, setActiveFilter] = useState("all");

  const counts = useMemo(() => {
    const stats = { all: items.length, tender: 0, policy: 0, enterprise: 0 };
    items.forEach((item) => {
      if (stats[item.domain] !== undefined) {
        stats[item.domain] += 1;
      }
    });
    return stats;
  }, [items]);

  const filteredItems = useMemo(() => {
    if (activeFilter === "all") {
      return items;
    }
    return items.filter((item) => item.domain === activeFilter);
  }, [activeFilter, items]);

  const columns = useMemo(
    () => [
      {
        title: "标题",
        dataIndex: "title",
        key: "title",
        width: 320,
        render: (value, record) => (
          <div>
            <Typography.Text strong>{value}</Typography.Text>
            <div className="table-subtitle">
              <Tag color={DOMAIN_COLORS[record.domain] || "default"} bordered={false}>
                {DOMAIN_LABELS[record.domain] || record.domain}
              </Tag>
              <span>记录 #{record.record_id}</span>
            </div>
          </div>
        ),
      },
      {
        title: "摘要",
        dataIndex: "summary",
        key: "summary",
        ellipsis: true,
      },
      {
        title: "评分",
        dataIndex: "score",
        key: "score",
        width: 96,
        render: (value) => <Tag color="blue">{Number(value).toFixed(3)}</Tag>,
      },
      {
        title: "关键字段",
        dataIndex: "key_fields",
        key: "key_fields",
        width: 360,
        render: (value) => (
          <Space size={[4, 4]} wrap>
            {Object.entries(value || {})
              .filter(([, item]) => item !== "" && item !== null && item !== undefined)
              .slice(0, 6)
              .map(([key, item]) => (
                <Tag key={key} className="field-tag">{`${key}: ${item}`}</Tag>
              ))}
          </Space>
        ),
      },
    ],
    []
  );

  const onSearch = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (query.trim()) {
        params.set("q", query.trim());
      }
      params.set("top_k", String(topK));
      const data = await apiFetch(`/search/all?${params.toString()}`);
      setItems(data.items || []);
      setActiveFilter("all");
    } catch (error) {
      message.error(`搜索失败：${String(error)}`);
    } finally {
      setLoading(false);
    }
  };

  const filterOptions = ["all", "tender", "policy", "enterprise"];

  return (
    <div className="page-shell">
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>统一智能检索</Typography.Title>
          <Typography.Text type="secondary">
            直接输入关键词，系统会同时检索招标、政策、企业三类数据，并按领域分组展示结果。
          </Typography.Text>
        </div>
        <Tag color="processing" icon={<DatabaseOutlined />}>
          MySQL + BM25 + FAISS
        </Tag>
      </div>

      <Card className="search-panel">
        <Space className="search-controls" size={10} wrap>
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onPressEnter={onSearch}
            placeholder="输入企业名称、项目名称、法规关键词等，系统会自动跨领域搜索"
            className="search-input search-input-wide"
            allowClear
          />
          <Button.Group className="topk-group">
            {[5, 10, 20, 50].map((value) => (
              <Button
                key={value}
                type={topK === value ? "primary" : "default"}
                onClick={() => setTopK(value)}
              >
                每域 Top {value}
              </Button>
            ))}
          </Button.Group>
          <Button type="primary" icon={<SearchOutlined />} onClick={onSearch} loading={loading}>
            统一搜索
          </Button>
        </Space>
        <Typography.Text type="secondary" className="search-helper">
          搜索会覆盖三个领域；下方的筛选标签仅影响展示，不会重新发起“选域搜索”。
        </Typography.Text>
      </Card>

      <div className="search-filter-bar">
        {filterOptions.map((filterKey) => (
          <button
            key={filterKey}
            type="button"
            className={`search-filter-chip ${activeFilter === filterKey ? "active" : ""}`}
            onClick={() => setActiveFilter(filterKey)}
          >
            <span>{DOMAIN_LABELS[filterKey]}</span>
            <span className="search-filter-count">{counts[filterKey] || 0}</span>
          </button>
        ))}
      </div>

      <Table
        rowKey={(record) => `${record.domain}-${record.record_id}`}
        columns={columns}
        dataSource={filteredItems}
        loading={loading}
        className="result-table"
        pagination={{ pageSize: 10, showSizeChanger: false }}
        locale={{
          emptyText: "暂无结果。你可以尝试换个关键词，系统会自动在三个领域继续搜索。",
        }}
      />
    </div>
  );
}
