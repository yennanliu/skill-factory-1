#!/usr/bin/env bash
# Capture the real OTel logs/metrics Claude Code exports while a plugin skill runs, using the
# local collector. Sanitized output goes to tests/fixtures/otel/<claude-version>/.
#
# usage: scripts/probe_otel.sh [model]     (default: claude-haiku-5-5; costs about a cent)
set -euo pipefail

MODEL="${1:-claude-haiku-5-5}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(claude --version | awk '{print $1}')"
DEST="$REPO/tests/fixtures/otel/$VERSION"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/sf-otel.XXXXXX")"  # GNU + BSD compatible
PORT="${SF_PROBE_OTEL_PORT:-14318}"
trap 'kill "${COLLECTOR_PID:-0}" 2>/dev/null || true; rm -rf "$TMP"' EXIT

PLUGIN="$TMP/plugin"; WORK="$TMP/work"; RAW="$TMP/raw/otel"
mkdir -p "$PLUGIN/.claude-plugin" "$PLUGIN/skills/probe-echo" "$WORK" "$RAW"
echo '{ "name": "sf-probe", "version": "0.0.1" }' > "$PLUGIN/.claude-plugin/plugin.json"
cat > "$PLUGIN/skills/probe-echo/SKILL.md" <<'MD'
---
name: probe-echo
description: Contract-probe skill. Use only when the user asks to run the probe-echo skill.
---
1. Run `echo probe-ok` with the Bash tool.
2. Reply with exactly: done
MD

# start the collector as a direct child (so `kill` reaches it) without holding our stdout open
PY="$(cd "$REPO" && uv run python -c 'import sys; print(sys.executable)')"
"$PY" -m skill_factory.telemetry.collector --port "$PORT" --dir "$RAW" > /dev/null 2>&1 &
COLLECTOR_PID=$!
for _ in $(seq 50); do curl -fs "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 0.1; done

set -a
# shellcheck disable=SC1091
source "$REPO/profiles/otel.env"
OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:$PORT"
OTEL_LOGS_EXPORT_INTERVAL=500
OTEL_METRIC_EXPORT_INTERVAL=1000
set +a

for prompt in "Run the probe-echo skill." "/sf-probe:probe-echo"; do   # model- then user-triggered
  ( cd "$WORK" && claude -p "$prompt" \
      --plugin-dir "$PLUGIN" --setting-sources project,local --model "$MODEL" \
      --output-format json --max-turns 6 --max-budget-usd 0.50 \
      --allowedTools "Bash(echo:*)" "Skill" --no-session-persistence < /dev/null > /dev/null )
done
sleep 3  # let the exporters flush

mkdir -p "$DEST"
for f in logs metrics; do
  [ -f "$RAW/$f.jsonl" ] && python3 "$REPO/scripts/probe/sanitize_otel.py" "$RAW/$f.jsonl" "$TMP" > "$DEST/$f.jsonl"
done
echo "fixtures written to $DEST"; ls -la "$DEST"
