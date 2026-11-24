#!/usr/bin/env bash
# Capture real Claude Code hook payloads and stream-json output for a skill invoked
# (a) by the model and (b) by the user typing /<plugin>:<skill>. Output is sanitized and
# written to tests/fixtures/claude/<claude-version>/ so parsers can be tested against it.
#
# usage: scripts/probe_claude.sh [model]     (default model: claude-haiku-5-5; costs a few cents)
set -euo pipefail

MODEL="${1:-claude-haiku-5-5}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(claude --version | awk '{print $1}')"
DEST="$REPO/tests/fixtures/claude/$VERSION"
TMP="$(mktemp -d -t sf-probe)"
trap 'rm -rf "$TMP"' EXIT

PLUGIN="$TMP/plugin"
WORK="$TMP/work"
RAW="$TMP/raw"
mkdir -p "$PLUGIN/.claude-plugin" "$PLUGIN/hooks" "$PLUGIN/skills/probe-echo" "$WORK" "$RAW"

cat > "$PLUGIN/.claude-plugin/plugin.json" <<JSON
{ "name": "sf-probe", "version": "0.0.1", "description": "skill-factory contract probe" }
JSON

cat > "$PLUGIN/skills/probe-echo/SKILL.md" <<'MD'
---
name: probe-echo
description: Contract-probe skill. Use only when the user asks to run the probe-echo skill.
---
1. Run `echo probe-ok` with the Bash tool.
2. Run `ls ./nonexistent-sf-probe` with the Bash tool. It is expected to fail; do not retry.
3. Reply with exactly: done
MD

cp "$REPO/scripts/probe/dump_hook.py" "$PLUGIN/hooks/dump_hook.py"
EVENTS=(SessionStart UserPromptSubmit UserPromptExpansion PreToolUse PostToolUse PostToolUseFailure Stop SessionEnd)
python3 - "$PLUGIN/hooks/hooks.json" "${EVENTS[@]}" <<'PY'
import json, sys
path, events = sys.argv[1], sys.argv[2:]
cmd = 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/dump_hook.py" '
hooks = {e: [{"hooks": [{"type": "command", "command": cmd + e}]}] for e in events}
for e in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
    hooks[e][0]["matcher"] = "*"
json.dump({"hooks": hooks}, open(path, "w"), indent=2)
PY

run() {  # $1 = case name, $2 = prompt
  local name="$1" prompt="$2"
  mkdir -p "$RAW/$name"
  echo ">> probe: $name"
  ( cd "$WORK" && SF_PROBE_OUT="$RAW/$name" claude -p "$prompt" \
      --plugin-dir "$PLUGIN" \
      --setting-sources project,local \
      --model "$MODEL" \
      --output-format stream-json --verbose \
      --max-turns 8 --max-budget-usd 0.50 \
      --allowedTools "Bash(echo:*)" "Bash(ls:*)" "Skill" \
      --no-session-persistence \
      < /dev/null > "$RAW/$name/stream.jsonl" ) || echo "   (claude exited non-zero; keeping output)"
}

run model_triggered "Run the probe-echo skill."
run user_triggered "/sf-probe:probe-echo"

python3 "$REPO/scripts/probe/sanitize.py" "$RAW" "$DEST" "$TMP"
echo "fixtures written to $DEST"
