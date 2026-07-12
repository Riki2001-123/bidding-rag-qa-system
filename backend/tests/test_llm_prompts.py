import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.services.llm_prompts as llm_prompts
from app.services.llm import LLMService


class LLMPromptLoaderTestCase(unittest.TestCase):
    def tearDown(self):
        llm_prompts.clear_prompt_cache()

    def test_policy_prompt_loads_from_markdown(self):
        prompt = llm_prompts.get_prompt("policy")
        messages = prompt.format_messages(
            domain="policy",
            user_role="admin",
            question="测试问题",
            evidence="测试证据",
        )

        self.assertGreaterEqual(len(messages), 4)
        self.assertIn("政策法规", messages[0].content)
        self.assertIn("测试问题", messages[-1].content)
        self.assertIn("测试证据", messages[-1].content)

    def test_missing_domain_prompt_falls_back_to_default_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt_dir = Path(tmp)
            self._write_file(
                prompt_dir / "default.md",
                (
                    "# System Prompt\n"
                    "DEFAULT SYSTEM {domain} {user_role}\n\n"
                    "# Few-shot\n\n"
                    "## Example 1\n"
                    "### User\n"
                    "示例用户\n\n"
                    "### Assistant\n"
                    "示例助手\n\n"
                    "# User Template\n"
                    "问题：{question}\n证据：{evidence}\n"
                ),
            )
            self._write_file(prompt_dir / "history.md", "# System Prompt\nHISTORY {domain} {user_role}\n")

            with patch.object(llm_prompts, "PROMPT_DIR", prompt_dir):
                llm_prompts.clear_prompt_cache()
                prompt = llm_prompts.get_prompt("policy")
                messages = prompt.format_messages(
                    domain="policy",
                    user_role="admin",
                    question="Q",
                    evidence="E",
                )

            self.assertIn("DEFAULT SYSTEM", messages[0].content)
            self.assertIn("Q", messages[-1].content)

    def test_invalid_domain_prompt_falls_back_to_default_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt_dir = Path(tmp)
            self._write_file(
                prompt_dir / "default.md",
                (
                    "# System Prompt\n"
                    "DEFAULT FALLBACK\n\n"
                    "# Few-shot\n\n"
                    "# User Template\n"
                    "问题：{question}\n证据：{evidence}\n"
                ),
            )
            self._write_file(prompt_dir / "history.md", "# System Prompt\nHISTORY {domain} {user_role}\n")
            self._write_file(
                prompt_dir / "enterprise.md",
                (
                    "# System Prompt\n"
                    "BROKEN ENTERPRISE\n\n"
                    "# Few-shot\n\n"
                    "## Example 1\n"
                    "### User\n"
                    "只有用户，没有助手\n"
                ),
            )

            with patch.object(llm_prompts, "PROMPT_DIR", prompt_dir):
                llm_prompts.clear_prompt_cache()
                prompt = llm_prompts.get_prompt("enterprise")
                messages = prompt.format_messages(
                    domain="enterprise",
                    user_role="admin",
                    question="Q",
                    evidence="E",
                )

            self.assertIn("DEFAULT FALLBACK", messages[0].content)

    def test_history_prompt_uses_markdown_template(self):
        content = llm_prompts.build_history_system_prompt("enterprise", "admin")
        self.assertIn("enterprise", content)
        self.assertIn("admin", content)

    def test_history_prompt_falls_back_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt_dir = Path(tmp)
            self._write_file(
                prompt_dir / "default.md",
                (
                    "# System Prompt\n"
                    "DEFAULT\n\n"
                    "# Few-shot\n\n"
                    "# User Template\n"
                    "问题：{question}\n证据：{evidence}\n"
                ),
            )

            with patch.object(llm_prompts, "PROMPT_DIR", prompt_dir):
                llm_prompts.clear_prompt_cache()
                content = llm_prompts.build_history_system_prompt("tender", "supplier")

            self.assertIn("tender", content)
            self.assertIn("supplier", content)
            self.assertIn("必须严格基于提供的证据回答", content)

    @staticmethod
    def _write_file(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")


class LLMServiceRegressionTestCase(unittest.TestCase):
    def test_call_langchain_without_history_uses_markdown_prompt(self):
        service = LLMService()
        fake_llm = RunnableLambda(lambda _: AIMessage(content="normal ok"))

        answer = service._call_langchain(
            fake_llm,
            question="测试问题",
            domain="policy",
            user_role="admin",
            contexts=[
                {
                    "title": "测试标题",
                    "summary": "测试摘要",
                    "key_fields": {"来源": "测试"},
                }
            ],
            history_messages=None,
        )

        self.assertEqual(answer, "normal ok")

    def test_call_langchain_with_history_uses_history_prompt(self):
        service = LLMService()
        fake_llm = RunnableLambda(lambda _: AIMessage(content="history ok"))

        answer = service._call_langchain(
            fake_llm,
            question="这是追问",
            domain="enterprise",
            user_role="admin",
            contexts=[
                {
                    "title": "企业测试",
                    "summary": "企业摘要",
                    "key_fields": {"行业": "软件和信息技术服务业"},
                }
            ],
            history_messages=[
                {"role": "user", "content": "上一轮问题"},
                {"role": "assistant", "content": "上一轮回答"},
            ],
        )

        self.assertEqual(answer, "history ok")


if __name__ == "__main__":
    unittest.main()
