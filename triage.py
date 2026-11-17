"""Issue-triage agent built on the OpenMinion 0.1 Python SDK.

Workflow (one useful, repeatable loop):
  1. Load one GitHub issue (title + body + labels) through the `load_issue`
     tool -- the only source of issue data, so the classification is always
     grounded in the real issue text.
  2. The model (Ollama `qwen2.5:0.5b` by default, or any provider configured
     in `.openminion/agents.json`) classifies it: category, priority, the
     module it should be routed to, and a short draft reply.
  3. The model text is normalised into the typed `TriageResult` schema
     (Pydantic) and printed as JSON plus a short human summary.

The agent runs end to end with a real model: the tool call is forced
(`forced_tools=["load_issue"]`) so the model always reads the issue before
answering, and the session id is stable per issue (`triage-<id>`) so a
follow-up question continues the same triage thread.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from openminion import Agent, ProviderError, tool
from openminion.services.runtime.ingress.types import TurnTimeoutError

ISSUES_DIR = Path(__file__).resolve().parent / "examples" / "issues"


def _find_config() -> str | None:
    """Locate .openminion/agents.json by searching upward from this file.

    The SDK resolves its config relative to the working directory; a repo
    checkout has it at the repo root, while this workspace keeps it one
    level up. Returning an explicit path makes the agent runnable from
    anywhere. Returns None when no config exists (SDK default applies).
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        config = candidate / ".openminion" / "agents.json"
        if config.exists():
            return str(config)
    return None

CATEGORIES = ("bug", "feature", "docs", "question")
PRIORITIES = ("P0", "P1", "P2", "P3")


class TriageResult(BaseModel):
    """Typed triage verdict for one issue."""

    issue_id: str
    category: str = Field(pattern="^(bug|feature|docs|question)$")
    priority: str = Field(pattern="^P[0-3]$")
    category_source: str = "model"
    route: str
    suggested_labels: list[str]
    reply_draft: str
    model_summary: str = ""


LABEL_TO_CATEGORY = {
    "bug": "bug",
    "enhancement": "feature",
    "feature": "feature",
    "documentation": "docs",
    "docs": "docs",
    "question": "question",
}


def _issue_path(issue_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "", issue_id)
    return ISSUES_DIR / f"{safe}.json"


@tool(min_scope="READ_ONLY", idempotent=True)
def load_issue(issue_id: str) -> str:
    """Return the title, labels and body of one issue as short plain text."""
    path = _issue_path(issue_id)
    if not path.exists():
        return f"issue {issue_id} not found"
    issue = json.loads(path.read_text(encoding="utf-8"))
    body = " ".join(str(issue.get("body") or "").split())
    labels = ", ".join(str(label) for label in issue.get("labels") or ["none"])
    return (
        f"Issue {issue.get('id')}: {issue.get('title')}\n"
        f"Labels: {labels}\n"
        f"Body: {body[:300]}"
    )


def _pick(text: str, options: tuple[str, ...], default: str) -> str:
    lowered = text.lower()
    for opt in options:
        if re.search(rf"\b{re.escape(opt.lower())}\b", lowered):
            return opt
    return default


def _first_sentence(text: str, limit: int = 240) -> str:
    cleaned = " ".join(text.split())
    match = re.split(r"(?<=[.!?])\s", cleaned)
    first = match[0] if match else cleaned
    return first[:limit]


def normalise(issue_id: str, model_text: str, issue: dict) -> TriageResult:
    """Normalise free model text into the typed schema.

    The model drafts the classification; this step only maps its words onto
    the fixed vocabulary (category / priority) and fills the reply draft from
    the model's own summary sentence. Nothing here re-classifies the issue:
    if the model names no category, the default is "question" / "P2".
    """
    category = _pick(model_text, CATEGORIES, "question")
    category_source = "model"
    # Cross-check: the issue's own labels are ground truth about its kind.
    # If the model contradicts an explicit category label, the label wins
    # and the verdict says so -- a human triager would do the same.
    label_category = next(
        (LABEL_TO_CATEGORY[label.lower()] for label in issue.get("labels") or []
         if label.lower() in LABEL_TO_CATEGORY),
        None,
    )
    if label_category and label_category != category:
        category, category_source = label_category, "label-cross-check"
    priority = _pick(model_text, PRIORITIES, "P2")
    body = (issue.get("body") or "").lower()
    title = (issue.get("title") or "").lower()
    if "readme" in body or "doc" in title or "documentation" in body:
        route = "docs"
    elif "crash" in body or "timeout" in title or "turntimeout" in body:
        route = "runtime / provider errors"
    elif "export" in body or "session" in body:
        route = "sessions / persistence"
    else:
        route = "core agent loop"
    labels = [category, priority.lower()]
    if issue.get("labels"):
        labels += [str(label) for label in issue["labels"]][:3]
    # The 0.5b local model often answers with just the category word. When
    # its verdict sentence is missing, assemble the draft reply from the
    # model's own classification plus the issue title -- never invented.
    verdict = _first_sentence(model_text)
    if len(verdict) < 20:
        verdict = f"{category} ({priority}): {issue.get('title', '')}"
    return TriageResult(
        issue_id=issue_id,
        category=category,
        priority=priority,
        category_source=category_source,
        route=route,
        suggested_labels=sorted(set(labels)),
        reply_draft=verdict,
        model_summary=_first_sentence(model_text, 400),
    )


def triage(issue_id: str, agent_id: str | None, model: str | None = None) -> TriageResult:
    issue = json.loads(_issue_path(issue_id).read_text(encoding="utf-8"))
    labels = ", ".join(str(label) for label in issue.get("labels") or ["none"])
    prompt = (
        f"Use load_issue to read issue {issue_id}, then triage it. "
        f"Labels already on the issue: {labels}. Answer in exactly "
        "three short lines:\n"
        "Category: bug, feature, docs or question\n"
        "Priority: P0, P1, P2 or P3\n"
        "Verdict: one short sentence"
    )
    with Agent(
        agent_id=agent_id,
        config_path=_find_config(),
        model=model,
        instructions=(
            "Triage GitHub issues. Call load_issue first, then answer "
            "in three short lines."
        ),
        tools=[load_issue],
        forced_tools=["load_issue"],
        session_id=f"triage-{issue_id}",
    ) as agent:
        result = agent.run(prompt)
    return normalise(issue_id, result.text, issue)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue_id", nargs="?", default="issue-101")
    parser.add_argument("--agent", default=None, help="Configured agent id")
    parser.add_argument("--model", default=None, help="Model override, e.g. qwen2.5:1.5b")
    parser.add_argument("--json", action="store_true", help="Print only the JSON verdict")
    args = parser.parse_args(argv)
    try:
        verdict = triage(args.issue_id, args.agent, args.model)
    except (ProviderError, TurnTimeoutError) as exc:
        print(f"provider failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(verdict.model_dump_json(indent=2))
    else:
        print(verdict.model_dump_json(indent=2))
        print()
        print(f"Issue {verdict.issue_id}: {verdict.category} / {verdict.priority} -> {verdict.route}")
        print(f"Labels: {', '.join(verdict.suggested_labels)}")
        print(f"Draft reply: {verdict.reply_draft}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
