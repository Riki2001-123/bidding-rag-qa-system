"""
SQL 型评测问答对生成脚本 V2
=============================
V2 优化：全部 JOIN 消除，改用「单表查询 + Python 字典拼装」
- 预加载企业/标书/政策数据到内存（一次性，走索引）
- 后续所有问答对生成纯 Python 操作，零 SQL 开销
- 预计耗时 < 30 秒

输出：qa_dataset_sql.json（可与 qa_dataset.json 合并）
"""

import json
import time
import random
import re
import logging
import os
import hashlib
from collections import Counter, defaultdict
from datetime import datetime
from typing import List, Dict, Any, Tuple

import pymysql
import pymysql.cursors
from openai import OpenAI

# ============================================================
# 配置
# ============================================================
API_KEY = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", "")
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_GENERATE = "qwen-turbo"
MAX_RETRIES = 3
REQUEST_DELAY = 0.3

OUTPUT_FILE = "qa_dataset_sql.json"
REPORT_FILE = "qa_report_sql.txt"

TARGET_CONFIG = {
    "enterprise": 250,
    "tender": 330,
    "cross_domain": 150,
    "policy_sql": 100,
}

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "xunfei07_rag_db"),
    "charset": "utf8mb4"
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


# ============================================================
# 数据库工具
# ============================================================
def query_db(sql: str, params=None) -> List[Dict]:
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()


def query_one(sql: str, params=None) -> Dict:
    rows = query_db(sql, params)
    return rows[0] if rows else {}


# ============================================================
# 数据预加载（一次性，全程零 JOIN）
# ============================================================
def preload_data():
    """预加载所有需要的数据到 Python 字典，后续纯内存操作"""
    logger.info("正在预加载数据...")

    # --- Enterprise 域 ---
    logger.info("  加载企业名称...")
    ent_names = {}  # record_id -> enterprise_name
    for r in query_db("SELECT record_id, content FROM text_chunks WHERE domain='enterprise' AND source_field='enterprise_name'"):
        ent_names[r["record_id"]] = r["content"]
    logger.info("    企业名称: %d 条" % len(ent_names))

    logger.info("  加载企业行业...")
    ent_industries = defaultdict(list)  # record_id -> [industry]
    for r in query_db("SELECT record_id, content FROM text_chunks WHERE domain='enterprise' AND source_field='industry'"):
        if r["content"]:
            ent_industries[r["record_id"]].append(r["content"])
    logger.info("    企业行业: %d 条" % sum(len(v) for v in ent_industries.values()))

    logger.info("  加载企业经营范围...")
    ent_scopes = {}  # record_id -> business_scope
    for r in query_db("SELECT record_id, content FROM text_chunks WHERE domain='enterprise' AND source_field='business_scope'"):
        ent_scopes[r["record_id"]] = r["content"]
    logger.info("    企业经营范围: %d 条" % len(ent_scopes))

    # 拼装企业完整数据: {record_id: {name, industry, scope}}
    enterprises = {}
    for rid in ent_names:
        industries = ent_industries.get(rid, [])
        scope = ent_scopes.get(rid, "")
        enterprises[rid] = {
            "record_id": rid,
            "name": ent_names[rid],
            "industry": industries[0] if industries else "未知",
            "scope": scope,
        }
    logger.info("    完整企业记录: %d 条" % len(enterprises))

    # --- Tender 域 ---
    logger.info("  加载标书标题...")
    tender_titles = {}  # record_id -> title
    for r in query_db("SELECT record_id, content FROM text_chunks WHERE domain='tender' AND source_field='title'"):
        tender_titles[r["record_id"]] = r["content"]

    logger.info("  加载标书项目名称...")
    tender_projects = {}  # record_id -> project_name
    for r in query_db("SELECT record_id, content FROM text_chunks WHERE domain='tender' AND source_field='project_name'"):
        tender_projects[r["record_id"]] = r["content"]

    logger.info("  加载标书中标方...")
    tender_tenderers = defaultdict(list)  # record_id -> [tenderer]
    for r in query_db("SELECT record_id, content FROM text_chunks WHERE domain='tender' AND source_field='tenderer'"):
        if r["content"]:
            tender_tenderers[r["record_id"]].append(r["content"])

    logger.info("  加载标书代理机构...")
    tender_agencies = defaultdict(list)  # record_id -> [agency]
    for r in query_db("SELECT record_id, content FROM text_chunks WHERE domain='tender' AND source_field='agency'"):
        if r["content"]:
            tender_agencies[r["record_id"]].append(r["content"])

    # 拼装标书完整数据
    tenders = {}
    for rid in tender_titles:
        tenders[rid] = {
            "record_id": rid,
            "title": tender_titles[rid],
            "project_name": tender_projects.get(rid, "未知"),
            "tenderers": tender_tenderers.get(rid, []),
            "agencies": tender_agencies.get(rid, []),
        }
    logger.info("    完整标书记录: %d 条" % len(tenders))

    # --- Policy 域 ---
    logger.info("  加载政策标题...")
    policy_titles = {}
    for r in query_db("SELECT record_id, content FROM text_chunks WHERE domain='policy' AND source_field='title'"):
        if r["content"] and len(r["content"]) > 5:
            policy_titles[r["record_id"]] = r["content"]
    logger.info("    政策文件: %d 条" % len(policy_titles))

    logger.info("数据预加载完成！\n")
    return enterprises, tenders, policy_titles


