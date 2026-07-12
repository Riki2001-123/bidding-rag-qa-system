"""
RAG 问答系统评估脚本
==================
功能：
1. 读取评估数据集（CSV/JSON）
2. 调用问答接口获取系统输出
3. 从三个维度评估：检索质量、生成质量、端到端
4. 输出评估报告

用法：
  python scripts/evaluate.py --data eval_data.csv --output eval_report.json
  python scripts/evaluate.py --data eval_data.csv --output eval_report.json --parallel
  python scripts/evaluate.py --data eval_data.csv --output eval_report.json --top_k 10

评估数据集格式（CSV）：
  question,ground_truth,domain,source_record_ids,difficulty
  "XX公司中标的金额是多少","XX公司中标金额为500万元","tender","123,456","easy"

评估数据集格式（JSON）：
  [
    {
      "question": "XX公司中标的金额是多少",
      "ground_truth": "XX公司中标金额为500万元",
      "domain": "tender",
      "source_record_ids": [123, 456],
      "difficulty": "easy"
    }
  ]
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

# 尝试导入 BERTScore（可选依赖，不可用时降级为字符级指标）
_BERTSCORE_AVAILABLE = False
try:
    from bert_score import score as bert_score_fn
    _BERTSCORE_AVAILABLE = True
except ImportError:
    pass

# ============================================================
# 配置
# ============================================================
BASE_URL = os.getenv("EVAL_BASE_URL", "http://localhost:8000/api")
CHAT_ENDPOINT = f"{BASE_URL}/chat/query"
LOGIN_ENDPOINT = f"{BASE_URL}/auth/login"
USERNAME = os.getenv("EVAL_USERNAME", "admin")
PASSWORD = os.getenv("EVAL_PASSWORD", "admin123")

# 请求间隔（秒），避免过快请求
REQUEST_INTERVAL = 1.0


# ============================================================
# 工具函数
# ============================================================
def login() -> str:
    """登录获取 token"""
    resp = requests.post(LOGIN_ENDPOINT, json={"username": USERNAME, "password": PASSWORD})
    resp.raise_for_status()
    return resp.json()["access_token"]


def query_rag(question: str, domain: Optional[str] = None, top_k: int = 5,
              token: str = "", session_id: Optional[int] = None) -> dict:
    """调用问答接口"""
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "question": question,
        "domain": domain,
        "top_k": top_k,
    }
    if session_id:
        payload["session_id"] = session_id

    resp = requests.post(CHAT_ENDPOINT, json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()


# ============================================================
# 评估指标计算
# ============================================================
def calc_retrieval_metrics(citation_record_ids: List[int],
                           ground_truth_ids: List[int],
                           top_k: int) -> Dict[str, float]:
    """
    检索质量指标
    - Recall@K: ground truth 中的记录有多少被召回了
    - Precision@K: 召回的记录中有多少是正确的
    - MRR: 第一个正确结果的排名倒数
    """
    if not ground_truth_ids:
        return {"recall": 0.0, "precision": 0.0, "mrr": 0.0}

    gt_set = set(ground_truth_ids)
    retrieved_set = set(citation_record_ids[:top_k])
    hits = retrieved_set & gt_set

    recall = len(hits) / len(gt_set) if gt_set else 0.0
    precision = len(hits) / len(citation_record_ids[:top_k]) if citation_record_ids else 0.0

    # MRR
    mrr = 0.0
    for rank, rid in enumerate(citation_record_ids[:top_k], start=1):
        if rid in gt_set:
            mrr = 1.0 / rank
            break

    return {
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "mrr": round(mrr, 4),
    }


def calc_text_similarity(text_a: str, text_b: str) -> float:
    """
    简单的文本相似度（基于字符级 Jaccard 相似度）
    作为生成质量的快速评估手段
    """
    if not text_a or not text_b:
        return 0.0

    set_a = set(text_a)
    set_b = set(text_b)
    intersection = set_a & set_b
    union = set_a | set_b

    if not union:
        return 0.0
    return len(intersection) / len(union)


def calc_bert_score(prediction: str, ground_truth: str) -> Dict[str, float]:
    """
    BERTScore 语义相似度指标（需 pip install bert_score）。
    返回 P/R/F1 三个维度的分数。
    """
    if not _BERTSCORE_AVAILABLE or not prediction or not ground_truth:
        return {"bert_precision": 0.0, "bert_recall": 0.0, "bert_f1": 0.0}
    try:
        P, R, F1 = bert_score_fn([prediction], [ground_truth], lang="zh", verbose=False)
        return {
            "bert_precision": round(float(P[0]), 4),
            "bert_recall": round(float(R[0]), 4),
            "bert_f1": round(float(F1[0]), 4),
        }
    except Exception:
        return {"bert_precision": 0.0, "bert_recall": 0.0, "bert_f1": 0.0}


def calc_generation_metrics(prediction: str, ground_truth: str,
                            use_bert: bool = True) -> Dict[str, float]:
    """
    生成质量指标
    - char_similarity: 字符级 Jaccard 相似度（兜底指标）
    - keyword_overlap: 关键词重叠率
    - length_ratio: 预测长度 / 标准答案长度（衡量完整性）
    - bert_f1 / bert_precision / bert_recall: BERTScore 语义指标（可选）
    """
    sim = calc_text_similarity(prediction, ground_truth)

    # 关键词重叠
    gt_keywords = set(ground_truth)
    pred_keywords = set(prediction)
    if gt_keywords:
        keyword_overlap = len(gt_keywords & pred_keywords) / len(gt_keywords)
    else:
        keyword_overlap = 0.0

    # 长度比
    len_gt = len(ground_truth)
    len_pred = len(prediction)
    length_ratio = len_pred / len_gt if len_gt > 0 else 0.0

    result = {
        "char_similarity": round(sim, 4),
        "keyword_overlap": round(keyword_overlap, 4),
        "length_ratio": round(length_ratio, 4),
    }

    # BERTScore 语义指标（需要 bert_score 库）
    if use_bert and _BERTSCORE_AVAILABLE:
        bert = calc_bert_score(prediction, ground_truth)
        result.update(bert)

    return result


def calc_end_to_end_score(prediction: str, ground_truth: str,
                          retrieval_metrics: Dict[str, float]) -> Dict[str, float]:
    """
    端到端综合评分
    - retrieval_weighted: 检索指标加权
    - generation_weighted: 生成指标加权
    - overall: 综合得分
    """
    retrieval_score = (
        retrieval_metrics["recall"] * 0.5 +
        retrieval_metrics["precision"] * 0.3 +
        retrieval_metrics["mrr"] * 0.2
    )

    gen_metrics = calc_generation_metrics(prediction, ground_truth)

    # 生成质量评分：BERTScore 可用时优先用 bert_f1，否则退化为字符级指标
    if "bert_f1" in gen_metrics and gen_metrics["bert_f1"] > 0:
        generation_score = (
            gen_metrics["bert_f1"] * 0.4 +
            gen_metrics["keyword_overlap"] * 0.3 +
            min(gen_metrics["length_ratio"], 1.5) / 1.5 * 0.3
        )
    else:
        generation_score = (
            gen_metrics["char_similarity"] * 0.3 +
            gen_metrics["keyword_overlap"] * 0.4 +
            min(gen_metrics["length_ratio"], 1.5) / 1.5 * 0.3
        )

    overall = retrieval_score * 0.4 + generation_score * 0.6

    return {
        "retrieval_score": round(retrieval_score, 4),
        "generation_score": round(generation_score, 4),
        "overall": round(overall, 4),
    }


# ============================================================
# 数据加载
# ============================================================
def load_dataset(filepath: str) -> List[dict]:
    """加载评估数据集，支持 CSV 和 JSON"""
    items = []

    if filepath.endswith(".json"):
        with open(filepath, "r", encoding="utf-8") as f:
            items = json.load(f)
    elif filepath.endswith(".csv"):
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                item = {
                    "question": row["question"],
                    "ground_truth": row["ground_truth"],
                    "domain": row.get("domain", ""),
                    "source_record_ids": [],
                    "difficulty": row.get("difficulty", "medium"),
                }
                # 解析 source_record_ids
                ids_str = row.get("source_record_ids", "")
                if ids_str:
                    item["source_record_ids"] = [int(x.strip()) for x in ids_str.split(",") if x.strip().isdigit()]
                items.append(item)
    else:
        print(f"不支持的文件格式: {filepath}，请使用 .csv 或 .json")
        sys.exit(1)

    print(f"加载了 {len(items)} 条评估数据")
    return items


# ============================================================
# 生成示例评估数据集
# ============================================================
def generate_sample_dataset(output_path: str):
    """生成示例评估数据集供参考"""
    samples = [
        {
            "question": "有哪些政府采购政策？",
            "ground_truth": "系统中存储了多条政府采购相关政策记录",
            "domain": "policy",
            "source_record_ids": [],
            "difficulty": "easy"
        },
        {
            "question": "最近有哪些招标项目？",
            "ground_truth": "系统中有大量招标项目记录，涵盖多个地区和行业",
            "domain": "tender",
            "source_record_ids": [],
            "difficulty": "easy"
        },
        {
            "question": "有哪些企业供应商？",
            "ground_truth": "系统中有多种类型的企业供应商记录",
            "domain": "enterprise",
            "source_record_ids": [],
            "difficulty": "easy"
        },
    ]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
    print(f"示例数据集已生成: {output_path}")
    print("请根据实际数据修改 question/ground_truth/source_record_ids 字段")


# ============================================================
# 主评估流程
# ============================================================
def run_evaluation(dataset: List[dict], top_k: int = 5,
                   max_items: Optional[int] = None) -> Dict:
    """
    执行评估
    返回完整的评估结果
    """
    print(f"\n{'='*60}")
    print(f"开始评估 - 共 {len(dataset)} 条数据")
    print(f"BERTScore: {'已启用' if _BERTSCORE_AVAILABLE else '未安装（pip install bert_score）'}")
    print(f"{'='*60}\n")

    token = login()
    print(f"登录成功，获取 token")

    if max_items:
        dataset = dataset[:max_items]
        print(f"限制评估数量: {max_items} 条")

    # 结果存储
    results = []
    total_retrieval = {"recall": [], "precision": [], "mrr": []}
    total_generation = {"char_similarity": [], "keyword_overlap": [], "length_ratio": []}
    if _BERTSCORE_AVAILABLE:
        total_generation["bert_precision"] = []
        total_generation["bert_recall"] = []
        total_generation["bert_f1"] = []
    total_e2e = {"retrieval_score": [], "generation_score": [], "overall": []}
    domain_stats = {}  # 按域统计
    difficulty_stats = {}  # 按难度统计

    success_count = 0
    error_count = 0

    for i, item in enumerate(dataset):
        question = item["question"]
        ground_truth = item["ground_truth"]
        domain = item.get("domain")
        gt_ids = item.get("source_record_ids", [])
        difficulty = item.get("difficulty", "medium")

        print(f"[{i+1}/{len(dataset)}] Q: {question[:50]}...")

        try:
            resp = query_rag(question, domain=domain, top_k=top_k, token=token)
            answer = resp.get("answer", "")
            citations = resp.get("citations", [])
            resp_domain = resp.get("domain", "")

            # 提取引用的 record_id
            citation_ids = [c["record_id"] for c in citations]

            # 计算各维度指标
            ret_metrics = calc_retrieval_metrics(citation_ids, gt_ids, top_k)
            gen_metrics = calc_generation_metrics(answer, ground_truth)
            e2e = calc_end_to_end_score(answer, ground_truth, ret_metrics)

            result_item = {
                "index": i + 1,
                "question": question,
                "ground_truth": ground_truth,
                "prediction": answer,
                "domain": resp_domain,
                "expected_domain": domain,
                "difficulty": difficulty,
                "retrieval_metrics": ret_metrics,
                "generation_metrics": gen_metrics,
                "end_to_end": e2e,
                "citation_count": len(citations),
                "source_record_ids": citation_ids,
                "ground_truth_ids": gt_ids,
                "error": None,
            }
            results.append(result_item)
            success_count += 1

            # 累计统计
            for k, v in ret_metrics.items():
                total_retrieval[k].append(v)
            for k, v in gen_metrics.items():
                total_generation[k].append(v)
            for k, v in e2e.items():
                total_e2e[k].append(v)

            # 按域统计
            if resp_domain not in domain_stats:
                domain_stats[resp_domain] = {"retrieval": [], "generation": [], "e2e": []}
            domain_stats[resp_domain]["e2e"].append(e2e["overall"])

            # 按难度统计
            if difficulty not in difficulty_stats:
                difficulty_stats[difficulty] = []
            difficulty_stats[difficulty].append(e2e["overall"])

        except Exception as e:
            error_count += 1
            print(f"  ERROR: {str(e)[:100]}")
            results.append({
                "index": i + 1,
                "question": question,
                "ground_truth": ground_truth,
                "prediction": "",
                "domain": domain,
                "expected_domain": domain,
                "difficulty": difficulty,
                "retrieval_metrics": {"recall": 0, "precision": 0, "mrr": 0},
                "generation_metrics": {"char_similarity": 0, "keyword_overlap": 0, "length_ratio": 0,
                                       "bert_precision": 0, "bert_recall": 0, "bert_f1": 0} if _BERTSCORE_AVAILABLE
                else {"char_similarity": 0, "keyword_overlap": 0, "length_ratio": 0},
                "end_to_end": {"retrieval_score": 0, "generation_score": 0, "overall": 0},
                "citation_count": 0,
                "source_record_ids": [],
                "ground_truth_ids": gt_ids,
                "error": str(e),
            })

        # 请求间隔
        time.sleep(REQUEST_INTERVAL)

    # ============================================================
    # 汇总统计
    # ============================================================
    def avg(lst):
        return round(sum(lst) / len(lst), 4) if lst else 0.0

    report = {
        "meta": {
            "eval_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_items": len(dataset),
            "success_count": success_count,
            "error_count": error_count,
            "top_k": top_k,
        },
        "summary": {
            "retrieval": {k: avg(v) for k, v in total_retrieval.items()},
            "generation": {k: avg(v) for k, v in total_generation.items()},
            "end_to_end": {k: avg(v) for k, v in total_e2e.items()},
        },
        "by_domain": {k: {"avg_score": avg(v["e2e"]), "count": len(v["e2e"])}
                      for k, v in domain_stats.items()},
        "by_difficulty": {k: {"avg_score": avg(v), "count": len(v)}
                          for k, v in difficulty_stats.items()},
        "details": results,
    }

    return report


def print_report(report: Dict):
    """打印评估报告摘要"""
    meta = report["meta"]
    summary = report["summary"]

    print(f"\n{'='*60}")
    print(f"评估报告")
    print(f"{'='*60}")
    print(f"评估时间: {meta['eval_time']}")
    print(f"总数据量: {meta['total_items']} 条")
    print(f"成功: {meta['success_count']} | 失败: {meta['error_count']}")
    print(f"Top-K: {meta['top_k']}")

    print(f"\n--- 检索质量 ---")
    ret = summary["retrieval"]
    print(f"  Recall@K:    {ret['recall']:.4f}")
    print(f"  Precision@K: {ret['precision']:.4f}")
    print(f"  MRR:         {ret['mrr']:.4f}")

    print(f"\n--- 生成质量 ---")
    gen = summary["generation"]
    print(f"  字符相似度:  {gen['char_similarity']:.4f}")
    print(f"  关键词重叠:  {gen['keyword_overlap']:.4f}")
    print(f"  长度比:      {gen['length_ratio']:.4f}")
    if "bert_f1" in gen:
        print(f"  BERTScore:   P={gen.get('bert_precision', 0):.4f}  R={gen.get('bert_recall', 0):.4f}  F1={gen['bert_f1']:.4f}")
    else:
        print(f"  BERTScore:   未安装（pip install bert_score）")

    print(f"\n--- 端到端综合 ---")
    e2e = summary["end_to_end"]
    print(f"  检索得分:    {e2e['retrieval_score']:.4f}")
    print(f"  生成得分:    {e2e['generation_score']:.4f}")
    print(f"  综合得分:    {e2e['overall']:.4f}")

    if report["by_domain"]:
        print(f"\n--- 按业务域 ---")
        for domain, stats in report["by_domain"].items():
            print(f"  {domain:12s}  综合得分: {stats['avg_score']:.4f}  (n={stats['count']})")

    if report["by_difficulty"]:
        print(f"\n--- 按难度 ---")
        for diff, stats in report["by_difficulty"].items():
            print(f"  {diff:6s}  综合得分: {stats['avg_score']:.4f}  (n={stats['count']})")

    # 找出表现最差的问题
    details = report["details"]
    errors = [d for d in details if d["error"]]
    worst = sorted([d for d in details if not d["error"]],
                   key=lambda x: x["end_to_end"]["overall"])[:5]

    if errors:
        print(f"\n--- 错误案例 ({len(errors)} 条) ---")
        for e in errors[:3]:
            print(f"  Q: {e['question'][:60]}")
            print(f"  E: {str(e['error'])[:80]}")

    if worst:
        print(f"\n--- 得分最低的 5 个问题 ---")
        for w in worst:
            print(f"  [{w['end_to_end']['overall']:.2f}] Q: {w['question'][:60]}")
            print(f"         Expected: {w['ground_truth'][:60]}")
            print(f"         Got:      {w['prediction'][:60]}")

    print(f"\n{'='*60}")


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG 问答系统评估脚本")
    parser.add_argument("--data", type=str, help="评估数据集文件路径 (CSV/JSON)")
    parser.add_argument("--output", type=str, default="eval_report.json", help="评估报告输出路径")
    parser.add_argument("--top_k", type=int, default=5, help="检索返回数量，默认 5")
    parser.add_argument("--max_items", type=int, default=None, help="最多评估条数（用于快速测试）")
    parser.add_argument("--base_url", type=str, default=None, help="API 基础地址")
    parser.add_argument("--username", type=str, default=None, help="登录用户名")
    parser.add_argument("--password", type=str, default=None, help="登录密码")
    parser.add_argument("--generate_sample", type=str, default=None,
                        help="生成示例评估数据集到指定路径")

    args = parser.parse_args()

    # 覆盖默认配置
    if args.base_url:
        BASE_URL = args.base_url
        CHAT_ENDPOINT = f"{BASE_URL}/chat/query"
        LOGIN_ENDPOINT = f"{BASE_URL}/auth/login"
    if args.username:
        USERNAME = args.username
    if args.password:
        PASSWORD = args.password

    # 生成示例数据集
    if args.generate_sample:
        generate_sample_dataset(args.generate_sample)
        sys.exit(0)

    # 检查数据集
    if not args.data:
        parser.error("请指定评估数据集 --data，或使用 --generate_sample 生成示例数据集")
        sys.exit(1)

    if not os.path.exists(args.data):
        print(f"数据集文件不存在: {args.data}")
        sys.exit(1)

    # 加载数据
    dataset = load_dataset(args.data)

    # 执行评估
    report = run_evaluation(dataset, top_k=args.top_k, max_items=args.max_items)

    # 打印报告
    print_report(report)

    # 保存报告
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n评估报告已保存: {args.output}")
