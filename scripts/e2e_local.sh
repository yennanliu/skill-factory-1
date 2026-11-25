#!/usr/bin/env bash
# End-to-end check of skill-factory against the real Claude Code CLI, on this machine.
# Everything runs in throwaway dirs (SF_HOME, a copy of the repo, and a throwaway
# CLAUDE_CONFIG_DIR for the plugin-install step), so nothing is installed into your real
# ~/.claude and ~/.skill-factory is never touched. Model calls use your normal Claude Code login
# (a fresh config dir has no login) and load plugins only via --plugin-dir.
#
# usage: scripts/e2e_local.sh            (~3-5 min, ~$0.50 of Haiku/Opus usage)
#        E2E_SKIP_PAID=1 scripts/e2e_local.sh   (no model calls: static + install checks only)
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/sf-e2e.XXXXXX")"
export SF_HOME="$TMP/sfhome"
PORT="${E2E_OTEL_PORT:-14399}"
MODEL="${E2E_MODEL:-claude-haiku-5-5}"
PASSED=() FAILED=()

cleanup() {
  # stop our collector by pid first (before its repo copy disappears), then remove everything
  local pidf="$SF_HOME/otel/collector.pid"
  [ -f "$pidf" ] && kill "$(cut -d' ' -f1 "$pidf")" 2>/dev/null
  git -C "$REPO" worktree remove --force "$WORK" >/dev/null 2>&1
  rm -rf "$TMP"
}
trap cleanup EXIT

step() {  # step "<name>" <command...>: run, record pass/fail, keep going
  local name="$1"; shift
  printf '\n== %s\n' "$name"
  if "$@"; then PASSED+=("$name"); echo "   ok"; else FAILED+=("$name"); echo "   FAILED"; fi
}
paid() { [ -z "${E2E_SKIP_PAID:-}" ]; }

# A clean copy of the committed tree (what a fresh clone would have).
WORK="$TMP/repo"
git -C "$REPO" worktree add -q --detach "$WORK" HEAD
cd "$WORK" || exit 1

step "uv sync --locked" uv sync --locked -q
step "pytest" uv run pytest -q -x -n auto
step "ruff + mypy" bash -c 'uv run ruff check . && uv run ruff format --check . >/dev/null && uv run mypy >/dev/null'
step "sf spec validate / build --check / compile --check / lint" bash -c \
  'uv run sf spec validate >/dev/null && uv run sf build --check && uv run sf compile --check && uv run sf lint'
step "claude plugin validate" bash -c 'claude plugin validate . >/dev/null && for p in plugins/*; do claude plugin validate "$p" >/dev/null || exit 1; done'

