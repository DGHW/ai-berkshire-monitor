#!/usr/bin/env python3
"""Codex packaging regression tests, using isolated files under work/ only.

Run: .venv/Scripts/python.exe tests/test_codex_adapter.py
These tests do not install skills, run research, or touch production state.
"""

import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SKILLS = load_script("codex_skill_generator", "sync-codex-skills.py")
PROMPTS = load_script("codex_prompt_generator", "sync-codex-prompts.py")


class TestCodexAdapter(unittest.TestCase):
    def setUp(self):
        scratch = REPO / "work"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="codex-adapter-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / "skills"
        self.sources.mkdir()
        self.runtime = self.root / "docs" / "codex-research-runtime.md"
        self.runtime.parent.mkdir()
        self.runtime.write_text("# Runtime\nUse actual spawn_agent calls.\n", encoding="utf-8")
        for name in (
            "investment-team", "investment-team-v2", "super-research",
            "earnings-team", "news-pulse", "private-company-research", "wechat-article",
        ):
            self.source(name, f"# {name}\nFresh independent research.\n")
        self.source("investment-team-lite", "# Lite\nAgent 数：1 个。\n")
        self.financial_body = "# Financial facts\nKeep the user's updated canonical schema.\n"
        self.source("financial-data", self.financial_body)
        self.source("INDEX", "# Documentation index\n")
        self.source("investment-memo-craft", "# Must not overwrite the hand-authored skill\n")
        self.manual = self.root / "codex-skills" / "investment-memo-craft" / "SKILL.md"
        self.manual.parent.mkdir(parents=True)
        self.manual.write_text("# Hand-authored Codex overlay\n", encoding="utf-8")
        skill_patch = mock.patch.multiple(
            SKILLS, ROOT=self.root, CLAUDE_SKILLS=self.sources,
            CODEX_SKILLS=self.root / "codex-skills", CODEX_RUNTIME=self.runtime,
        )
        prompt_patch = mock.patch.multiple(
            PROMPTS, ROOT=self.root, CLAUDE_SKILLS=self.sources,
            CODEX_PROMPTS=self.root / "codex-prompts",
        )
        skill_patch.start()
        prompt_patch.start()
        self.addCleanup(skill_patch.stop)
        self.addCleanup(prompt_patch.stop)

    def source(self, name, text):
        path = self.sources / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def invoke(self, module, check=False):
        output = io.StringIO()
        argv = ["generator", "--check"] if check else ["generator"]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            module.main()
        return output.getvalue()

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*") if path.is_file()
        }

    def test_core_skills_package_runtime_and_check_is_read_only(self):
        self.invoke(SKILLS)
        for name in (
            "investment-team", "investment-team-v2", "super-research",
            "earnings-team", "news-pulse", "private-company-research", "wechat-article",
        ):
            folder = self.root / "codex-skills" / name
            text = (folder / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("references/codex-research-runtime.md", text)
            self.assertIn("takes precedence", text)
            self.assertEqual(
                (folder / "references" / "codex-research-runtime.md").read_bytes(),
                self.runtime.read_bytes(),
            )
        before = self.snapshot()
        self.assertIn("Checked", self.invoke(SKILLS, check=True))
        self.assertEqual(before, self.snapshot())

    def test_source_and_runtime_changes_are_stale_without_check_writes(self):
        self.invoke(SKILLS)
        self.source("financial-data", self.financial_body + "New canonical fact.\n")
        self.runtime.write_text("# Runtime\nRevised execution contract.\n", encoding="utf-8")
        before = self.snapshot()
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["generator", "--check"]), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                SKILLS.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn(str(Path("financial-data") / "SKILL.md"), output.getvalue())
        self.assertIn("codex-research-runtime.md", output.getvalue())
        self.assertEqual(before, self.snapshot())

    def test_missing_or_modified_packaged_reference_is_stale(self):
        self.invoke(SKILLS)
        reference = (
            self.root / "codex-skills" / "investment-team-v2" /
            "references" / "codex-research-runtime.md"
        )
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    reference.unlink()
                else:
                    reference.write_text("# Local drift\n", encoding="utf-8")
                before = self.snapshot()
                with self.assertRaises(SystemExit) as raised:
                    self.invoke(SKILLS, check=True)
                self.assertEqual(raised.exception.code, 1)
                self.assertEqual(before, self.snapshot())

    def test_manual_overlay_and_updated_financial_body_are_preserved(self):
        manual = self.manual.read_bytes()
        self.invoke(SKILLS)
        self.assertEqual(manual, self.manual.read_bytes())
        self.assertFalse((self.root / "codex-skills" / "INDEX").exists())
        financial = self.root / "codex-skills" / "financial-data" / "SKILL.md"
        self.assertTrue(financial.read_text(encoding="utf-8").endswith(self.financial_body))

    def test_source_metadata_is_preserved(self):
        metadata = (
            "---\nname: financial-data\ndescription: Existing description\n"
            "allowed-tools: Read\n---\n\n"
        )
        self.source("financial-data", metadata + self.financial_body)
        self.invoke(SKILLS)
        text = (self.root / "codex-skills" / "financial-data" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(metadata))
        self.assertEqual(text.count("name: financial-data\n"), 1)
        self.assertEqual(text.count("description: Existing description\n"), 1)

    def test_lite_retains_one_worker_semantics(self):
        self.invoke(SKILLS)
        folder = self.root / "codex-skills" / "investment-team-lite"
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("remains one worker per stock", text)
        self.assertTrue(text.endswith("# Lite\nAgent 数：1 个。\n"))
        self.assertFalse((folder / "references").exists())

    def test_additional_task_source_receives_runtime(self):
        self.source("custom-delegate", "# Research\n使用 Task 工具收集资料。\n")
        self.invoke(SKILLS)
        self.assertTrue((
            self.root / "codex-skills" / "custom-delegate" /
            "references" / "codex-research-runtime.md"
        ).is_file())

    def test_prompt_fallback_resolves_real_paths_and_check_is_read_only(self):
        self.invoke(PROMPTS)
        path = self.root / "codex-prompts" / "super-research.md"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("~/ai-berkshire", text)
        self.assertIn("session's installed skill catalog", text)
        self.assertIn("actual AI Berkshire checkout", text)
        self.assertIn("$ARGUMENTS", text)
        self.assertFalse((self.root / "codex-prompts" / "INDEX.md").exists())
        before = self.snapshot()
        self.invoke(PROMPTS, check=True)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
