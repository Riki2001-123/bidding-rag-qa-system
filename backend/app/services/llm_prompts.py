import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from langchain_core.prompts import (
    AIMessagePromptTemplate,
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
)


PROMPT_DIR = Path(__file__).with_name("prompts")

_TOP_LEVEL_SECTION_PATTERN = re.compile(r"(?m)^# (System Prompt|Few-shot|User Template)\s*$")
_EXAMPLE_SECTION_PATTERN = re.compile(r"(?m)^## (Example [^\n]+)\s*$")
_ROLE_SECTION_PATTERN = re.compile(r"(?m)^### (User|Assistant)\s*$")

_DEFAULT_HISTORY_SYSTEM_PROMPT = (
    "你是招投标采购问答助手，必须严格基于提供的证据回答。\n"
    "当前业务域：{domain}。\n"
    "当前用户角色：{user_role}。\n\n"
    "回答要求：\n"
    "1. 先直接回答问题。\n"
    "2. 再说明依据，并引用证据编号，例如[证据1]、[证据2]。\n"
    "3. 涉及金额、日期、主体名称时，必须明确来源。\n"
    "4. 如果证据不足，必须明确说明无法确认。\n"
    "5. 需要结合上下文理解用户追问和代词指代，例如“它”“第一条”“那个项目”。\n"
    "6. 如果问题明显与招投标采购业务无关，直接礼貌拒答，不要编造。"
)

_DEFAULT_SYSTEM_PROMPT = (
    "你是招投标采购问答助手，必须严格基于提供的证据作答。\n"
    "当前业务域：{domain}。\n"
    "当前用户角色：{user_role}。\n\n"
    "回答格式要求：\n"
    "1. 先给出直接答案。\n"
    "2. 然后说明依据，并引用证据编号，例如[证据1]、[证据2]。\n"
    "3. 涉及金额、日期、主体名称等关键数据时，必须说明来源。\n"
    "4. 如果证据不足，明确说明无法确认。"
)

_DEFAULT_USER_TEMPLATE = "问题：{question}\n\n证据：\n{evidence}"


@dataclass(frozen=True)
class PromptExample:
    user: str
    assistant: str


@dataclass(frozen=True)
class PromptDocument:
    system_prompt: str
    user_template: str
    examples: List[PromptExample]


_PROMPT_CACHE: Dict[str, ChatPromptTemplate] = {}
_HISTORY_PROMPT_CACHE: str = ""


def clear_prompt_cache() -> None:
    _PROMPT_CACHE.clear()
    global _HISTORY_PROMPT_CACHE
    _HISTORY_PROMPT_CACHE = ""


def get_prompt(domain: str) -> ChatPromptTemplate:
    cache_key = domain or "default"
    if cache_key in _PROMPT_CACHE:
        return _PROMPT_CACHE[cache_key]

    template = _load_prompt_template(cache_key)
    _PROMPT_CACHE[cache_key] = template
    return template


def build_history_system_prompt(domain: str, user_role: str) -> str:
    template = _load_history_template()
    return template.format(domain=domain, user_role=user_role)


def _load_prompt_template(domain: str) -> ChatPromptTemplate:
    filename = f"{domain}.md"
    try:
        document = _parse_prompt_document((PROMPT_DIR / filename).read_text(encoding="utf-8"))
    except Exception as exc:
        if domain != "default":
            print(f"[Prompt] failed to load {filename}, fallback to default.md: {exc}")
            return _load_prompt_template("default")

        print(f"[Prompt] failed to load default.md, fallback to built-in default prompt: {exc}")
        document = PromptDocument(
            system_prompt=_DEFAULT_SYSTEM_PROMPT,
            user_template=_DEFAULT_USER_TEMPLATE,
            examples=[],
        )

    messages = [SystemMessagePromptTemplate.from_template(document.system_prompt)]
    for example in document.examples:
        messages.append(HumanMessagePromptTemplate.from_template(example.user))
        messages.append(AIMessagePromptTemplate.from_template(example.assistant))
    messages.append(HumanMessagePromptTemplate.from_template(document.user_template))
    return ChatPromptTemplate.from_messages(messages)


def _load_history_template() -> str:
    global _HISTORY_PROMPT_CACHE
    if _HISTORY_PROMPT_CACHE:
        return _HISTORY_PROMPT_CACHE

    path = PROMPT_DIR / "history.md"
    try:
        sections = _split_sections(path.read_text(encoding="utf-8"), _TOP_LEVEL_SECTION_PATTERN)
        system_prompt = sections.get("System Prompt", "").strip()
        if not system_prompt:
            raise ValueError("history.md is missing '# System Prompt' content")
        _HISTORY_PROMPT_CACHE = system_prompt
    except Exception as exc:
        print(f"[Prompt] failed to load history.md, fallback to built-in history prompt: {exc}")
        _HISTORY_PROMPT_CACHE = _DEFAULT_HISTORY_SYSTEM_PROMPT

    return _HISTORY_PROMPT_CACHE


def _parse_prompt_document(content: str) -> PromptDocument:
    sections = _split_sections(content, _TOP_LEVEL_SECTION_PATTERN)
    system_prompt = sections.get("System Prompt", "").strip()
    user_template = sections.get("User Template", "").strip()
    if not system_prompt:
        raise ValueError("missing '# System Prompt' section")
    if not user_template:
        raise ValueError("missing '# User Template' section")

    examples = _parse_examples(sections.get("Few-shot", ""))
    return PromptDocument(
        system_prompt=system_prompt,
        user_template=user_template,
        examples=examples,
    )


def _parse_examples(content: str) -> List[PromptExample]:
    few_shot = content.strip()
    if not few_shot:
        return []

    examples = []
    for _, example_body in _split_sections(few_shot, _EXAMPLE_SECTION_PATTERN).items():
        roles = _split_sections(example_body, _ROLE_SECTION_PATTERN)
        user = roles.get("User", "").strip()
        assistant = roles.get("Assistant", "").strip()
        if not user or not assistant:
            raise ValueError("few-shot example must contain both '### User' and '### Assistant'")
        examples.append(PromptExample(user=user, assistant=assistant))
    return examples


def _split_sections(content: str, pattern: re.Pattern) -> Dict[str, str]:
    matches = list(pattern.finditer(content))
    if not matches:
        return {}

    sections: Dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        sections[match.group(1)] = content[start:end].strip()
    return sections
