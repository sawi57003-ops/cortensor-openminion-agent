#!/usr/bin/env bash
# Demo entry point: warm up the local Ollama model (so model-load time is
# not charged against the agent turn), then triage one example issue.
# Run from the repo root. Requires: ollama serve running, OpenMinion
# configured for provider ollama / qwen2.5:0.5b (see README.md).
set -euo pipefail
cd "$(dirname "$0")"

export NO_PROXY='*' no_proxy='*'

ISSUE="${1:-issue-104}"

echo "== warming up qwen2.5:0.5b =="
curl -s --max-time 180 http://127.0.0.1:11434/api/chat \
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"Hi"}],"stream":false}' \
  -o /dev/null
echo "== model warm =="
echo
echo "== triaging $ISSUE =="
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif [ -x ../.venv/bin/python ]; then
  PY=../.venv/bin/python
else
  PY=python3
fi
"$PY" triage.py "$ISSUE"
