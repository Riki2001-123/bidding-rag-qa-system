"""直接测试后端 HTTP 接口返回格式"""
import httpx
import json

BASE = "http://localhost:8000/api"

# 登录
resp = httpx.post(f"{BASE}/auth/login", json={"username": "admin", "password": "admin123"}, timeout=10)
token = resp.json()["access_token"]
print(f"登录成功, token={token[:20]}...")

# 测试几个问题
questions = [
    ("tender", "政府采购招标公告"),
    ("enterprise", "袁江平贸易（合肥经开）商行"),
    ("policy", "政府采购法适用范围"),
]

for domain, q in questions:
    resp = httpx.post(
        f"{BASE}/chat/query",
        json={"question": q, "domain": domain, "top_k": 10},
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    data = resp.json()
    print(f"\n--- domain={domain}, q='{q}' ---")
    print(f"  status_code: {resp.status_code}")
    print(f"  response keys: {list(data.keys())}")
    citations = data.get("citations", [])
    print(f"  citations count: {len(citations)}")
    if citations:
        c = citations[0]
        print(f"  first citation keys: {list(c.keys())}")
        print(f"  first citation title: {c.get('title', '')[:50]}")
