"""
RAG 生成阶段大模型对比评测脚本
================================
功能：
1. 从 qa_dataset_merged.json 抽样 100 条评测数据
2. 通过后端 API 获取检索上下文（context）
3. 对每个问题，用 5 个模型分别生成回答
4. 用 qwen-max 做 LLM-as-Judge 评分
5. 计算 BERTScore + ROUGE-L
6. 输出对比报告 + JSON 结果

用法：
  python scripts/evaluate_multi_model.py
  python scripts/evaluate_multi_model.py --sample 50
  python scripts/evaluate_multi_model.py --skip-generate  # 跳过生成，直接评分（需已有 results）

前置条件：
  - 后端服务运行中（http://localhost:8000）
  - pip install bert_score rouge_score
"""

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx
from collections import Counter

# ============================================================
# 可选依赖
# ============================================================
_BERTSCORE_AVAILABLE = False
try:
    from bert_score import score as bert_score_fn
    _BERTSCORE_AVAILABLE = True
except ImportError:
    print("[WARN] bert_score 未安装，将跳过 BERTScore 评估")

_ROUGE_AVAILABLE = False
try:
    import rouge_score
    from rouge_score.rouge_scorer import RougeScorer
    _ROUGE_AVAILABLE = True
except ImportError:
    print("[WARN] rouge_score 未安装，将跳过 ROUGE-L 评估")


