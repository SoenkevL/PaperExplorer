"""scripts/skill_nudge.py, the hook that points Claude Code at CLAUDE.md's Agent skills."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "skill_nudge.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("skill_nudge", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "prompt",
    [
        "Add a filter for Citations by year",
        "Implement the Scope picker",
        "The sidebar fails on long Answers",
        "Search got slow after the last migration",
        "Refactor the asking module",
        "Review my branch before I open the PR",
    ],
)
def test_a_request_to_build_fix_design_or_review_gets_the_nudge(prompt: str) -> None:
    nudge = _script().nudge(prompt)
    assert nudge is not None
    assert "## Agent skills" in nudge


@pytest.mark.parametrize(
    "prompt",
    [
        "What does ADR 0003 say?",
        "Thanks, that's all",
        "Where do the eval runs live?",
    ],
)
def test_a_question_or_remark_gets_no_nudge(prompt: str) -> None:
    assert _script().nudge(prompt) is None


def test_the_word_must_stand_alone() -> None:
    # "address" and "padding" contain "add"; "errorless" is not a report of an error.
    assert _script().nudge("What is the address of the padding?") is None


def test_the_hook_prints_the_nudge_for_claude_code_and_exits_cleanly() -> None:
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "Add a Scope picker"})
    done = subprocess.run(
        [sys.executable, str(SCRIPT)], input=payload, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0
    assert "## Agent skills" in done.stdout


def test_the_hook_stays_silent_on_input_it_cannot_read() -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT)], input="not json", capture_output=True, text=True, check=False
    )
    assert done.returncode == 0
    assert done.stdout == ""