# ============================================================
# 问答对生成（纯 Python，零 SQL）
# ============================================================

def gen_enterprise_count_by_industry(enterprises, limit=25):
    """按行业统计企业数量"""
    # 从预载数据统计
    ind_counter = Counter()
    for ent in enterprises.values():
        if ent["industry"] != "未知":
            ind_counter[ent["industry"]] += 1
    top_industries = ind_counter.most_common(limit)

    qa_list = []
    for ind_name, cnt in top_industries:
        qa_list.append({
            "question": f"目前系统中收录了多少家{ind_name}行业的企业？",
            "answer": f"系统中共收录了{cnt}家{ind_name}行业的企业。",
            "question_type": "factual",
            "difficulty": "easy",
            "_domain": "enterprise",
            "_source_field": "industry",
            "_template": "enterprise_count_by_industry",
        })
    return qa_list


def gen_enterprise_scope_match(enterprises, limit_per_kw=5):
    """按关键词搜索经营范围匹配的企业"""
    keywords = ["信息技术", "软件开发", "系统集成", "安防", "办公", "建筑材料",
                "教育", "医疗", "环保", "电子", "咨询", "技术服务",
                "网络", "通信", "设备", "施工", "市政", "园林绿化"]

    qa_list = []
    for kw in keywords:
        matched = []
        for ent in enterprises.values():
            if kw in ent["scope"]:
                matched.append(ent["name"])
                if len(matched) >= limit_per_kw:
                    break
        if matched:
            qa_list.append({
                "question": f'哪些企业的经营范围包含"{kw}"相关业务？',
                "answer": f'经营范围包含"{kw}"的企业有：' + "；".join(matched),
                "question_type": "reasoning",
                "difficulty": "medium",
                "_domain": "enterprise",
                "_source_field": "business_scope",
                "_template": "enterprise_scope_match",
            })
    return qa_list


def gen_enterprise_detail(enterprises, limit=80):
    """随机选取企业，生成详细信息问答"""
    ent_list = list(enterprises.values())
    # 过滤掉没有经营范围的
    ent_list = [e for e in ent_list if e["scope"] and e["scope"] != ""]
    random.shuffle(ent_list)

    qa_list = []
    for ent in ent_list[:limit]:
        qa_list.append({
            "question": f'请介绍一下"{ent["name"]}"这家企业的基本信息。',
            "answer": f"企业名称：{ent['name']}\n所属行业：{ent['industry']}\n经营范围：{ent['scope']}",
            "question_type": "factual",
            "difficulty": "easy",
            "_domain": "enterprise",
            "_source_field": "aggregated",
            "_template": "enterprise_detail",
        })
    return qa_list


def gen_enterprise_industry_stats(enterprises):
    """行业分布统计"""
    ind_counter = Counter()
    for ent in enterprises.values():
        if ent["industry"] != "未知":
            ind_counter[ent["industry"]] += 1
    top = ind_counter.most_common(15)

    lines = "\n".join(f"- {name}：{cnt}家" for name, cnt in top)
    return [{
        "question": "目前收录的企业按行业分布如何？各有多少家？",
        "answer": f"企业行业分布如下：\n{lines}",
        "question_type": "factual",
        "difficulty": "medium",
        "_domain": "enterprise",
        "_source_field": "industry",
        "_template": "enterprise_industry_stats",
    }]


