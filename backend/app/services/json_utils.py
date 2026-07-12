"""JSON 解析工具函数。"""
import json
import re
from typing import Any, Dict, Optional


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """从文本中提取 JSON 对象。先尝试直接解析，失败则正则提取。"""
    if not text:
        return None

    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None

    try:
        payload = json.loads(match.group(0))
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        return None
