"""
RAG 评测数据集自动生成脚本 V2（聚合模式 + 断点续传）
=====================================================
核心改进：
- 元数据聚合：tender 4字段拼成"标书信息卡"，enterprise 4字段拼成"企业档案卡"
- 跨域组合：enterprise_name 匹配 tender 的 tenderer 生成跨域问答
- 去掉 LLM 打分（省一半费用），用更严格的 prompt + 多层自动规则替代
- 断点续传：每处理完一个 chunk 立即保存

输出：qa_dataset.json（最终数据集）+ qa_report.txt（统计报告）
"""

import json
import time
import random
import re
import hashlib
import logging
import os
import pymysql
import pymysql.cursors
from collections import Counter, defaultdict
from datetime import datetime
from openai import OpenAI

# ============================================================
# 配置
# ============================================================
API_KEY = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", "")
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_GENERATE = "qwen-plus"         # qwen-plus 性价比高，质量足够
MAX_RETRIES = 3
RETRY_DELAY_MIN = 1
RETRY_DELAY_MAX = 3
REQUEST_DELAY = 0.5

OUTPUT_FILE = "qa_dataset.json"
REPORT_FILE = "qa_report.txt"
CHECKPOINT_FILE = "qa_checkpoint.json"

# 数据库配置
DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "xunfei07_rag_db"),
    "charset": "utf8mb4"
}

# 各域采样数量（已有490条，补到1000+）
# 已有: policy=415, enterprise=73, tender=2, cross_domain=0
# 目标: tender~330, enterprise~250, cross_domain~150
# 过滤率约50%, 所以按 x2 采样
SAMPLE_CONFIG = {
    "policy_content": 0,            # 已有415条，不需要补
    "policy_title": 0,              # 不需要补
    "enterprise_aggregated": 120,   # 补~180条 (120*3*50%通过率)
    "tender_aggregated": 250,       # 补~375条 (250*3*50%通过率)
    "cross_domain": 100,            # 补~150条 (100*3*50%通过率)
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

# ============================================================
# 断点续传
# ============================================================
def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("断点续传：已处理 %d 条，累计 %d 条问答对" % (
                len(data.get('done_keys', [])), len(data.get('qa_pairs', []))))
            return data
        except Exception as e:
            logger.warning("断点文件损坏，从头开始: %s" % e)
    return {"done_keys": [], "qa_pairs": []}


def save_checkpoint(checkpoint):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False)