def gen_enterprise_tender_eligibility(enterprises, limit=100):
    """企业投标资格分析"""
    project_types = [
        ("信息化建设项目", ["软件开发", "系统集成", "信息技术"]),
        ("安防监控工程", ["安防", "监控", "智能化"]),
        ("办公用品采购", ["办公用品", "文具", "办公设备"]),
        ("建筑装饰工程", ["建筑", "装饰", "装修"]),
        ("医疗设备采购", ["医疗", "医疗器械"]),
        ("环保工程", ["环保", "环境治理", "污水处理"]),
        ("市政工程", ["市政", "道路", "桥梁"]),
        ("通信工程", ["通信", "网络", "光缆"]),
        ("水利工程", ["水利", "河道", "排水"]),
        ("消防工程", ["消防", "火灾报警", "灭火"]),
    ]

    ent_list = [e for e in enterprises.values() if len(e.get("scope", "")) > 100]
    random.shuffle(ent_list)

    qa_list = []
    for ent in ent_list[:limit]:
        proj_type, proj_kw = random.choice(project_types)
        matched = any(kw in ent["scope"] for kw in proj_kw)
        analysis = (
            f'该企业经营范围中包含与{proj_kw[0]}相关的业务条目，具备参与此类项目投标的基本条件。'
            if matched else
            f'该企业经营范围中未明确提及与{proj_kw[0]}直接相关的业务条目，建议进一步核实资质证书和过往业绩。'
        )
        qa_list.append({
            "question": f'根据"{ent["name"]}"的经营范围，该企业是否适合参与{proj_type}类项目的投标？',
            "answer": f"企业：{ent['name']}\n经营范围：{ent['scope'][:300]}\n\n分析：针对{proj_type}类项目，{analysis}",
            "question_type": "reasoning",
            "difficulty": "hard",
            "_domain": "enterprise",
            "_source_field": "business_scope",
            "_template": "enterprise_tender_eligibility",
        })
    return qa_list


def gen_enterprise_same_industry(enterprises, limit=30):
    """同行业企业列举"""
    ind_counter = Counter()
    for ent in enterprises.values():
        if ent["industry"] != "未知":
            ind_counter[ent["industry"]] += 1
    top_industries = [ind for ind, cnt in ind_counter.most_common(limit) if cnt >= 3]

    qa_list = []
    for ind_name in top_industries:
        sample = [e["name"] for e in enterprises.values()
                  if e["industry"] == ind_name][:8]
        qa_list.append({
            "question": f"列举几家{ind_name}行业的企业。",
            "answer": f"{ind_name}行业的企业有：" + "、".join(sample),
            "question_type": "factual",
            "difficulty": "easy",
            "_domain": "enterprise",
            "_source_field": "industry",
            "_template": "enterprise_same_industry_sample",
        })
    return qa_list


def gen_tender_by_agency(tenders, limit=60):
    """按代理机构查标书"""
    # 统计每个代理机构出现的次数
    agency_tenders = defaultdict(list)
    for t in tenders.values():
        for ag in t["agencies"]:
            agency_tenders[ag].append(t)

    # 取出现次数最多的代理机构
    top_agencies = sorted(agency_tenders.keys(), key=lambda a: len(agency_tenders[a]), reverse=True)[:limit]

    qa_list = []
    for ag_name in top_agencies:
        items = agency_tenders[ag_name][:5]
        details = "；".join(
            f"《{t['title']}》（中标方：{t['tenderers'][0] if t['tenderers'] else '未知'}）"
            for t in items
        )
        qa_list.append({
            "question": f"{ag_name}代理过哪些项目？中标方分别是谁？",
            "answer": f"{ag_name}代理的项目有：{details}",
            "question_type": "factual",
            "difficulty": "easy",
            "_domain": "tender",
            "_source_field": "agency",
            "_template": "tender_by_agency",
        })
    return qa_list