install_all() {
  local CLAUDE_CONFIG_DIR="$TMP/claude-config"  # isolated: plugin commands need no login
  export CLAUDE_CONFIG_DIR
  claude plugin marketplace add "$WORK" >/dev/null || return 1
  for p in plugins/*; do claude plugin install "$(basename "$p")@skill-factory" >/dev/null || return 1; done
  claude plugin list | grep -c "@skill-factory" | grep -qx "$(ls plugins | wc -l | tr -d ' ')"
}
step "marketplace add + install every plugin (isolated config)" install_all

search_works() {
  uv run sf search "review my python code for bugs" --json | python3 -c \
    'import json,sys; assert json.load(sys.stdin)[0]["skill"]=="python-code-reviewer"' &&
  python3 plugins/sf-platform/skills/find-skills/scripts/find_skills.py "file a leetcode solution" |
    python3 -c 'import json,sys; assert json.load(sys.stdin)[0]["skill"]=="lc-add"'
}
step "sf search + bundled find_skills.py" search_works

papercut_flow() {
  python3 skills/log-papercut/scripts/log_papercut.py --skill sf-core:python-code-reviewer \
    --tried "e2e" --expected "ok" --happened "e2e test papercut" --severity low >/dev/null &&
  uv run sf papercuts list | grep -q "e2e test papercut"
}
step "log_papercut.py -> sf papercuts list" papercut_flow

install_codex() {
  local proj="$TMP/codex-proj"; mkdir -p "$proj" && git -C "$proj" init -q
  uv run sf install --agent codex --dest "$proj/.codex/skills" lc-coach >/dev/null || return 1
  [ -f "$proj/.codex/skills/lc-coach/SKILL.md" ] || return 1
  if command -v codex >/dev/null; then
    (cd "$proj" && codex debug prompt-input 2>/dev/null) | grep -q "lc-coach"
  fi
}
step "sf install --agent codex (+ Codex sees it, if installed)" install_codex

if paid; then
  live_session() {
    uv run sf otel up --port "$PORT" >/dev/null || { echo "   sf otel up failed"; return 1; }
    eval "$(uv run sf otel env --port "$PORT")"
    export OTEL_LOGS_EXPORT_INTERVAL=500
    local w="$TMP/session"; mkdir -p "$w"
    printf 'def add(a, b):\n    return a+b\n\ndef avg(xs):\n    return sum(xs)/len(xs)\n' > "$w/calc.py"
    (cd "$w" && claude -p "Use the python-code-reviewer skill to review calc.py in at most 8 lines." \
      --plugin-dir "$WORK/plugins/sf-platform" --plugin-dir "$WORK/plugins/sf-core" \
      --setting-sources project,local --model "$MODEL" --output-format json --max-turns 8 \
      --max-budget-usd 0.5 --allowedTools Read Skill --no-session-persistence </dev/null >/dev/null) \
      || { echo "   claude -p failed"; return 1; }
    sleep 4  # let the exporters flush
    unset CLAUDE_CODE_ENABLE_TELEMETRY OTEL_LOGS_EXPORTER OTEL_METRICS_EXPORTER OTEL_EXPORTER_OTLP_ENDPOINT
    grep -q '"skill":"python-code-reviewer"' "$SF_HOME/telemetry/events.jsonl" || { echo "   no hook event"; return 1; }
    uv run sf stats --since 1h | grep -q "python-code-reviewer" || { echo "   not in sf stats"; return 1; }
    uv run sf report --out "$TMP/report.html" --since 1h >/dev/null && grep -q "python-code-reviewer" "$TMP/report.html" || return 1
    uv run sf analyze --since 1h >/dev/null
  }
  step "live session: hooks + OTel -> sf stats / report / analyze" live_session

  step "sf eval (real claude plugin eval run)" uv run sf eval python-code-reviewer \
    --case read-only-review --runs 1 --no-baseline --model "$MODEL" --budget 0.5

  step "sf agent run weekly-skill-digest" uv run sf agent run weekly-skill-digest

  new_and_compile() {
    uv run sf new e2e-demo --goal "Explain a cron expression field by field with next run times." >/dev/null || return 1
    python3 - <<'PY' || return 1
p = "skills/e2e-demo/spec.yaml"; s = open(p).read()
s = s.replace('"TODO: a request that should trigger this skill"', '"what does this cron expression mean?"')
s = s.replace('"TODO: what the skill produces"', '"a field-by-field explanation and the next 3 run times"')
open(p, "w").write(s)
PY
    rm -rf skills/e2e-demo/evals/smoke
    uv run sf compile e2e-demo --apply >/dev/null && uv run sf lint e2e-demo && uv run sf build >/dev/null &&
      claude plugin validate plugins/sf-experimental >/dev/null
  }
  step "sf new + sf compile (Opus) -> lint-clean + valid plugin" new_and_compile
else
  echo; echo "(E2E_SKIP_PAID set: skipped live session, eval, managed agent and compile)"
fi

printf '\n== summary: %d passed, %d failed\n' "${#PASSED[@]}" "${#FAILED[@]}"
for f in "${FAILED[@]}"; do echo "   FAILED: $f"; done
[ "${#FAILED[@]}" -eq 0 ]
