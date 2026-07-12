from typing import List

from langchain.text_splitter import RecursiveCharacterTextSplitter

# 内部 splitter 实例（线程安全，可复用）
_splitter = RecursiveCharacterTextSplitter(
    chunk_size=400,
    chunk_overlap=60,
    length_function=len,
    separators=["\n\n", "\n", "。", "；", ";", "！", "？", "，", ",", " "],
)

# 保留原始常量供外部模块引用
DEFAULT_SEPARATORS = _splitter._separators


def recursive_split_text(text: str, max_chars: int = 400, overlap: int = 60) -> List[str]:
    """对文本进行递归切块。

    用 LangChain 的 RecursiveCharacterTextSplitter 实现语义感知的文本分割。
    函数签名完全兼容旧版调用方式。
    """
    text = (text or "").strip()
    if not text:
        return []

    # 如果调用方传入了与默认值不同的参数，创建临时 splitter
    if max_chars != 400 or overlap != 60:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_chars,
            chunk_overlap=overlap,
            length_function=len,
            separators=DEFAULT_SEPARATORS,
        )
    else:
        splitter = _splitter

    docs = splitter.create_documents([text])
    chunks = [doc.page_content for doc in docs]
    return [item for item in chunks if item]