# ============================================================
# 数据加载
# ============================================================
def load_policy_content(limit=200):
    """加载政策 content（长文本）"""
    conn = pymysql.connect(**DB_CONFIG)
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute("""
            SELECT id, domain, record_id, source_field, content 
            FROM text_chunks 
            WHERE domain = 'policy' AND source_field = 'content' AND LENGTH(content) > 200
            ORDER BY RAND() LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    conn.close()
    logger.info("policy_content: 加载 %d 条" % len(rows))
    return rows


def load_policy_title(limit=50):
    """加载政策标题（中等长度）"""
    conn = pymysql.connect(**DB_CONFIG)
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute("""
            SELECT id, domain, record_id, source_field, content 
            FROM text_chunks 
            WHERE domain = 'policy' AND source_field = 'title' AND LENGTH(content) > 40
            ORDER BY RAND() LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    conn.close()
    logger.info("policy_title: 加载 %d 条" % len(rows))
    return rows


def load_enterprise_aggregated(limit=150):
    """按 record_id 聚合企业信息，生成企业档案卡"""
    conn = pymysql.connect(**DB_CONFIG)
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        # 先随机抽取 record_id
        cur.execute("""
            SELECT DISTINCT record_id 
            FROM text_chunks 
            WHERE domain = 'enterprise' AND source_field = 'business_scope' AND LENGTH(content) > 150
            ORDER BY RAND() LIMIT %s
        """, (limit,))
        record_ids = [r['record_id'] for r in cur.fetchall()]
        
        if not record_ids:
            conn.close()
            return []
        
        # 获取这些 record 的所有字段
        placeholders = ','.join(['%s'] * len(record_ids))
        cur.execute("""
            SELECT record_id, source_field, content
            FROM text_chunks 
            WHERE domain = 'enterprise' AND record_id IN (%s)
        """ % placeholders, record_ids)
        all_rows = cur.fetchall()
    conn.close()
    
    # 按 record_id 聚合
    record_map = defaultdict(dict)
    for r in all_rows:
        field = r['source_field']
        # 有些 record 有多条 business_scope，拼起来
        if field in record_map[r['record_id']]:
            record_map[r['record_id']][field] += "；" + r['content']
        else:
            record_map[r['record_id']][field] = r['content']
    
    # 组装成聚合文档
    result = []
    for rid, fields in record_map.items():
        doc = {
            "id": "ent_%d" % rid,
            "domain": "enterprise",
            "record_id": rid,
            "source_field": "aggregated",
            "content": _format_enterprise_card(fields)
        }
        result.append(doc)
    
    logger.info("enterprise_aggregated: 加载 %d 条" % len(result))
    return result


def load_tender_aggregated(limit=150):
    """按 record_id 聚合标书信息，生成标书信息卡"""
    conn = pymysql.connect(**DB_CONFIG)
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        # 随机抽取 record_id
        cur.execute("""
            SELECT DISTINCT record_id 
            FROM text_chunks 
            WHERE domain = 'tender'
            ORDER BY RAND() LIMIT %s
        """, (limit,))
        record_ids = [r['record_id'] for r in cur.fetchall()]
        
        if not record_ids:
            conn.close()
            return []
        
        placeholders = ','.join(['%s'] * len(record_ids))
        cur.execute("""
            SELECT record_id, source_field, content
            FROM text_chunks 
            WHERE domain = 'tender' AND record_id IN (%s)
        """ % placeholders, record_ids)
        all_rows = cur.fetchall()
    conn.close()
    
    record_map = defaultdict(dict)
    for r in all_rows:
        record_map[r['record_id']][r['source_field']] = r['content']
    
    result = []
    for rid, fields in record_map.items():
        doc = {
            "id": "tender_%d" % rid,
            "domain": "tender",
            "record_id": rid,
            "source_field": "aggregated",
            "content": _format_tender_card(fields)
        }
        result.append(doc)
    
    logger.info("tender_aggregated: 加载 %d 条" % len(result))
    return result


def load_cross_domain(limit=50):
    """跨域组合：enterprise_name 匹配 tender 的 tenderer（两步法，避免慢 JOIN）"""
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # 步骤1: 取 tender 的 tenderer 列表（只取前5000个不重复的，足够匹配）
            cur.execute("""
                SELECT DISTINCT content as tenderer_name
                FROM text_chunks 
                WHERE domain = 'tender' AND source_field = 'tenderer'
                    AND LENGTH(content) > 5
                LIMIT 5000
            """)
            tenderer_names = set(r['tenderer_name'] for r in cur.fetchall())
            logger.info("cross_domain: tenderer 去重列表 %d 条" % len(tenderer_names))

            if not tenderer_names:
                return []

            # 步骤2: 查企业表中 enterprise_name 在 tenderer 列表中的
            # 用 LIKE 做模糊匹配（企业名可能包含在 tenderer 中，或反之）
            matched = []
            for name in list(tenderer_names)[:2000]:  # 限制检查数量
                cur.execute("""
                    SELECT e.content as enterprise_name, e.record_id as ent_rid
                    FROM text_chunks e
                    WHERE e.domain = 'enterprise' 
                        AND e.source_field = 'enterprise_name'
                        AND (e.content = %s OR e.content LIKE CONCAT('%%', %s, '%%') 
                             OR %s LIKE CONCAT('%%', e.content, '%%'))
                    LIMIT 1
                """, (name, name, name))
                row = cur.fetchone()
                if row:
                    matched.append(row['enterprise_name'])
                    if len(matched) >= limit:
                        break

            if not matched:
                logger.info("cross_domain: 未找到匹配的企业-标书关联")
                return []

            logger.info("cross_domain: 找到 %d 个企业-标书匹配" % len(matched))

            # 步骤3: 查这些企业的经营范围 + 关联的标书信息
            result = []
            for ent_name in matched:
                cur.execute("""
                    SELECT content FROM text_chunks
                    WHERE domain = 'enterprise' AND source_field = 'business_scope'
                        AND record_id = (
                            SELECT record_id FROM text_chunks
                            WHERE domain = 'enterprise' AND source_field = 'enterprise_name'
                                AND content = %s LIMIT 1
                        )
                    LIMIT 1
                """, (ent_name,))
                scope_row = cur.fetchone()

                cur.execute("""
                    SELECT record_id FROM text_chunks
                    WHERE domain = 'tender' AND source_field = 'tenderer'
                        AND content = %s LIMIT 1
                """, (ent_name,))
                tender_row = cur.fetchone()

                if tender_row and scope_row:
                    tid = tender_row['record_id']
                    cur.execute("""
                        SELECT source_field, content FROM text_chunks
                        WHERE domain = 'tender' AND record_id = %s
                    """, (tid,))
                    tender_fields = {r['source_field']: r['content'] for r in cur.fetchall()}

                    doc = {
                        "id": "cross_%d" % tid,
                        "domain": "cross_domain",
                        "record_id": str(tid),
                        "source_field": "cross_domain",
                        "content": _format_cross_domain_card({
                            "business_scope": scope_row['content'][:500],
                            "tender_title": tender_fields.get("title", ""),
                            "agency": tender_fields.get("agency", ""),
                            "project_name": tender_fields.get("project_name", ""),
                        })
                    }
                    result.append(doc)

                    if len(result) >= limit:
                        break
    finally:
        conn.close()

    logger.info("cross_domain: 最终生成 %d 条" % len(result))
    return result


def _format_enterprise_card(fields):
    """格式化企业档案卡"""
    parts = []
    name = fields.get("enterprise_name", "未知企业")
    industry = fields.get("industry", "")
    region = fields.get("region", "")
    scope = fields.get("business_scope", "")
    
    parts.append("【企业名称】%s" % name)
    if industry:
        parts.append("【所属行业】%s" % industry)
    if region:
        parts.append("【所在地区】%s" % region)
    if scope:
        parts.append("【经营范围】%s" % scope)
    
    return "\n".join(parts)


def _format_tender_card(fields):
    """格式化标书信息卡"""
    parts = []
    title = fields.get("title", "")
    project = fields.get("project_name", "")
    agency = fields.get("agency", "")
    tenderer = fields.get("tenderer", "")
    summary = fields.get("content_summary", "")
    
    if title:
        parts.append("【标书标题】%s" % title)
    if project:
        parts.append("【项目名称】%s" % project)
    if agency:
        parts.append("【代理机构】%s" % agency)
    if tenderer:
        parts.append("【中标方】%s" % tenderer)
    if summary:
        parts.append("【内容摘要】%s" % summary)
    
    return "\n".join(parts)


def _format_cross_domain_card(r):
    """格式化跨域组合卡"""
    parts = []
    parts.append("【企业经营范围】%s" % r.get('business_scope', ''))
    parts.append("【中标项目标题】%s" % r.get('tender_title', ''))
    parts.append("【代理机构】%s" % r.get('agency', ''))
    parts.append("【项目名称】%s" % r.get('project_name', ''))
    return "\n".join(parts)


# ============================================================
# Prompt 模板
# ============================================================

PROMPT_POLICY_CONTENT = """你是一名招投标/政务领域的资深质检专家。请基于以下政策文档片段，生成3个高质量问答对。

【文档信息】
- 所属领域：policy（政策）
- 来源字段：content

【文档内容】
{content}

【生成要求】
1. 生成恰好3个问答对，问题类型必须覆盖以下三类（各1个）：
   - 事实型(factual)：直接从文档中可找到明确答案，如具体数字、日期、名称、条款编号
   - 推理型(reasoning)：需综合文档中2个以上信息点进行推理判断
   - 跨域型(cross_domain)：需关联招投标法规、企业资质要求等外部知识来回答

2. 答案必须严格基于文档内容，禁止编造。答案要具体，包含关键数据、条款编号等。
3. 答案长度：100-400字
4. source_text 必须是原文中真实存在的片段

【输出格式】JSON对象：
{{
  "qa_pairs": [
    {{
      "question": "自然语言提问，模拟真实用户",
      "answer": "详细准确的答案",
      "question_type": "factual/reasoning/cross_domain",
      "source_text": "原文片段",
      "difficulty": "easy/medium/hard"
    }}
  ]
}}"""

PROMPT_POLICY_TITLE = """你是政府采购领域的信息检索专家。以下是某份政策文件的标题信息。请基于此生成2个问答对。

【政策标题】
{content}

【要求】
1. 生成2个问答对：
   - 政策查询型：用户可能搜索此类政策时的自然查询
   - 政策理解型：从标题推断该政策的核心内容、适用范围

2. 答案需标注"根据政策标题信息"，合理推断但不可编造具体条款
3. 答案长度 80-250字

【输出格式】JSON对象：
{{
  "qa_pairs": [
    {{
      "question": "问题",
      "answer": "答案",
      "question_type": "factual/reasoning",
      "source_text": "标题原文",
      "difficulty": "easy/medium"
    }}
  ]
}}"""

PROMPT_ENTERPRISE = """你是一名招投标领域的资质审核专家。以下是某企业的完整档案信息（含名称、行业、地区、经营范围），请基于此生成3个高质量问答对。

【企业档案】
{content}

【生成要求】
1. 生成恰好3个问答对，覆盖以下类型：
   - 事实型(factual)：企业的基本信息是什么？属于什么行业？注册地在哪里？
   - 推理型(reasoning)：根据经营范围，该企业是否具备某类项目（如信息化建设、安防工程、办公用品供应等）的投标资格？理由是什么？
   - 跨域型(cross_domain)：结合招投标法规，该企业的经营范围可能涉及哪些资质要求？

2. 答案必须基于企业档案中的信息，分析要具体
3. 推理型问题必须引用经营范围中的具体业务条目作为依据
4. 答案长度：80-300字

【输出格式】JSON对象：
{{
  "qa_pairs": [
    {{
      "question": "问题",
      "answer": "答案",
      "question_type": "factual/reasoning/cross_domain",
      "source_text": "档案中的原文片段",
      "difficulty": "easy/medium/hard"
    }}
  ]
}}"""

PROMPT_TENDER = """你是一名招投标领域的信息分析专家。以下是某条标书的完整信息（标题、项目名称、代理机构、中标方），请基于此生成3个高质量问答对。

【标书信息】
{content}

【生成要求】
1. 生成恰好3个问答对，覆盖以下类型：
   - 事实型(factual)：该标书的中标方是谁？代理机构是哪家？项目名称是什么？
   - 推理型(reasoning)：根据标书信息，该项目的业务领域是什么？属于什么类型的采购？
   - 跨域型(cross_domain)：结合招投标法规，此类项目通常需要什么资质？该中标方是否可能具备？

2. 答案必须基于标书信息中的字段，分析要有依据
3. 推理型和跨域型答案中需引用具体字段内容作为依据
4. 答案长度：80-300字

【输出格式】JSON对象：
{{
  "qa_pairs": [
    {{
      "question": "问题",
      "answer": "答案",
      "question_type": "factual/reasoning/cross_domain",
      "source_text": "标书信息中的原文片段",
      "difficulty": "easy/medium/hard"
    }}
  ]
}}"""

PROMPT_CROSS_DOMAIN = """你是一名招投标领域的资深分析师。以下是某企业与其中标项目的关联信息，请基于此生成3个跨域问答对。

【关联信息】
{content}

【生成要求】
1. 生成恰好3个问答对，全部为跨域型(cross_domain)或推理型(reasoning)：
   - 资质匹配分析：该企业的经营范围是否与中标项目匹配？为什么？
   - 竞争分析：该企业中标此项目可能具备哪些优势？
   - 合规分析：此类项目通常需要什么资质？从经营范围看是否满足？

2. 答案必须同时引用企业经营范围和标书信息中的具体内容
3. 分析要有逻辑，不能泛泛而谈
4. 答案长度：100-350字

【输出格式】JSON对象：
{{
  "qa_pairs": [
    {{
      "question": "问题",
      "answer": "答案",
      "question_type": "cross_domain/reasoning",
      "source_text": "引用的原文片段",
      "difficulty": "medium/hard"
    }}
  ]
}}"""


# ============================================================
# API 调用封装
# ============================================================
def call_llm(system_prompt, user_prompt, model=MODEL_GENERATE):
    kwargs = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"}
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content.strip()
            return json.loads(content)
        except json.JSONDecodeError:
            if attempt < MAX_RETRIES - 1:
                time.sleep(random.uniform(RETRY_DELAY_MIN, RETRY_DELAY_MAX))
            else:
                match = re.search(r'\{[\s\S]*\}', content)
                if match:
                    try:
                        return json.loads(match.group(0))
                    except:
                        pass
                raise
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                delay = random.uniform(RETRY_DELAY_MIN, RETRY_DELAY_MAX) * (attempt + 1)
                logger.warning("API 失败（第%d次），%.1fs后重试: %s" % (attempt+1, delay, e))
                time.sleep(delay)
            else:
                raise


def select_prompt(chunk):
    """根据 chunk 类型选择 prompt"""
    sf = chunk["source_field"]
    domain = chunk["domain"]

    if domain == "policy" and sf == "content":
        return PROMPT_POLICY_CONTENT.format(content=chunk["content"])
    elif domain == "policy" and sf == "title":
        return PROMPT_POLICY_TITLE.format(content=chunk["content"])
    elif domain == "enterprise":
        return PROMPT_ENTERPRISE.format(content=chunk["content"])
    elif domain == "tender":
        return PROMPT_TENDER.format(content=chunk["content"])
    elif domain == "cross_domain":
        return PROMPT_CROSS_DOMAIN.format(content=chunk["content"])
    else:
        return PROMPT_POLICY_CONTENT.format(content=chunk["content"])


def generate_qa(chunk):
    """对单个 chunk 生成问答对"""
    prompt = select_prompt(chunk)
    system = "你是RAG评测数据集构造助手。必须返回JSON对象，包含qa_pairs数组。每条必须含question、answer、question_type、source_text、difficulty。"
    result = call_llm(system, prompt)

    if isinstance(result, dict) and "qa_pairs" in result:
        return result["qa_pairs"]
    elif isinstance(result, list):
        return result
    else:
        for k, v in (result.items() if isinstance(result, dict) else []):
            if isinstance(v, list):
                return v
        raise ValueError("未知返回格式")


# ============================================================
# 自动质量过滤（多层，替代 LLM 打分）
# ============================================================
def auto_filter(qa, chunk):
    """多层自动规则过滤，返回 (passed, reason)"""
    # 第一层：必填字段
    required = ["question", "answer", "question_type", "source_text", "difficulty"]
    for field in required:
        if field not in qa or not qa[field]:
            return False, "缺少字段: %s" % field

    # 第二层：长度检查（更严格）
    if len(qa["question"]) < 8:
        return False, "问题太短: %d字" % len(qa["question"])
    if len(qa["question"]) > 100:
        return False, "问题太长: %d字" % len(qa["question"])
    if len(qa["answer"]) < 30:
        return False, "答案太短: %d字" % len(qa["answer"])
    if len(qa["answer"]) > 500:
        return False, "答案过长: %d字" % len(qa["answer"])

    # 第三层：类型校验
    valid_types = {"factual", "reasoning", "cross_domain"}
    if qa["question_type"] not in valid_types:
        return False, "无效类型: %s" % qa["question_type"]

    valid_diff = {"easy", "medium", "hard"}
    if qa["difficulty"] not in valid_diff:
        return False, "无效难度: %s" % qa["difficulty"]

    # 第四层：source_text 存在性验证
    source = qa["source_text"].strip()
    chunk_content = chunk["content"]
    if source and len(source) >= 6:
        source_clean = re.sub(r'[，。、；：！？""''（）\s\n\r]', '', source)
        content_clean = re.sub(r'[，。、；：！？""''（）\s\n\r]', '', chunk_content)
        if source_clean:
            # 至少匹配 source 中连续 10 个字符
            found = False
            for i in range(0, max(1, len(source_clean) - 10), 3):
                substr = source_clean[i:i+10]
                if substr and substr in content_clean:
                    found = True
                    break
            if not found:
                return False, "source_text 引用验证失败"

    # 第五层：答案不能与问题高度重复
    q_clean = re.sub(r'\s', '', qa["question"])
    a_clean = re.sub(r'\s', '', qa["answer"])
    if q_clean == a_clean:
        return False, "答案与问题相同"
    # 答案前30字不能和问题完全一样
    if len(a_clean) > 30 and a_clean[:30] == q_clean[:30]:
        return False, "答案开头重复问题"

    # 第六层：问题多样性（不能以"是什么"开头超过一定比例的简单判断已在去重阶段处理）

    return True, "通过"


# ============================================================
# 语义去重
# ============================================================
def text_fingerprint(text):
    clean = re.sub(r'\s+', '', text.lower())
    return hashlib.md5(clean.encode()).hexdigest()


def is_duplicate(qa, seen_questions):
    q_clean = re.sub(r'\s+', '', qa["question"])
    q_len = len(q_clean)
    if q_len == 0:
        return True

    fp = text_fingerprint(qa["question"])
    if fp in seen_questions:
        return True

    for seen_q in seen_questions:
        seen_clean = re.sub(r'\s+', '', seen_q)
        if abs(len(seen_clean) - q_len) / max(len(seen_clean), q_len, 1) > 0.3:
            continue
        shorter, longer = (q_clean, seen_clean) if q_len <= len(seen_clean) else (seen_clean, q_clean)
        matches = sum(1 for i in range(len(longer) - len(shorter) + 1)
                      if longer[i:i+len(shorter)] == shorter)
        if matches > 0:
            return True

    seen_questions.add(fp)
    return False


# ============================================================
# 主流程
# ============================================================
def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("RAG 评测数据集生成 V2（聚合模式）- 开始")
    logger.info("=" * 60)

    # 1. 断点续传
    checkpoint = load_checkpoint()
    done_keys = set(checkpoint["done_keys"])
    existing_qa = checkpoint.get("qa_pairs", [])

    # 加载已有问答对（如果是从V1生成的 qa_dataset.json 合并）
    old_qa_file = "qa_dataset_old.json"
    if os.path.exists(OUTPUT_FILE) and len(existing_qa) == 0:
        # 首次运行V2，把V1的结果导入
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                old_qa = json.load(f)
            logger.info("加载已有问答对: %d 条 (来自 %s)" % (len(old_qa), OUTPUT_FILE))
            # 备份
            with open(old_qa_file, "w", encoding="utf-8") as f:
                json.dump(old_qa, f, ensure_ascii=False, indent=2)
            existing_qa.extend(old_qa)
            logger.info("累计: %d 条" % len(existing_qa))
        except Exception as e:
            logger.warning("加载已有数据失败: %s，从头开始" % e)

    # 2. 加载数据（分类加载）
    logger.info("开始从数据库加载数据...")
    all_chunks = []

    if SAMPLE_CONFIG["policy_content"] > 0 and "policy_content_loaded" not in done_keys:
        chunks = load_policy_content(SAMPLE_CONFIG["policy_content"])
        done_keys.add("policy_content_loaded")
        all_chunks.extend(chunks)
    else:
        logger.info("policy_content: 跳过（不需要补或已加载）")

    if SAMPLE_CONFIG["policy_title"] > 0 and "policy_title_loaded" not in done_keys:
        chunks = load_policy_title(SAMPLE_CONFIG["policy_title"])
        done_keys.add("policy_title_loaded")
        all_chunks.extend(chunks)
    else:
        logger.info("policy_title: 跳过（不需要补或已加载）")

    if SAMPLE_CONFIG["enterprise_aggregated"] > 0 and "enterprise_loaded" not in done_keys:
        chunks = load_enterprise_aggregated(SAMPLE_CONFIG["enterprise_aggregated"])
        done_keys.add("enterprise_loaded")
        all_chunks.extend(chunks)
    else:
        logger.info("enterprise_aggregated: 跳过（已加载）")

    if SAMPLE_CONFIG["tender_aggregated"] > 0 and "tender_loaded" not in done_keys:
        chunks = load_tender_aggregated(SAMPLE_CONFIG["tender_aggregated"])
        done_keys.add("tender_loaded")
        all_chunks.extend(chunks)
    else:
        logger.info("tender_aggregated: 跳过（已加载）")

    if SAMPLE_CONFIG["cross_domain"] > 0 and "cross_domain_loaded" not in done_keys:
        chunks = load_cross_domain(SAMPLE_CONFIG["cross_domain"])
        done_keys.add("cross_domain_loaded")
        all_chunks.extend(chunks)
    else:
        logger.info("cross_domain: 跳过（已加载）")

    # 过滤已处理的 chunk
    chunks_to_process = [c for c in all_chunks if c["id"] not in done_keys]
    logger.info("待处理: %d 条，已有问答对: %d 条" % (len(chunks_to_process), len(existing_qa)))

    # 统计
    domains = Counter(c["domain"] for c in all_chunks)
    logger.info("全部数据域分布: %s" % dict(domains))

    # 3. 生成问答对
    gen_stats = {"success": 0, "failed": 0, "by_domain": Counter()}

    for i, chunk in enumerate(chunks_to_process):
        try:
            qa_list = generate_qa(chunk)
            for qa in qa_list:
                qa["_chunk_id"] = chunk["id"]
                qa["_domain"] = chunk["domain"]
                qa["_source_field"] = chunk["source_field"]

                # 自动过滤
                passed, reason = auto_filter(qa, chunk)
                if passed:
                    existing_qa.append(qa)

            gen_stats["success"] += 1
            gen_stats["by_domain"][chunk["domain"]] += 1
            logger.info("[%d/%d] %s#%s 成功，累计 %d 条" % (
                i+1, len(chunks_to_process), chunk["domain"], chunk["id"], len(existing_qa)))

            time.sleep(REQUEST_DELAY)
        except Exception as e:
            gen_stats["failed"] += 1
            logger.warning("[%d/%d] %s#%s 失败: %s" % (i+1, len(chunks_to_process), chunk["domain"], chunk["id"], e))

        # 每个 chunk 保存断点
        done_keys.add(chunk["id"])
        save_checkpoint({
            "done_keys": list(done_keys),
            "qa_pairs": existing_qa
        })

    logger.info("生成完成: 成功 %d, 失败 %d, 问答对 %d 条" % (
        gen_stats["success"], gen_stats["failed"], len(existing_qa)))

    # 4. 语义去重
    logger.info("开始语义去重...")
    seen_questions = set()
    final_qa = []
    for qa in existing_qa:
        if not is_duplicate(qa, seen_questions):
            final_qa.append(qa)
    logger.info("去重: %d -> %d" % (len(existing_qa), len(final_qa)))

    # 5. 清理内部字段
    for qa in final_qa:
        qa["chunk_id"] = qa.pop("_chunk_id")
        qa["domain"] = qa.pop("_domain")
        qa["source_field"] = qa.pop("_source_field")

    # 6. 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_qa, f, ensure_ascii=False, indent=2)
    logger.info("数据集已保存: %s" % OUTPUT_FILE)

    # 7. 统计报告
    elapsed = (datetime.now() - start_time).total_seconds()
    type_dist = Counter(q["question_type"] for q in final_qa)
    domain_dist = Counter(q["domain"] for q in final_qa)
    diff_dist = Counter(q["difficulty"] for q in final_qa)
    q_lens = [len(q["question"]) for q in final_qa]
    a_lens = [len(q["answer"]) for q in final_qa]

    report = """RAG 评测数据集生成报告 V2（聚合模式）
============================================================
生成时间: %s
耗时: %.1f 秒 (%.1f 分钟)

采样配置
------------------------------------------------------------
policy_content: %d 条（政策正文，3条/chunk）
policy_title: %d 条（政策标题，2条/chunk）
enterprise_aggregated: %d 条（企业聚合档案，3条/chunk）
tender_aggregated: %d 条（标书聚合信息卡，3条/chunk）
cross_domain: %d 条（跨域组合，3条/chunk）

生成统计
------------------------------------------------------------
生成成功: %d / %d 失败
最终问答对: %d 条

数据分布
------------------------------------------------------------
域分布: %s
问题类型: %s
难度分布: %s

问题长度: 最短 %d 字, 最长 %d 字, 平均 %d 字
答案长度: 最短 %d 字, 最长 %d 字, 平均 %d 字

配置
------------------------------------------------------------
生成模型: %s
过滤: 多层自动规则（无LLM打分，节省50%%费用）
""" % (
        start_time.strftime('%Y-%m-%d %H:%M:%S'), elapsed, elapsed/60,
        SAMPLE_CONFIG["policy_content"], SAMPLE_CONFIG["policy_title"],
        SAMPLE_CONFIG["enterprise_aggregated"], SAMPLE_CONFIG["tender_aggregated"],
        SAMPLE_CONFIG["cross_domain"],
        gen_stats["success"], gen_stats["failed"], len(final_qa),
        dict(domain_dist), dict(type_dist), dict(diff_dist),
        min(q_lens) if q_lens else 0, max(q_lens) if q_lens else 0,
        sum(q_lens)//len(q_lens) if q_lens else 0,
        min(a_lens) if a_lens else 0, max(a_lens) if a_lens else 0,
        sum(a_lens)//len(a_lens) if a_lens else 0,
        MODEL_GENERATE
    )

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("统计报告已保存: %s" % REPORT_FILE)

    # 8. 清理断点
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        logger.info("断点文件已清理")

    print("\n" + "=" * 60)
    print("生成完成！共 %d 条问答对" % len(final_qa))
    print("耗时: %.1f 分钟" % (elapsed/60))
    print("域分布: %s" % dict(domain_dist))
    print("问题类型: %s" % dict(type_dist))
    print("保存至: %s" % OUTPUT_FILE)
    print("=" * 60)


if __name__ == "__main__":
    main()
