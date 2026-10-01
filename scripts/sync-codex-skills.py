#!/usr/bin/env python3
"""Generate Codex skills from AI Berkshire Claude command files."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_SKILLS = ROOT / "skills"
CODEX_SKILLS = ROOT / "codex-skills"
CODEX_RUNTIME = ROOT / "docs" / "codex-research-runtime.md"

# INDEX is documentation, not an executable skill. This hand-authored Codex
# overlay must never be replaced by generated content, even if a source appears.
EXCLUDED_SOURCES = {"INDEX", "investment-memo-craft"}
AGENT_RUNTIME_SKILLS = {
    "deep-company-series",
    "earnings-review",
    "earnings-team",
    "expectation-arb",
    "industry-research",
    "investment-checklist",
    "investment-research",
    "investment-team",
    "investment-team-v2",
    "management-deep-dive",
    "news-pulse",
    "portfolio-review",
    "private-company-research",
    "super-research",
    "wechat-article",
}


def needs_runtime(name: str, source_text: str) -> bool:
    # Lite describes one worker per stock, including when many stocks are
    # reviewed in separate windows. Do not turn it into a four-role team.
    if name == "investment-team-lite":
        return False
    return name in AGENT_RUNTIME_SKILLS or bool(
        re.search(r"(?:Task|Agent)\s*工具|TeamCreate|TaskCreate|SendMessage", source_text)
    )


def split_frontmatter(text: str) -> tuple[str | None, str]:
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end + 5 :].lstrip("\n")


def first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def yaml_quote(value: str) -> str:
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def metadata_for(name: str, source_name: str, source_text: str) -> str:
    existing, body = split_frontmatter(source_text)
    if existing:
        has_name = re.search(r"(?m)^name:\s*", existing) is not None
        has_description = re.search(r"(?m)^description:\s*", existing) is not None
        lines = []
        if not has_name:
            lines.append(f"name: {name}")
        if not has_description:
            title = first_heading(body, name)
            lines.append(
                "description: "
                + yaml_quote(f"AI Berkshire skill: {title}. Source: skills/{source_name}.")
            )
        lines.append(existing.rstrip())
        return "---\n" + "\n".join(lines) + "\n---\n\n"

    title = first_heading(source_text, name)
    description = f"AI Berkshire skill: {title}. Source: skills/{source_name}."
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {yaml_quote(description)}\n"
        "---\n\n"
    )


def codex_body(name: str, source_name: str, source_text: str) -> str:
    _, body = split_frontmatter(source_text)
    # Empty blockquotes have identical rendering without trailing whitespace.
    body = re.sub(r"(?m)^>[ \t]+$", ">", body)
    note = (
        "## Codex adapter note\n\n"
        f"This skill is generated from `skills/{source_name}` so Claude Code "
        "and Codex users share one canonical workflow.\n\n"
        "- Treat `$ARGUMENTS` as the user's request in the current Codex thread.\n"
        "- Source `Task`/`Agent` research delegation means actual native "
        "`spawn_agent` calls (for example `collaboration.spawn_agent`); "
        "`TeamCreate`/`TaskCreate` are Claude or WorkBuddy bookkeeping and "
        "need not be called in Codex. Preserve the source's role count and "
        "dependencies. A lead must not simulate multiple researchers or "
        "claim that several report files prove separate agents ran.\n"
        "- `send_message` sends context but does not resume an idle agent. "
        "Use native `followup_task` to trigger another round in an existing "
        "agent; use the active session's documented wait and messaging APIs. "
        "If native delegation is unavailable, mark the requested team "
        "research incomplete. A lead-only alternative requires the user's "
        "explicit choice and must be labeled lead-only, never multi-agent.\n"
        "- Source `WebSearch` means real web browsing with sources; `Bash`, "
        "`Read`, and `Write` mean the session's shell and file tools. Verify "
        "actual access; do not infer Codex permissions from Claude settings "
        "or present training knowledge as a search result.\n"
        "- Locate the actual repository checkout before using its `tools/`; "
        "resolve an available Python interpreter (prefer the project's "
        "virtual environment) instead of assuming `python3` or a fixed "
        "home-directory path. Follow the applicable Windows shell wrapper.\n"
        "- Use the client's current date and timezone when supplied; "
        "otherwise confirm the date with the session clock. State the data "
        "cutoff and each source's actual period. Do not substitute the "
        "execution host's date or infer today's date from training data.\n"
        "- Preserve the research quality rules from `AGENTS.md`: cross-check "
        "financial data, use exact arithmetic tools for valuation/math, and "
        "clearly label uncertainty and source gaps.\n\n"
    )
    if needs_runtime(name, source_text):
        note += (
            "Before research, read this skill's local "
            "`references/codex-research-runtime.md`. It is the Codex runtime "
            "contract and takes precedence over conflicting source platform "
            "APIs, 429/timeout lead-synthesis shortcuts, time targets, and "
            "claims that report count or formatting proves a real team. "
            "Follow its context boundaries, blind first-round debate, "
            "evidence-stage domain judgments, and final action decision "
            "requirements rather than conflicting source instructions. "
            "Keep the source's research roles and business methodology.\n\n"
        )
    elif name == "investment-team-lite":
        note += (
            "`investment-team-lite` remains one worker per stock; do not "
            "expand a single-stock lite review into a four- or six-role "
            "research team.\n\n"
        )
    return note + body.rstrip() + "\n"


def main() -> None:
    check = "--check" in sys.argv[1:]
    unknown_args = [arg for arg in sys.argv[1:] if arg != "--check"]
    if unknown_args:
        joined = ", ".join(unknown_args)
        raise SystemExit(f"Unknown argument(s): {joined}")

    sources = [
        source for source in sorted(CLAUDE_SKILLS.glob("*.md"))
        if source.stem not in EXCLUDED_SOURCES
    ]
    source_texts = [(source, source.read_text(encoding="utf-8")) for source in sources]
    runtime = None
    if any(needs_runtime(source.stem, body) for source, body in source_texts):
        if not CODEX_RUNTIME.is_file():
            raise SystemExit(f"Missing Codex research runtime: {CODEX_RUNTIME}")
        runtime = CODEX_RUNTIME.read_text(encoding="utf-8")
    if not check:
        CODEX_SKILLS.mkdir(exist_ok=True)

    count = 0
    stale: list[str] = []
    for source, source_text in source_texts:
        name = source.stem
        target_dir = CODEX_SKILLS / name
        target = target_dir / "SKILL.md"
        content = metadata_for(name, source.name, source_text) + codex_body(
            name, source.name, source_text
        )
        if check:
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                stale.append(str(target.relative_to(ROOT)))
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        if needs_runtime(name, source_text):
            reference = target_dir / "references" / "codex-research-runtime.md"
            if check:
                if not reference.exists() or reference.read_text(encoding="utf-8") != runtime:
                    stale.append(str(reference.relative_to(ROOT)))
            else:
                reference.parent.mkdir(exist_ok=True)
                reference.write_text(runtime, encoding="utf-8")
        count += 1

    if check:
        if stale:
            print("Codex skills are out of date:")
            for path in stale:
                print(f"  {path}")
            raise SystemExit(1)
        print(f"Checked {count} Codex skills in {CODEX_SKILLS.relative_to(ROOT)}")
        return

    print(f"Generated {count} Codex skills in {CODEX_SKILLS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
