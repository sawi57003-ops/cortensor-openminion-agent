# Cortensor OpenMinion Agent — Issue Triage

A small working agent built with the **OpenMinion 0.1 Python SDK** for
**Cortensor Hackathon #5** (DoraHacks 2418).

It triages one GitHub issue per run: it reads the issue through a tool,
asks a real local model to classify it (category + priority + draft reply),
and prints a typed JSON verdict. One useful, repeatable workflow — no
frontend, no multi-agent system, no paid anything.

- Author: Fngyuan ([@sawi57003-ops](https://github.com/sawi57003-ops))
- Model path: **Ollama, local, free** — `qwen2.5:0.5b` (no account, no key, no card)
- Demo video: `demo/` (see below) — a real model-backed run, recorded live

## What it does

```text
$ ./run_demo.sh
# -> warms up Ollama, then:
$ python triage.py issue-104
{
  "issue_id": "issue-104",
  "category": "bug",
  "priority": "P0",
  "category_source": "model",
  "route": "core agent loop",
  "suggested_labels": ["bug", "p0"],
  "reply_draft": "openminion: Category: bug Priority: P0 Verdict: The issue is a bug.",
  "model_summary": "openminion: Category: bug Priority: P0 Verdict: The issue is a bug."
}

Issue issue-104: bug / P0 -> core agent loop
Labels: bug, p0
Draft reply: openminion: Category: bug Priority: P0 Verdict: The issue is a bug.
```

Workflow, step by step:

1. **Read the issue through a tool.** `load_issue(issue_id)` (a
   `@tool`, `READ_ONLY`, idempotent) returns the issue title, labels and
   body as short plain text. It is the only source of issue data, so the
   classification is always grounded in the real issue text.
2. **The model classifies it.** An `Agent` with
   `forced_tools=["load_issue"]` must call the tool before answering, then
   answers in three short lines: `Category:` (bug / feature / docs /
   question), `Priority:` (P0–P3), `Verdict:` (one sentence).
3. **The answer is normalised into a typed schema.** The free model text
   is mapped onto the fixed `TriageResult` vocabulary (Pydantic). The
   issue's own labels are ground truth about its kind: if the model
   contradicts an explicit category label (`bug`, `enhancement`,
   `documentation`, `question`), the label wins and `category_source`
   records `"label-cross-check"` instead of `"model"` — the same thing a
   human triager would do. The reply draft is the model's verdict sentence,
   or — when the small model answers with just the category word — the
   model's classification plus the issue title, never invented text.
4. **One session per issue.** The session id is `triage-<issue_id>`, so a
   follow-up question continues the same triage thread.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # openminion==0.1.20

# 1. Install Ollama (https://ollama.com) and pull the small model:
ollama pull qwen2.5:0.5b
ollama serve   # keep running in a separate terminal

# 2. Point OpenMinion at it (from the repo root):
openminion setup --provider ollama --model qwen2.5:0.5b --no-chat
#    -- or: openminion config init --provider ollama, then edit
#    .openminion/agents.json (see "Two config tweaks" below)

# 3. Run it (the wrapper warms the model up first -- see below):
./run_demo.sh                 # triages examples/issues/issue-104.json
.venv/bin/python triage.py issue-101   # any bundled example, or your own
# triage.py finds .openminion/agents.json by searching upward from its own
# location, so it also runs from any other working directory.
```

### Two config tweaks you need on a CPU-only machine

1. **Proxy bypass (mandatory in proxied environments).** OpenMinion's
   HTTP client reads `HTTP_PROXY`/`ALL_PROXY` from the environment, and a
   `no_proxy` entry such as `[::1]` breaks localhost calls with
   `InvalidURL: Invalid port: ':1]'`. Run everything with:
   `NO_PROXY='*' no_proxy='*'`. `run_demo.sh` does this for you.
2. **Turn timeout.** The default `runtime.chat_turn_timeout_seconds`
   (90 s) is too tight for a 0.5b model on CPU: the SDK's own
   `examples/sdk/tool_agent.py` takes ~50–95 s per run here, and triage
   runs were killed at exactly 90.00 s. Raise it to 300 in
   `.openminion/agents.json` (`runtime.chat_turn_timeout_seconds`).
   See "What We Learned" below.

## One working example

`examples/issues/issue-104.json` — "Agent loop hangs on empty provider
reply" (label `bug`):

```bash
NO_PROXY='*' no_proxy='*' .venv/bin/python triage.py issue-104
```

Verified live runs (Ollama `qwen2.5:0.5b`, this VM, 17 Nov 2026):

| Run | Result | Time |
|---|---|---|
| 1 | `bug / P2`, full format, no refusal | ~88 s |
| 2 | `bug / P0`, full 3-line format, verdict sentence present | ~61 s |

Three more bundled examples: `issue-101` (timeout crash, labels
`timeout`/`ollama`), `issue-102` (session export feature, label
`enhancement`), `issue-103` (README docs gap, label `documentation`).
Add your own issue as `examples/issues/<id>.json` with fields `id`,
`title`, `body`, `labels` and run `triage.py <id>`.

## OpenMinion SDK workflow (how this agent uses it)

- `Agent(instructions, tools, forced_tools, session_id, model)` — one
  agent, one workflow. `forced_tools=["load_issue"]` guarantees the
  model reads the issue before answering (verifiable first tool call,
  same pattern as the SDK's `examples/sdk/tool_agent.py`).
- `@tool(min_scope="READ_ONLY", idempotent=True)` — the issue reader is
  declared read-only, so the runtime can never let the agent write
  through it.
- `ProviderError` / `TurnTimeoutError` are caught and reported as a
  clean one-line failure instead of a traceback.
- Sessions (`session_id="triage-<id>"`) keep one thread per issue.
- Pydantic schema (`TriageResult`) for the typed verdict, following the
  SDK's `examples/sdk/structured_output.py` pattern (normalised here
  from free text, because the 0.5b model cannot reliably emit raw JSON —
  see "What We Learned").

## Building Blocks Tested

| Building block | Used? | Result |
|---|---|---|
| `Agent` + `forced_tools` | Yes | Works. Model always calls `load_issue` first. |
| `@tool` (read-only, idempotent) | Yes | Works. Plain-text tool results beat raw JSON for the small model (see below). |
| Ollama provider, `qwen2.5:0.5b` | Yes | Works, free, no account. ~60–90 s/run on CPU. Model answers are terse and noisy (priority flips P2↔P0 across runs of the same issue). |
| Ollama provider, `qwen2.5:1.5b` | Yes | **Does not work on this VM.** `TurnTimeoutError` at 90.00 s even on a fresh server; each turn takes 10–30 s. |
| `structured_output` (`output_type=`) | Tested | Not used in the final agent: the 0.5b model cannot reliably emit valid JSON, so the verdict is normalised from free text instead. |
| Sessions (`session_id`) | Yes | Works. Same issue id continues the same thread. |
| `openminion setup --provider ollama` | Yes | Works. Config lands in `.openminion/agents.json`. |
| `openminion status readiness` | Yes | Reports `overall=demo` for echo; not a real provider-readiness claim. |
| Cortensor Portal / Gemma4 | No | Not used — no access granted; Portal is optional for qualification and the agent does not depend on it. |
| Echo provider | Yes (smoke test) | Works, no model. Used only to verify the SDK install. |

## What We Learned

Actionable product feedback for the OpenMinion team (SDK 0.1.20,
Python 3.12, Ollama `qwen2.5:0.5b`, CPU-only VM):

1. **The freshness classifier misfires on small local models and
   derails unrelated tasks.** The SDK runs the *same* tiny model as an
   internal "freshness-routing classifier" on every prompt. It
   classified a plain issue-triage prompt as freshness-sensitive
   (`time_sensitive=true`, `needs_live_data=true`), which injected a
   "require explicit failure wording" obligation — and the model then
   answered with a canned refusal ("I couldn't provide a grounded
   current news/finance answer because Missing live-data evidence for a
   freshness-sensitive answer") instead of triaging. It happened on 3
   of 7 runs before the fix. **Fix that works:** start the prompt with
   a direct tool request ("Use load_issue to read issue …") — the
   classifier has a `skipped_direct_tool_request` bypass for exactly
   that shape, and refusals stopped immediately (0 of 2 runs after).
   **Suggestion:** skip freshness classification when the prompt's
   tool calls already supply the evidence, or run the classifier on a
   capable model instead of the same small one.
2. **The 90 s default turn timeout is too tight for CPU Ollama.**
   The SDK's own `tool_agent.py` example takes 48–95 s with
   `qwen2.5:0.5b` on CPU, so legitimate runs are killed at exactly
   90.00 s. **Suggestion:** raise the default, or scale it to the
   provider (local CPU models need more), or surface a hint naming the
   config key (`runtime.chat_turn_timeout_seconds`) in the timeout
   error.
3. **Small-model tool results should be short plain text, not JSON.**
   Returning the raw issue JSON from the tool made the model ramble or
   refuse; a 3-line plain-text rendering (title / labels / truncated
   body) fixed it. **Suggestion:** document this in the tool-authoring
   guide — tool-result shape matters more than prompt wording for
   small models.
4. **`TurnTimeoutError` is not a `ProviderError`.** Catching only
   `ProviderError` (as the SDK examples do) lets a timeout crash the
   program with a full traceback. It lives at
   `openminion.services.runtime.ingress.types.TurnTimeoutError` and is
   not exported from the top-level `openminion` package.
   **Suggestion:** export it top-level, or make it a `ProviderError`
   subclass, and show it in the error-handling example.
5. **Proxy env vars break localhost providers.** With `HTTP_PROXY` /
   `ALL_PROXY` set and a `no_proxy` entry of `[::1]`, Ollama calls
   fail with `InvalidURL: Invalid port: ':1]'`. `NO_PROXY='*'` fixes
   it. **Suggestion:** the Ollama provider should bypass proxies for
   loopback by default.
6. **Priority classification is noisy on a 0.5b model.** The same
   issue scored P2 in one run and P0 in the next; category is stable,
   priority is not. The agent reports the model's call as-is and never
   smooths it — but a production triage agent should confirm priority
   with a second pass or a larger model.

## Reproducibility

- SDK: `openminion==0.1.20` (PyPI), Python 3.12.3
- Model: Ollama `qwen2.5:0.5b` (397 MB), local, free
- No paid service, no API key, no account needed for the default path
- Demo video: a real run of `./run_demo.sh`, recorded live with
  ffmpeg + Xvfb (see `demo/`), unedited within the run

## License

MIT. OpenMinion SDK itself is Apache-2.0 (OpenMinion).