# ============================================================
# 模型配置（5 个待评测模型）
# ============================================================
MODELS = {
    "qwen-turbo": {
        "api_key": os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-turbo",
    },
    "qwen-plus": {
        "api_key": os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "qwen-max": {
        "api_key": os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
    },
    "deepseek-v3": {
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "glm-4-plus": {
        "api_key": os.getenv("ZHIPU_API_KEY", ""),
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
    },
}

# LLM-as-Judge 使用 qwen-max
JUDGE_MODEL = MODELS["qwen-max"]

# ============================================================
# 后端 API 配置
# ============================================================
BACKEND_BASE_URL = os.getenv("EVAL_BASE_URL", "http://localhost:8000/api")
LOGIN_ENDPOINT = f"{BACKEND_BASE_URL}/auth/login"
EVAL_RETRIEVE_ENDPOINT = f"{BACKEND_BASE_URL}/eval/retrieve"
EVAL_USERNAME = os.getenv("EVAL_USERNAME", "admin")
EVAL_PASSWORD = os.getenv("EVAL_PASSWORD", "admin123")

# ============================================================
# 评测抽样配置
# ============================================================
# 按评测方案：政策120 + 企业75 + 招标105 = 300
# policy走混合检索（向量+BM25），enterprise/tender走纯SQL
SAMPLE_CONFIG = {
    "policy": 120,
    "tender": 105,
    "enterprise": 75,
}

# ============================================================
# Prompt 模板
# ============================================================
GENERATION_PROMPT = """你是一个招投标领域的专业问答助手。请根据提供的检索证据，准确回答用户的问题。

要求：
1. 回答必须基于下方证据，不得编造信息
2. 引用证据时标注来源编号，如 [证据1]
3. 如果证据不足，明确说明无法确认
4. 回答简洁专业，避免冗余

证据：
{evidence}

问题：{question}

请回答："""

JUDGE_PROMPT = """你是一个严谨的评测专家。请评估AI回答的质量。

## 问题
{question}

## 参考答案（ground truth）
{ground_truth}

## AI回答
{prediction}

## 评分维度（每项 1-10 分）

1. **准确性（Accuracy）**：回答是否正确回答了问题，关键事实是否与参考答案一致
2. **无幻觉（Faithfulness）**：回答是否完全基于证据，有无编造信息（幻觉越少分越高）
3. **引用准确率（Citation Accuracy）**：引用的来源是否真实存在且相关
4. **完整度（Completeness）**：回答是否涵盖了问题的主要方面

## 输出格式
请严格按以下 JSON 格式输出，不要输出其他内容：
{{"accuracy": <1-10>, "faithfulness": <1-10>, "citation_accuracy": <1-10>, "completeness": <1-10>, "reason": "<简要评价>"}}"""


# ============================================================
# 工具函数
# ============================================================
def login() -> str:
    """登录获取 JWT token"""
    resp = httpx.post(LOGIN_ENDPOINT, json={"username": EVAL_USERNAME, "password": EVAL_PASSWORD}, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


# 全局 token，支持自动刷新
_current_token: str = ""


def query_backend(question: str, domain: Optional[str], top_k: int = 5) -> dict:
    """调评测专用检索接口（/api/eval/retrieve），返回 citations"""
    global _current_token
    headers = {"Authorization": f"Bearer {_current_token}"}
    payload = {"question": question, "domain": domain, "top_k": top_k}
    resp = httpx.post(EVAL_RETRIEVE_ENDPOINT, json=payload, headers=headers, timeout=60)
    if resp.status_code == 401:
        print("  [TOKEN] 过期，重新登录...")
        _current_token = login()
        headers = {"Authorization": f"Bearer {_current_token}"}
        resp = httpx.post(EVAL_RETRIEVE_ENDPOINT, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()


def build_evidence_text(contexts: List[dict]) -> str:
    """将检索上下文构建为证据文本"""
    if not contexts:
        return "（无检索结果）"
    lines = []
    for i, ctx in enumerate(contexts, 1):
        title = ctx.get("title", "")
        summary = ctx.get("summary", "")
        key_fields = ctx.get("key_fields", {})
        line = f"[证据{i}] 标题：{title}"
        if summary:
            line += f"\n摘要：{summary}"
        if key_fields:
            # 输出关键结构化字段
            field_str = " | ".join(f"{k}: {v}" for k, v in key_fields.items() if v)
            if field_str and field_str != summary:
                line += f"\n详情：{field_str}"
        lines.append(line)
    return "\n\n".join(lines)


def should_use_source_text(item: dict) -> bool:
    """Use dataset evidence directly for deictic enterprise questions."""
    question = item.get("question", "")
    if item.get("domain") != "enterprise" or not item.get("source_text"):
        return False
    markers = ("这家公司", "这家企业", "该公司", "该企业", "给定的信息", "给定信息")
    return any(marker in question for marker in markers)


def call_llm(api_key: str, base_url: str, model: str, prompt: str, temperature: float = 0.1, timeout: int = 60) -> str:
    """调用 OpenAI 兼容 API"""
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": 1024,
    }
    client = httpx.Client(http2=False, timeout=timeout)
    try:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    finally:
        client.close()


def call_llm_with_retry(api_key: str, base_url: str, model: str, prompt: str,
                        max_retries: int = 2, temperature: float = 0.1) -> str:
    """带重试的 LLM 调用"""
    for attempt in range(max_retries + 1):
        try:
            return call_llm(api_key, base_url, model, prompt, temperature)
        except Exception as e:
            if attempt < max_retries:
                wait = 3 * (attempt + 1)
                print(f"    [RETRY] {model} 第 {attempt+1} 次失败，{wait}s 后重试: {str(e)[:80]}")
                time.sleep(wait)
            else:
                print(f"    [ERROR] {model} 最终失败: {str(e)[:80]}")
                raise


def parse_judge_response(response: str) -> Optional[dict]:
    """解析 Judge 的 JSON 响应，将 1-10 分转换为 0-100% 百分比"""
    # 尝试提取 JSON
    text = response.strip()
    # 去掉可能的 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
        # 校验字段
        required = ["accuracy", "faithfulness", "citation_accuracy", "completeness"]
        for key in required:
            if key not in result:
                return None
            # 1-10 分转百分比（保留1位小数）
            result[key] = round(float(result[key]) * 10, 1)
        if "reason" not in result:
            result["reason"] = ""
        return result
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


# ============================================================
# 抽样逻辑
# ============================================================
def sample_dataset(all_data: List[dict], config: Dict[str, int]) -> List[dict]:
    """按域和难度分层抽样"""
    random.seed(42)

    # 按 domain 分组
    by_domain: Dict[str, List[dict]] = {}
    for item in all_data:
        d = item.get("domain", "unknown")
        if d not in by_domain:
            by_domain[d] = []
        by_domain[d].append(item)

    sampled = []
    for domain, count in config.items():
        pool = by_domain.get(domain, [])
        if len(pool) < count:
            print(f"[WARN] 域 '{domain}' 只有 {len(pool)} 条，不足目标 {count} 条，全部选用")
            sampled.extend(pool)
        else:
            sampled.extend(random.sample(pool, count))

    random.shuffle(sampled)
    return sampled


def build_sample_config(total: int) -> Dict[str, int]:
    """Scale the default domain mix down for quick smoke evaluations."""
    if total >= sum(SAMPLE_CONFIG.values()):
        return dict(SAMPLE_CONFIG)
    total = max(int(total), 1)
    weights = {"policy": 120, "tender": 105, "enterprise": 75}
    scaled = {domain: max(1, round(total * count / sum(weights.values()))) for domain, count in weights.items()}
    while sum(scaled.values()) > total:
        domain = max(scaled, key=scaled.get)
        if scaled[domain] > 1:
            scaled[domain] -= 1
        else:
            break
    while sum(scaled.values()) < total:
        domain = max(weights, key=weights.get)
        scaled[domain] += 1
    return scaled


def configured_models() -> Dict[str, dict]:
    configured = {name: cfg for name, cfg in MODELS.items() if cfg.get("api_key")}
    missing = [name for name, cfg in MODELS.items() if not cfg.get("api_key")]
    if missing:
        print(f"[WARN] 跳过未配置 API Key 的模型: {', '.join(missing)}")
    if not configured:
        raise RuntimeError("没有可用模型 API Key，请设置 DASHSCOPE_API_KEY/OPENAI_API_KEY、DEEPSEEK_API_KEY 或 ZHIPU_API_KEY")
    return configured


def eval_item_id(item: dict) -> str:
    raw = "|".join([
        str(item.get("domain", "")),
        str(item.get("question", "")),
        str(item.get("answer", item.get("ground_truth", ""))),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_run_id(eval_data: List[dict], sample_config: Dict[str, int]) -> str:
    payload = {
        "sample_config": sample_config,
        "models": sorted(MODELS.keys()),
        "items": [eval_item_id(item) for item in eval_data],
    }
    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    return f"sample{len(eval_data)}_{digest}"


def resolve_output_dir(base_output: str, run_id: str) -> str:
    return os.path.join(base_output, run_id)


# ============================================================
# 阶段 1：生成回答
# ============================================================
def generate_answers(eval_data: List[dict], output_dir: str, fresh: bool = False) -> dict:
    """对每条数据，调后端获取上下文，然后用每个模型生成回答（支持断点续跑）"""
    model_names = list(MODELS.keys())
    results = {}

    # 断点续跑：加载已有结果
    save_path = os.path.join(output_dir, "generation_results.json")
    if os.path.exists(save_path) and not fresh:
        with open(save_path, "r", encoding="utf-8") as f:
            results = json.load(f)
        print(f"  [续跑] 加载已有 {len(results)} 条结果")

    total = len(eval_data)
    print(f"\n{'='*60}")
    print(f"阶段 1：生成回答 | 共 {total} 条 x {len(model_names)} 个模型 = {total * len(model_names)} 次调用")
    print(f"{'='*60}")

    for i, item in enumerate(eval_data):
        qid = f"q_{i:03d}_{eval_item_id(item)}"
        # 跳过已完成的题
        if qid in results:
            print(f"[{i+1}/{total}] 跳过已完成: {item['question'][:60]}...")
            continue
        question = item["question"]
        ground_truth = item.get("answer", item.get("ground_truth", ""))
        domain = item.get("domain", None)
        question_type = item.get("question_type", "unknown")
        difficulty = item.get("difficulty", "medium")

        print(f"\n[{i+1}/{total}] {question[:60]}...")

        # 1) 调后端评测检索接口获取上下文
        try:
            if should_use_source_text(item):
                backend_resp = {"retrieval_mode": "dataset_source_text", "context_count": 1}
                contexts = [{
                    "title": item.get("source_file_name") or "评测数据原文",
                    "summary": item["source_text"],
                    "key_fields": {},
                }]
            else:
                backend_resp = query_backend(question, domain)
                contexts = []
                answer_text = backend_resp.get("answer_text") or ""
                if answer_text:
                    contexts.append({
                        "title": "SQL 精确查询结果",
                        "summary": answer_text,
                        "key_fields": {},
                    })
                # 新接口返回: {"domain": ..., "citations": [{"title": ..., "summary": ..., "key_fields": ...}]}
                for c in backend_resp.get("citations", []):
                    ctx_item = {
                        "title": c.get("title", ""),
                        "summary": c.get("summary", ""),
                        "key_fields": c.get("key_fields", {}),
                    }
                    # 如果 summary 为空，尝试从 key_fields 拼接关键信息
                    if not ctx_item["summary"] and ctx_item["key_fields"]:
                        parts = [f"{k}: {v}" for k, v in ctx_item["key_fields"].items() if v]
                        ctx_item["summary"] = " | ".join(parts)
                    contexts.append(ctx_item)
            evidence = build_evidence_text(contexts)
            context_count = backend_resp.get("context_count", len(contexts))
            retrieval_mode = backend_resp.get("retrieval_mode") or ("混合检索" if domain == "policy" else "纯SQL")
            print(f"  检索到 {context_count} 条证据 (链路: {retrieval_mode})")
        except Exception as e:
            print(f"  [ERROR] 后端调用失败: {e}")
            evidence = "（检索服务不可用）"
            contexts = []

        # 2) 用每个模型生成回答
        answers = {}
        for model_name in model_names:
            cfg = MODELS[model_name]
            prompt = GENERATION_PROMPT.format(evidence=evidence, question=question)
            try:
                answer = call_llm_with_retry(
                    cfg["api_key"], cfg["base_url"], cfg["model"], prompt
                )
                answers[model_name] = answer
                print(f"  {model_name:15s} | {len(answer)} 字符")
            except Exception:
                answers[model_name] = ""
                print(f"  {model_name:15s} | [FAILED]")

        results[qid] = {
            "question": question,
            "ground_truth": ground_truth,
            "domain": domain,
            "question_type": question_type,
            "difficulty": difficulty,
            "evidence": evidence,
            "context_count": len(contexts),
            "answers": answers,
        }

        # 每题跑完立即保存，支持断点续跑
        save_path = os.path.join(output_dir, "generation_results.json")
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        # 请求间隔，避免限流
        time.sleep(0.5)

    print(f"\n生成结果已保存: {save_path}")

    return results


# ============================================================
# 阶段 2：LLM-as-Judge 评分
# ============================================================
def judge_answers(results: dict, output_dir: str) -> dict:
    """用 qwen-max 对每个模型的回答进行评分"""
    judge_cfg = JUDGE_MODEL
    model_names = list(MODELS.keys())
    total = len(results)
    judge_total = total * len(model_names)

    print(f"\n{'='*60}")
    print(f"阶段 2：LLM-as-Judge 评分 | 共 {judge_total} 次调用")
    print(f"{'='*60}")

    judge_results = {}
    success_count = 0
    fail_count = 0

    for qid, item in results.items():
        judge_results[qid] = {}
        question = item["question"]
        ground_truth = item["ground_truth"]

        for model_name in model_names:
            prediction = item["answers"].get(model_name, "")
            if not prediction:
                judge_results[qid][model_name] = None
                continue

            prompt = JUDGE_PROMPT.format(
                question=question,
                ground_truth=ground_truth,
                prediction=prediction,
            )

            try:
                resp = call_llm_with_retry(
                    judge_cfg["api_key"], judge_cfg["base_url"], judge_cfg["model"],
                    prompt, max_retries=2
                )
                scores = parse_judge_response(resp)
                judge_results[qid][model_name] = scores
                if scores:
                    success_count += 1
                    print(f"  [{qid}] {model_name:15s} | 准确={scores['accuracy']} 幻觉={scores['faithfulness']} 引用={scores['citation_accuracy']} 完整={scores['completeness']}")
                else:
                    fail_count += 1
                    print(f"  [{qid}] {model_name:15s} | [PARSE ERROR] {resp[:80]}")
            except Exception as e:
                fail_count += 1
                judge_results[qid][model_name] = None
                print(f"  [{qid}] {model_name:15s} | [ERROR] {e}")

            time.sleep(0.3)

    print(f"\n评分完成: 成功 {success_count}, 失败 {fail_count}")

    save_path = os.path.join(output_dir, "judge_results.json")
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(judge_results, f, ensure_ascii=False, indent=2)
    print(f"评分结果已保存: {save_path}")

    return judge_results


# ============================================================
# 阶段 3：BERTScore + ROUGE-L
# ============================================================
def calc_automated_metrics(results: dict) -> dict:
    """计算 BERTScore 和 ROUGE-L"""
    model_names = list(MODELS.keys())
    total = len(results)
    metric_results = {}

    print(f"\n{'='*60}")
    print(f"阶段 3：自动化指标 | BERTScore: {_BERTSCORE_AVAILABLE} | ROUGE: {_ROUGE_AVAILABLE}")
    print(f"{'='*60}")

    rouge_scorer = None
    if _ROUGE_AVAILABLE:
        rouge_scorer = RougeScorer(["rougeL"], use_stemmer=True)

    # 收集所有文本用于批量 BERTScore
    all_predictions = {m: [] for m in model_names}
    all_references = []

    for qid, item in results.items():
        gt = item["ground_truth"]
        if not gt:
            gt = "无参考答案"
        all_references.append(gt)
        for m in model_names:
            pred = item["answers"].get(m, "")
            if not pred:
                pred = "无回答"
            all_predictions[m].append(pred)

    # BERTScore 批量计算
    bert_scores = {}
    if _BERTSCORE_AVAILABLE:
        print("  计算 BERTScore（首次运行会下载模型，请耐心等待）...")
        for m in model_names:
            try:
                P, R, F1 = bert_score_fn(
                    all_predictions[m], all_references,
                    lang="zh", verbose=False,
                    model_type="bert-base-chinese",
                )
                bert_scores[m] = {
                    "precision": [round(float(x), 4) for x in P],
                    "recall": [round(float(x), 4) for x in R],
                    "f1": [round(float(x), 4) for x in F1],
                }
                print(f"    {m}: P={float(P.mean()):.4f} R={float(R.mean()):.4f} F1={float(F1.mean()):.4f}")
            except Exception as e:
                print(f"    {m}: BERTScore 计算失败 - {e}")
                bert_scores[m] = {"precision": [0.0]*total, "recall": [0.0]*total, "f1": [0.0]*total}

    # ROUGE-L 逐条计算
    rouge_scores = {}
    if _ROUGE_AVAILABLE:
        print("  计算 ROUGE-L...")
        for m in model_names:
            rouge_scores[m] = []
            for idx in range(total):
                pred = all_predictions[m][idx]
                ref = all_references[idx]
                score = rouge_scorer.score(ref, pred)
                rouge_scores[m].append(round(score["rougeL"].fmeasure, 4))
            avg_rouge = sum(rouge_scores[m]) / len(rouge_scores[m])
            print(f"    {m}: ROUGE-L = {avg_rouge:.4f}")

    # 汇总
    qids = list(results.keys())
    for idx, qid in enumerate(qids):
        metric_results[qid] = {}
        for m in model_names:
            metric_results[qid][m] = {
                "bert_score": {
                    "precision": bert_scores.get(m, {}).get("precision", [0.0])[idx],
                    "recall": bert_scores.get(m, {}).get("recall", [0.0])[idx],
                    "f1": bert_scores.get(m, {}).get("f1", [0.0])[idx],
                } if _BERTSCORE_AVAILABLE else None,
                "rouge_l": rouge_scores.get(m, [0.0])[idx] if _ROUGE_AVAILABLE else None,
            }

    return metric_results


# ============================================================
# 阶段 4：生成报告
# ============================================================
def build_report(results: dict, judge_results: dict, metric_results: dict,
                 output_dir: str, sample_config: Optional[Dict[str, int]] = None) -> dict:
    """汇总所有结果，生成最终报告"""
    model_names = list(MODELS.keys())

    # --- 按模型汇总 ---
    model_summary = {}
    for m in model_names:
        scores = {"accuracy": [], "faithfulness": [], "citation_accuracy": [], "completeness": []}
        bert_f1s = []
        rouge_ls = []

        for qid in results:
            # Judge 评分
            js = judge_results.get(qid, {}).get(m)
            if js:
                for k in scores:
                    scores[k].append(js[k])

            # 自动化指标
            am = metric_results.get(qid, {}).get(m, {})
            if am:
                if am.get("bert_score") and am["bert_score"].get("f1") is not None:
                    bert_f1s.append(am["bert_score"]["f1"])
                if am.get("rouge_l") is not None:
                    rouge_ls.append(am["rouge_l"])

        def avg(lst):
            return round(sum(lst) / len(lst), 2) if lst else 0.0

        model_summary[m] = {
            "count": len(scores["accuracy"]),
            "accuracy_avg": avg(scores["accuracy"]),
            "faithfulness_avg": avg(scores["faithfulness"]),
            "citation_accuracy_avg": avg(scores["citation_accuracy"]),
            "completeness_avg": avg(scores["completeness"]),
            "overall_avg": avg([avg(scores[k]) for k in scores]),
            "bert_score_f1_avg": avg(bert_f1s),
            "rouge_l_avg": avg(rouge_ls),
        }

    # --- 按域汇总 ---
    domain_summary = {}
    for m in model_names:
        domain_summary[m] = {}
        for qid, item in results.items():
            d = item.get("domain", "unknown")
            if d not in domain_summary[m]:
                domain_summary[m][d] = {"accuracy": [], "faithfulness": []}
            js = judge_results.get(qid, {}).get(m)
            if js:
                domain_summary[m][d]["accuracy"].append(js["accuracy"])
                domain_summary[m][d]["faithfulness"].append(js["faithfulness"])

    def avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else 0.0

    # 扁平化 domain_summary
    domain_flat = {}
    for m in model_names:
        for d, vals in domain_summary[m].items():
            key = f"{m}_{d}"
            domain_flat[key] = {
                "model": m,
                "domain": d,
                "count": len(vals["accuracy"]),
                "accuracy_avg": avg(vals["accuracy"]),
                "faithfulness_avg": avg(vals["faithfulness"]),
            }

    # --- 按难度汇总 ---
    difficulty_summary = {}
    for m in model_names:
        difficulty_summary[m] = {}
        for qid, item in results.items():
            diff = item.get("difficulty", "medium")
            if diff not in difficulty_summary[m]:
                difficulty_summary[m][diff] = {"accuracy": [], "overall": []}
            js = judge_results.get(qid, {}).get(m)
            if js:
                difficulty_summary[m][diff]["accuracy"].append(js["accuracy"])
                difficulty_summary[m][diff]["overall"].append(
                    avg([js["accuracy"], js["faithfulness"], js["citation_accuracy"], js["completeness"]])
                )

    # --- 构建报告 ---
    report = {
        "meta": {
            "eval_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_questions": len(results),
            "models_evaluated": model_names,
            "judge_model": next((name for name, cfg in MODELS.items() if cfg is JUDGE_MODEL), "qwen-max"),
            "sample_config": sample_config or SAMPLE_CONFIG,
        },
        "model_summary": model_summary,
        "domain_summary": domain_flat,
        "difficulty_summary": {
            m: {d: {k: avg(v) for k, v in vals.items()} for d, vals in diffs.items()}
            for m, diffs in difficulty_summary.items()
        },
    }

    # 保存报告
    report_path = os.path.join(output_dir, "evaluation_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report


def print_report(report: dict):
    """打印评测报告"""
    meta = report["meta"]
    summary = report["model_summary"]

    print(f"\n{'='*70}")
    print(f"  RAG 生成阶段大模型对比评测报告")
    print(f"{'='*70}")
    print(f"  评测时间: {meta['eval_time']}")
    print(f"  题目数量: {meta['total_questions']}")
    print(f"  评测模型: {', '.join(meta['models_evaluated'])}")
    print(f"  评分模型: {meta['judge_model']}")
    print(f"{'='*70}")

    # 总览表
    print(f"\n{'模型':<15} {'准确性':>8} {'无幻觉':>8} {'引用准确':>8} {'完整度':>8} {'综合':>8} {'BERT-F1':>8} {'ROUGE-L':>8}")
    print(f"{'-'*79}")
    for m, s in summary.items():
        print(f"{m:<15} {s['accuracy_avg']:>7.1f}% {s['faithfulness_avg']:>7.1f}% {s['citation_accuracy_avg']:>7.1f}% {s['completeness_avg']:>7.1f}% {s['overall_avg']:>7.1f}% {s['bert_score_f1_avg']:>8.4f} {s['rouge_l_avg']:>8.4f}")

    # 找最佳模型
    best_overall = max(summary.items(), key=lambda x: x[1]["overall_avg"])
    best_bert = max(summary.items(), key=lambda x: x[1]["bert_score_f1_avg"])
    best_rouge = max(summary.items(), key=lambda x: x[1]["rouge_l_avg"])

    print(f"\n  [综合最佳] {best_overall[0]} ({best_overall[1]['overall_avg']:.2f})")
    print(f"  [BERT最佳] {best_bert[0]} ({best_bert[1]['bert_score_f1_avg']:.4f})")
    print(f"  [ROUGE最佳] {best_rouge[0]} ({best_rouge[1]['rouge_l_avg']:.4f})")

    # 按难度
    diff_summary = report.get("difficulty_summary", {})
    if diff_summary:
        print(f"\n--- 按难度分布 ---")
        for m, diffs in diff_summary.items():
            for d, vals in diffs.items():
                print(f"  {m:<15} {d:<10} 准确={vals['accuracy_avg']:.2f} 综合={vals['overall_avg']:.2f}")

    print(f"\n{'='*70}")
    print(f"  报告已保存至 evaluation_report.json")
    print(f"{'='*70}")


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="RAG 多模型对比评测")
    parser.add_argument("--data", type=str,
                        default=os.path.join(os.path.dirname(__file__), "..", "tests", "qa_dataset_merged.json"),
                        help="评测数据集路径")
    parser.add_argument("--output", type=str,
                        default=os.path.join(os.path.dirname(__file__), "eval_output"),
                        help="输出目录")
    parser.add_argument("--sample", type=int, default=300,
                        help="抽样数量（默认 300）")
    parser.add_argument("--skip-generate", action="store_true",
                        help="跳过生成阶段，直接从已有 results 加载")
    parser.add_argument("--skip-judge", action="store_true",
                        help="跳过 Judge 评分（仅计算自动化指标）")
    parser.add_argument("--fresh", action="store_true",
                        help="Ignore checkpoint files for this run and regenerate results")
    args = parser.parse_args()
    global MODELS, JUDGE_MODEL
    MODELS = configured_models()
    JUDGE_MODEL = MODELS.get("qwen-max") or next(iter(MODELS.values()))

    # 创建输出目录
    os.makedirs(args.output, exist_ok=True)

    # 加载数据集
    print(f"加载数据集: {args.data}")
    if not os.path.exists(args.data):
        print(f"数据集不存在: {args.data}")
        sys.exit(1)

    with open(args.data, "r", encoding="utf-8") as f:
        all_data = json.load(f)
    print(f"总数据量: {len(all_data)} 条")

    # 统计域分布
    domain_dist = Counter(item.get("domain", "unknown") for item in all_data)
    print(f"域分布: {dict(domain_dist)}")

    # 抽样
    sample_config = build_sample_config(args.sample)
    print(f"抽样配置: {sample_config}")
    eval_data = sample_dataset(all_data, sample_config)
    run_id = build_run_id(eval_data, sample_config)
    args.output = resolve_output_dir(args.output, run_id)
    os.makedirs(args.output, exist_ok=True)
    print(f"本次输出目录: {args.output}")
    print(f"抽样: {len(eval_data)} 条")

    # 登录后端
    print(f"\n连接后端: {BACKEND_BASE_URL}")
    try:
        global _current_token
        _current_token = login()
        print("登录成功")
    except Exception as e:
        print(f"后端登录失败: {e}")
        print("请确保后端服务已启动 (uvicorn app.main:app)")
        sys.exit(1)

    # 阶段 1：生成回答
    if args.skip_generate:
        gen_path = os.path.join(args.output, "generation_results.json")
        if os.path.exists(gen_path):
            print(f"\n跳过生成，加载已有结果: {gen_path}")
            with open(gen_path, "r", encoding="utf-8") as f:
                results = json.load(f)
        else:
            print(f"未找到生成结果: {gen_path}，请先运行生成阶段")
            sys.exit(1)
    else:
        results = generate_answers(eval_data, args.output, fresh=args.fresh)

    # 阶段 2：LLM-as-Judge 评分
    if args.skip_judge:
        judge_path = os.path.join(args.output, "judge_results.json")
        if os.path.exists(judge_path):
            print(f"\n跳过评分，加载已有结果: {judge_path}")
            with open(judge_path, "r", encoding="utf-8") as f:
                judge_results = json.load(f)
        else:
            judge_results = judge_answers(results, args.output)
    else:
        judge_results = judge_answers(results, args.output)

    # 阶段 3：BERTScore + ROUGE-L
    metric_results = calc_automated_metrics(results)

    # 保存自动化指标
    metric_path = os.path.join(args.output, "metric_results.json")
    with open(metric_path, "w", encoding="utf-8") as f:
        json.dump(metric_results, f, ensure_ascii=False, indent=2)
    print(f"自动化指标已保存: {metric_path}")

    # 阶段 4：生成报告
    report = build_report(results, judge_results, metric_results, args.output, sample_config)
    print_report(report)


if __name__ == "__main__":
    main()