def gen_tender_winner_count(tenders, limit=40):
    """按中标方统计中标次数"""
    tenderer_count = Counter()
    for t in tenders.values():
        for td in t["tenderers"]:
            tenderer_count[td] += 1
    top = tenderer_count.most_common(limit)

    qa_list = []
    for name, cnt in top:
        qa_list.append({
            "question": f"{name}中标了几个项目？",
            "answer": f"{name}共中标了{cnt}个项目。",
            "question_type": "factual",
            "difficulty": "easy",
            "_domain": "tender",
            "_source_field": "tenderer",
            "_template": "tender_winner_count",
        })
    return qa_list


def gen_tender_project_list(tenders, limit=30):
    """标书列表 - 多种问题变体避免去重"""
    tlist = list(tenders.values())
    random.shuffle(tlist)

    qa_list = []
    question_variants = [
        "请列出最近的一些标书信息，包括项目名称和中标方。",
        "系统中有哪些标书记录？",
        "请展示一些标书的基本信息。",
        "列出部分标书的中标方和代理机构信息。",
    ]
    used_hashes = set()
    for variant in question_variants:
        for i in range(min(limit // 4, len(tlist))):
            t = tlist[(variant.__hash__() + i) % len(tlist)]
            td = t["tenderers"][0] if t["tenderers"] else "未知"
            ag = t["agencies"][0] if t["agencies"] else "未知"
            h = hash(t["record_id"])
            if h in used_hashes:
                continue
            used_hashes.add(h)
            qa_list.append({
                "question": variant,
                "answer": f"- 《{t['title']}》\n  项目名称：{t['project_name']}  中标方：{td}  代理机构：{ag}",
                "question_type": "factual",
                "difficulty": "medium",
                "_domain": "tender",
                "_source_field": "title",
                "_template": "tender_project_list",
            })
    return qa_list


def gen_tender_by_project_type(tenders, limit=30):
    """按项目类型关键词搜索标书"""
    project_kw = ["信息化", "采购", "工程", "服务", "设备", "软件", "建设",
                  "维护", "运维", "装修", "绿化", "消防", "安防", "网络", "医疗"]
    qa_list = []
    all_tenders = list(tenders.values())
    for kw in project_kw:
        matched = []
        for t in all_tenders:
            if kw in t["title"] or kw in t["project_name"]:
                td = t["tenderers"][0] if t["tenderers"] else "未知"
                matched.append(f"《{t['title']}》（中标方：{td}）")
                if len(matched) >= 5:
                    break
        if matched:
            qa_list.append({
                "question": f'系统中有哪些与"{kw}"相关的标书项目？',
                "answer": f'与"{kw}"相关的标书项目有：\n' + "\n".join(f"- {m}" for m in matched),
                "question_type": "reasoning",
                "difficulty": "medium",
                "_domain": "tender",
                "_source_field": "title",
                "_template": "tender_by_project_type",
            })
    return qa_list


def gen_tender_detail(tenders, limit=60):
    """单条标书详情问答"""
    tlist = list(tenders.values())
    random.shuffle(tlist)

    qa_list = []
    question_variants = [
        lambda t: f'请介绍标书"{t["title"]}"的基本信息。',
        lambda t: f'标书"{t["title"]}"的中标方和代理机构分别是谁？',
        lambda t: f'"{t["title"]}"这个项目的具体情况是什么？',
    ]
    for t in tlist[:limit]:
        if not t["tenderers"]:
            continue
        variant = random.choice(question_variants)
        td = t["tenderers"][0]
        ag = t["agencies"][0] if t["agencies"] else "未知"
        qa_list.append({
            "question": variant(t),
            "answer": f"标书名称：{t['title']}\n项目名称：{t['project_name']}\n中标方：{td}\n代理机构：{ag}",
            "question_type": "factual",
            "difficulty": "easy",
            "_domain": "tender",
            "_source_field": "title",
            "_template": "tender_detail",
        })
    return qa_list


def gen_tender_winner_projects(tenders, limit=40):
    """中标方的项目列表"""
    tenderer_projects = defaultdict(list)
    for t in tenders.values():
        for td in t["tenderers"]:
            tenderer_projects[td].append(t)

    top = sorted(tenderer_projects.keys(), key=lambda n: len(tenderer_projects[n]), reverse=True)[:limit]

    qa_list = []
    for name in top:
        items = tenderer_projects[name][:5]
        details = "；".join(
            f"《{t['title']}》（代理机构：{t['agencies'][0] if t['agencies'] else '未知'}）"
            for t in items
        )
        qa_list.append({
            "question": f"{name}中标了哪些项目？分别是什么类型的？",
            "answer": f"{name}中标的项目有：{details}",
            "question_type": "factual",
            "difficulty": "medium",
            "_domain": "tender",
            "_source_field": "tenderer",
            "_template": "tender_winner_projects",
        })
    return qa_list


def gen_tender_agency_ranking(tenders):
    """代理机构排名"""
    agency_count = Counter()
    for t in tenders.values():
        for ag in t["agencies"]:
            agency_count[ag] += 1
    top = agency_count.most_common(15)

    lines = "\n".join(f"{i+1}. {name}：{cnt}个项目" for i, (name, cnt) in enumerate(top))
    return [{
        "question": "哪些代理机构代理的项目数量最多？请列出排名。",
        "answer": f"代理机构项目数量排名：\n{lines}",
        "question_type": "factual",
        "difficulty": "medium",
        "_domain": "tender",
        "_source_field": "agency",
        "_template": "tender_agency_ranking",
    }]


def gen_tender_winner_ranking(tenders):
    """中标方排名"""
    tenderer_count = Counter()
    for t in tenders.values():
        for td in t["tenderers"]:
            tenderer_count[td] += 1
    top = tenderer_count.most_common(15)

    lines = "\n".join(f"{i+1}. {name}：中标{cnt}次" for i, (name, cnt) in enumerate(top))
    return [{
        "question": "哪些企业中标次数最多？请列出排名。",
        "answer": f"中标次数排名：\n{lines}",
        "question_type": "factual",
        "difficulty": "medium",
        "_domain": "tender",
        "_source_field": "tenderer",
        "_template": "tender_winner_ranking",
    }]


def gen_cross_domain(enterprises, tenders, limit=100):
    """跨域：企业-标书关联"""
    # 构建 中标方 -> 标书列表 索引
    tenderer_tenders = defaultdict(list)
    for t in tenders.values():
        for td in t["tenderers"]:
            tenderer_tenders[td].append(t)

    # 找既是企业名又是中标方的
    ent_names = set(e["name"] for e in enterprises.values())
    matched = [(name, enterprises_by_name(enterprises, name))
               for name in ent_names if name in tenderer_tenders]

    random.shuffle(matched)

    qa_list = []
    for ent_name, ent in matched[:limit]:
        t_list = tenderer_tenders[ent_name][:3]
        if not t_list:
            continue

        # 模板1: 企业-项目匹配
        proj = t_list[0]
        qa_list.append({
            "question": f'企业"{ent_name}"中标了项目"{proj["title"]}"，该企业的经营范围与这个项目匹配吗？',
            "answer": f"项目：{proj['title']}\n企业经营范围：{ent['scope'][:300]}\n\n需结合企业经营范围与项目需求进行综合评估。建议查看该企业是否有相关资质证书和类似项目经验。",
            "question_type": "cross_domain",
            "difficulty": "hard",
            "_domain": "cross_domain",
            "_source_field": "cross_domain",
            "_template": "cross_ent_tender_match",
        })

        # 模板2: 企业竞标竞争力
        titles = [t["title"] for t in t_list[:5]]
        qa_list.append({
            "question": f'根据系统中收录的数据，企业"{ent_name}"参与竞标的竞争力如何？中标了哪些项目？',
            "answer": (
                f"中标记录：共中标{len(t_list)}个项目\n"
                f"中标项目：" + "；".join(f"《{t}》" for t in titles) + "\n"
                f"经营范围：{ent['scope'][:200]}\n\n"
                f"综合评估：该企业有一定中标记录，具备相关领域经验。具体竞争力需结合项目预算、资质要求等综合判断。"
            ),
            "question_type": "cross_domain",
            "difficulty": "hard",
            "_domain": "cross_domain",
            "_source_field": "cross_domain",
            "_template": "cross_winner_competition",
        })
    return qa_list


def enterprises_by_name(enterprises, name):
    """按名称查找企业"""
    for e in enterprises.values():
        if e["name"] == name:
            return e
    return {"name": name, "industry": "未知", "scope": "未知"}


def gen_policy_sql(policy_titles):
    """政策域统计型问答"""
    total = len(policy_titles)
    sample = list(policy_titles.values())[:15]
    lines = "\n".join(f"- {t}" for t in sample)

    qa_list = [
        {
            "question": "目前系统收录了多少条政策文件？",
            "answer": f"系统共收录了{total}条政策文件。",
            "question_type": "factual",
            "difficulty": "easy",
            "_domain": "policy",
            "_source_field": "title",
            "_template": "policy_count",
        },
        {
            "question": "系统中有哪些政策文件？请列举一些。",
            "answer": f"系统收录的政策文件包括：\n{lines}",
            "question_type": "factual",
            "difficulty": "easy",
            "_domain": "policy",
            "_source_field": "title",
            "_template": "policy_recent",
        },
    ]

    # 按关键词搜索政策文件
    policy_kw = ["采购", "招标", "投标", "工程", "信息化", "安全", "环保", "医疗",
                 "教育", "科技", "财政", "税收", "中小企业", "创新", "数字化"]
    all_titles = list(policy_titles.values())
    for kw in policy_kw:
        matched = [t for t in all_titles if kw in t][:10]
        if matched:
            qa_list.append({
                "question": f'系统中有哪些与"{kw}"相关的政策文件？',
                "answer": f'与"{kw}"相关的政策文件有：\n' + "\n".join(f"- {t}" for t in matched),
                "question_type": "factual",
                "difficulty": "easy",
                "_domain": "policy",
                "_source_field": "title",
                "_template": "policy_keyword_search",
            })

    return qa_list


# ============================================================
# 质量过滤 & 去重
# ============================================================

def auto_filter(qa: dict) -> Tuple[bool, str]:
    if len(qa.get("question", "")) < 6:
        return False, "问题太短"
    if len(qa.get("answer", "")) < 10:
        return False, "答案太短"
    if len(qa.get("answer", "")) > 800:
        return False, "答案过长"
    if "未找到" in qa.get("answer", "") and "暂无" in qa.get("answer", ""):
        return False, "答案为空结果"
    clean_a = re.sub(r'[，。；：、！？\s\n\r\-—()（）《》\u201c\u201d\u2018\u2019\d]', '', qa["answer"])
    if len(clean_a) < 5:
        return False, "答案实质内容太少"
    return True, "通过"


def text_fingerprint(text: str) -> str:
    clean = re.sub(r'\s+', '', text.lower())
    return hashlib.md5(clean.encode()).hexdigest()


def deduplicate(qa_list: List[Dict]) -> List[Dict]:
    seen = set()
    result = []
    for qa in qa_list:
        fp = text_fingerprint(qa["question"])
        if fp not in seen:
            seen.add(fp)
            result.append(qa)
    return result


# ============================================================
# LLM 润色（可选）
# ============================================================

def polish_answer(qa: dict) -> dict:
    try:
        resp = client.chat.completions.create(
            model=MODEL_GENERATE,
            temperature=0.3,
            messages=[
                {"role": "system", "content": "你是招投标领域的助手。请将以下结构化答案润色成更流畅的自然语言，保留所有数据和信息，不要添加新信息。直接输出润色后的文本，不要加任何前缀。"},
                {"role": "user", "content": qa["answer"]}
            ],
            max_tokens=500,
        )
        polished = resp.choices[0].message.content.strip()
        if polished and len(polished) > 20:
            qa["answer"] = polished
    except Exception as e:
        logger.warning("  润色失败: %s" % e)
    return qa


# ============================================================
# 主流程
# ============================================================

def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("SQL 型评测问答对生成 V2 - 开始")
    logger.info("=" * 60)

    # 0. 预加载数据（一次性 SQL）
    enterprises, tenders, policy_titles = preload_data()
    preload_time = datetime.now()
    logger.info("数据预加载耗时: %.1f 秒\n" % (preload_time - start_time).total_seconds())

    all_qa = []

    # 1. Enterprise 域
    logger.info("--- Enterprise 域 ---")
    gen_time = datetime.now()
    ent_qa = []
    ent_qa.extend(gen_enterprise_count_by_industry(enterprises))
    logger.info("  enterprise_count_by_industry: %d 条" % len(ent_qa))
    ent_qa.extend(gen_enterprise_scope_match(enterprises))
    logger.info("  enterprise_scope_match: %d 条" % len(ent_qa))
    ent_qa.extend(gen_enterprise_detail(enterprises, limit=100))
    logger.info("  enterprise_detail: %d 条" % len(ent_qa))
    ent_qa.extend(gen_enterprise_industry_stats(enterprises))
    logger.info("  enterprise_industry_stats: %d 条" % len(ent_qa))
    ent_qa.extend(gen_enterprise_tender_eligibility(enterprises, limit=50))
    logger.info("  enterprise_tender_eligibility: %d 条" % len(ent_qa))
    ent_qa.extend(gen_enterprise_same_industry(enterprises))
    logger.info("  enterprise_same_industry_sample: %d 条" % len(ent_qa))
    ent_qa = deduplicate(ent_qa)
    all_qa.extend(ent_qa)
    logger.info("  Enterprise 合计: %d 条 (%.1fs)" % (len(ent_qa), (datetime.now() - gen_time).total_seconds()))

    # 2. Tender 域
    logger.info("\n--- Tender 域 ---")
    gen_time = datetime.now()
    tender_qa = []
    tender_qa.extend(gen_tender_by_agency(tenders))
    logger.info("  tender_by_agency: %d 条" % len(tender_qa))
    tender_qa.extend(gen_tender_winner_count(tenders))
    logger.info("  tender_winner_count: %d 条" % len(tender_qa))
    tender_qa.extend(gen_tender_project_list(tenders, limit=40))
    logger.info("  tender_project_list: %d 条" % len(tender_qa))
    tender_qa.extend(gen_tender_by_project_type(tenders))
    logger.info("  tender_by_project_type: %d 条" % len(tender_qa))
    tender_qa.extend(gen_tender_detail(tenders, limit=80))
    logger.info("  tender_detail: %d 条" % len(tender_qa))
    tender_qa.extend(gen_tender_winner_projects(tenders))
    logger.info("  tender_winner_projects: %d 条" % len(tender_qa))
    tender_qa.extend(gen_tender_agency_ranking(tenders))
    logger.info("  tender_agency_ranking: %d 条" % len(tender_qa))
    tender_qa.extend(gen_tender_winner_ranking(tenders))
    logger.info("  tender_winner_ranking: %d 条" % len(tender_qa))
    tender_qa = deduplicate(tender_qa)
    all_qa.extend(tender_qa)
    logger.info("  Tender 合计: %d 条 (%.1fs)" % (len(tender_qa), (datetime.now() - gen_time).total_seconds()))

    # 3. Cross Domain
    logger.info("\n--- Cross Domain ---")
    gen_time = datetime.now()
    cross_qa = gen_cross_domain(enterprises, tenders, limit=80)
    cross_qa = deduplicate(cross_qa)
    all_qa.extend(cross_qa)
    logger.info("  Cross Domain 合计: %d 条 (%.1fs)" % (len(cross_qa), (datetime.now() - gen_time).total_seconds()))

    # 4. Policy SQL
    logger.info("\n--- Policy SQL ---")
    policy_qa = gen_policy_sql(policy_titles)
    all_qa.extend(policy_qa)
    logger.info("  Policy SQL 合计: %d 条" % len(policy_qa))

    # 5. 质量过滤
    logger.info("\n--- 质量过滤 ---")
    before = len(all_qa)
    all_qa = [qa for qa in all_qa if auto_filter(qa)[0]]
    logger.info("  过滤: %d -> %d 条" % (before, len(all_qa)))

    # 6. LLM 润色（默认关闭）
    POLISH = False
    if POLISH:
        logger.info("\n--- LLM 润色 ---")
        for i, qa in enumerate(all_qa):
            polish_answer(qa)
            if (i + 1) % 20 == 0:
                logger.info("  润色进度: %d/%d" % (i + 1, len(all_qa)))
            time.sleep(REQUEST_DELAY)
    else:
        logger.info("\n--- LLM 润色: 跳过（POLISH=False）---")

    # 7. 最终去重
    all_qa = deduplicate(all_qa)

    # 8. 清理内部字段
    for qa in all_qa:
        qa["chunk_id"] = qa.pop("_template", "")
        qa["domain"] = qa.pop("_domain", "")
        qa["source_field"] = qa.pop("_source_field", "")

    # 9. 保存
    output_path = os.path.join(os.path.dirname(__file__) or ".", OUTPUT_FILE)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_qa, f, ensure_ascii=False, indent=2)
    logger.info("数据集已保存: %s" % output_path)

    # 10. 统计报告
    elapsed = (datetime.now() - start_time).total_seconds()
    type_dist = Counter(q.get("question_type", "") for q in all_qa)
    domain_dist = Counter(q.get("domain", "") for q in all_qa)
    diff_dist = Counter(q.get("difficulty", "") for q in all_qa)
    tpl_dist = Counter(q.get("chunk_id", "") for q in all_qa)

    report = """SQL 型评测问答对生成报告 (V2 - 零JOIN优化版)
=============================================
生成时间: %s
总耗时: %.1f 秒

数据分布
---------------------------------------------
域分布: %s
问题类型: %s
难度分布: %s
模板分布: %s

总计: %d 条问答对
- Enterprise: %d
- Tender: %d
- Cross Domain: %d
- Policy SQL: %d

LLM 润色: %s
""" % (
        start_time.strftime('%Y-%m-%d %H:%M:%S'), elapsed,
        dict(domain_dist), dict(type_dist), dict(diff_dist), dict(tpl_dist),
        len(all_qa),
        len([q for q in all_qa if q.get("domain") == "enterprise"]),
        len([q for q in all_qa if q.get("domain") == "tender"]),
        len([q for q in all_qa if q.get("domain") == "cross_domain"]),
        len([q for q in all_qa if q.get("domain") == "policy"]),
        "是 (qwen-turbo)" if POLISH else "否 (纯模板)",
    )

    report_path = os.path.join(os.path.dirname(__file__) or ".", REPORT_FILE)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("报告已保存: %s" % report_path)

    print("\n" + "=" * 60)
    print("生成完成！共 %d 条问答对" % len(all_qa))
    print("耗时: %.1f 秒" % elapsed)
    print("域分布: %s" % dict(domain_dist))
    print("问题类型: %s" % dict(type_dist))
    print("保存至: %s" % output_path)
    print("=" * 60)


def merge_datasets():
    """合并语义型(qa_dataset.json)和SQL型(qa_dataset_sql.json)数据集"""
    base_dir = os.path.dirname(__file__) or "."
    base_path = os.path.join(base_dir, "qa_dataset.json")
    sql_path = os.path.join(base_dir, "qa_dataset_sql.json")
    merged_path = os.path.join(base_dir, "qa_dataset_merged.json")

    base_data = []
    sql_data = []

    if os.path.exists(base_path):
        with open(base_path, "r", encoding="utf-8") as f:
            base_data = json.load(f)
        print(f"加载语义型数据集: {len(base_data)} 条")

    if os.path.exists(sql_path):
        with open(sql_path, "r", encoding="utf-8") as f:
            sql_data = json.load(f)
        print(f"加载SQL型数据集: {len(sql_data)} 条")

    merged = base_data + sql_data
    print(f"合并前总计: {len(merged)} 条")

    # 统一字段格式
    for qa in merged:
        if "domain" not in qa and "_domain" in qa:
            qa["domain"] = qa.pop("_domain")
        if "source_field" not in qa and "_source_field" in qa:
            qa["source_field"] = qa.pop("_source_field")
        if "chunk_id" not in qa and "_template" in qa:
            qa["chunk_id"] = qa.pop("_template")

    # 去重（按问题指纹）
    merged = deduplicate(merged)
    print(f"去重后: {len(merged)} 条")

    with open(merged_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"合并数据集已保存: {merged_path}")

    # 统计
    domain_dist = Counter(q.get("domain", "unknown") for q in merged)
    type_dist = Counter(q.get("question_type", "unknown") for q in merged)
    diff_dist = Counter(q.get("difficulty", "unknown") for q in merged)
    print(f"\n域分布: {dict(domain_dist)}")
    print(f"问题类型: {dict(type_dist)}")
    print(f"难度分布: {dict(diff_dist)}")

    return merged


if __name__ == "__main__":
    main()
    print("\n\n" + "=" * 60)
    print("开始合并数据集...")
    print("=" * 60)
    merge_datasets()
