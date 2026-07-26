from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
ROLE_ROOT = ROOT / "templates/roles"
WORKERS = ("explorer", "implementer", "evaluator", "reviewer", "doc-curator")
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def test_runtime_role_templates_remain_english() -> None:
    for path in ROLE_ROOT.glob("*.md"):
        assert not CJK.search(path.read_text(encoding="utf-8")), path


def test_supervisor_defines_language_priority_and_task_contract_handoff() -> None:
    supervisor = (ROLE_ROOT / "supervisor.md").read_text(encoding="utf-8")
    for rule in (
        "an explicit language request in the user's current message",
        "an explicit output-language instruction in `.role-cli-workflow/project_instructions.md`",
        "the language used in the user's latest message",
        "English when the language cannot be determined",
        "`objective`, `deliverables`, `acceptance_criteria`, and task-specific `constraints`",
        "do not create language metadata",
    ):
        assert rule in supervisor


def test_workers_share_language_selection_and_canonical_content_rules() -> None:
    for role in WORKERS:
        content = (ROLE_ROOT / f"{role}.md").read_text(encoding="utf-8")
        assert "explicitly requested human-readable output language" in content
        assert "language of the Task Contract `objective`" in content
        assert "use English when it cannot be determined" in content
        assert "schema and Result Envelope keys" in content
        assert "raw logs, raw diagnostics" in content
    doc_curator = (ROLE_ROOT / "doc-curator.md").read_text(encoding="utf-8")
    assert "documentation target language" in doc_curator


def test_workflow_status_uses_english_canonical_labels() -> None:
    supervisor = (ROLE_ROOT / "supervisor.md").read_text(encoding="utf-8")
    blocks = [
        part.split("[/WORKFLOW_STATUS]", 1)[0]
        for part in supervisor.split("\n[WORKFLOW_STATUS]\n")[1:]
    ]
    assert blocks
    assert sum(line == "[WORKFLOW_STATUS]" for line in supervisor.splitlines()) == 1
    assert sum(line == "[/WORKFLOW_STATUS]" for line in supervisor.splitlines()) == 1
    labels: set[str] = set()
    for block in blocks:
        assert not CJK.search(block)
        for line in block.splitlines():
            if ":" in line:
                labels.add(line.split(":", 1)[0])
    assert {
        "STATE",
        "CURRENT_PHASE",
        "CURRENT_ROLE",
        "TRANSFER",
        "WAITING_FOR",
        "WORKFLOW_ID",
        "TASK_ID",
    } <= labels
    assert "English canonical field labels" in supervisor
    assert "Human-readable values may use the selected output language" in supervisor


def test_bilingual_readmes_have_matching_core_entry_points() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    traditional_chinese = (ROOT / "README.zh-TW.md").read_text(encoding="utf-8")
    switcher = "[English](README.md) | [繁體中文](README.zh-TW.md)"
    for content in (english, traditional_chinese):
        assert switcher in content
        for item in (
            "DIRECT",
            "VERIFY",
            "REVIEW",
            "FULL",
            "role-cli-workflow init",
            "role-cli-workflow doctor",
            "role-cli-workflow open",
            "project_instructions.md",
            "[WORKFLOW_STATUS]",
        ):
            assert item in content
    assert "docs/en/README.md" in english
    assert "docs/en/getting-started.md" not in traditional_chinese.split(
        "## 文件", 1
    )[1].split("## 語言行為", 1)[0]
    assert "docs/zh-TW/README.md" in traditional_chinese
    assert "docs/zh-TW/getting-started.md" in traditional_chinese
    assert "docs/zh-TW/workflow-routing.md" in traditional_chinese
    assert "There is no CLI language option" in english
    assert "目前沒有 CLI language option" in traditional_chinese


def test_no_language_configuration_or_runtime_metadata_was_added() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src").rglob("*.py")
    )
    assert "--language" not in source
    assert "--locale" not in source
    assert re.search(r"""[\"']language[\"']\s*:""", source) is None
    assert re.search(r"""[\"']locale[\"']\s*:""", source) is None
