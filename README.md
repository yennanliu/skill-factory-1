# skill-factory-1

A scaled-down, Uber-style platform for authoring, evaluating, measuring and improving agent skills
(Claude Code first; Codex / OpenCode / Gemini CLI later).

一個縮小版、Uber 風格的 agent skill 平台：撰寫、評測、量測並持續改善 agent skill
（先支援 Claude Code，之後再支援 Codex / OpenCode / Gemini CLI）。

## Docs / 文件
- Design doc (English): [docs/DESIGN.en.md](docs/DESIGN.en.md)
- 設計文件（繁體中文）：[docs/DESIGN.zh-TW.md](docs/DESIGN.zh-TW.md)
- Implementation plan (English): [docs/IMPLEMENTATION_PLAN.en.md](docs/IMPLEMENTATION_PLAN.en.md)
- 實作計畫（繁體中文）：[docs/IMPLEMENTATION_PLAN.zh-TW.md](docs/IMPLEMENTATION_PLAN.zh-TW.md)

## Quickstart / 快速開始
Requires [uv](https://docs.astral.sh/uv/) and Claude Code. 需要 uv 與 Claude Code。

```bash
uv sync                      # create .venv and install deps from uv.lock
uv run sf --help
uv run sf version            # sf, installed Claude Code, pinned contract version
uv run sf spec validate      # validate skills/*/spec.yaml + SKILL.md frontmatter
uv run sf build              # regenerate plugins/ + .claude-plugin/marketplace.json
uv run sf release lc-add --bump patch   # bump skill + plugin version, rebuild
uv run sf lint               # house rules; --list-rules, --changed, --format github, --strict
uv run sf eval lc-add        # run a skill's evals (via `claude plugin eval`), with a no-skill baseline
uv run sf eval --all --runs 3 -j 8   # every skill; results in runs/<skill>/<timestamp>/
uvx pre-commit install       # lint changed skills + check generated files on every commit
scripts/probe_otel.sh        # re-capture OTel payloads after upgrading the CLI (~$0.01)
uv run pytest                # unit + Claude Code contract tests
uv run ruff check . && uv run ruff format --check . && uv run mypy
scripts/probe_claude.sh      # re-capture Claude Code payloads after upgrading the CLI (~$0.01)
```

## Install the skills / 安裝 skill
```
/plugin marketplace add yennanliu/skill-factory-1
/plugin install sf-core@skill-factory
/plugin install sf-domain-leetcode@skill-factory
```

| Tier (`spec.yaml`) | Plugin |
|---|---|
| `always-on` | `sf-platform` |
| `core` | `sf-core` |
| `domain` + `domain: x` | `sf-domain-x` |
| `experimental` | `sf-experimental` |

## Telemetry / 遙測
Two sources, both local-only (`$SF_HOME`, default `~/.skill-factory/`):

1. **Hooks**: install `sf-platform` (`/plugin install sf-platform@skill-factory`). Each skill call, each tool error
   (attributed to the active skill) and each session start/end is appended to `telemetry/events.jsonl`. No
   prompts, tool input or paths are recorded.
2. **OpenTelemetry**: gives the cost per skill.
   ```bash
   uv run sf otel up                 # stdlib collector on 127.0.0.1:4318 -> ~/.skill-factory/otel/
   eval "$(uv run sf otel env)"      # then start claude from this shell
   uv run sf stats --since 1d        # calls, model/user trigger, sessions, cost/call, tool errors
   ```
3. **Report**: `uv run sf report` writes `report/index.html`, one self-contained page (no JS, no CDN). It shows, per
   skill: calls, model/user split, cost/call (mean and p90), tool errors, repos, active weeks, versions in use,
   skills used alongside, a 14-day sparkline, context cost, and the latest eval with its baseline verdict.
4. **Status line**: `uv run sf statusline install [--budget 2]` shows a diff, then sets `statusLine` in
   `~/.claude/settings.json` so it displays `$0.42 · Opus 5.5 · ctx 31% · lc-add`. The cost turns yellow at 50% of
   the session budget and red at 80%. There are no hard caps.

## Evals / 評測
Cases live in `skills/<name>/evals/<case>/case.yaml`, in Claude Code's own
[plugin eval format](https://code.claude.com/docs/en/plugin-evals.md). Shared scaffold scripts go in `evals/_shared/`.
`sf eval` stages the skill as a one-skill plugin and runs `claude plugin eval` with `--no-publish`, `--scaffold`,
the spec's tool grants and a no-plugin **baseline arm**. It then reports, per case and per skill: the pass rate with
a 95% Wilson interval, the baseline pass rate, the mean score, and how often the skill actually fired.
Each skill's status is checked against its spec: `ok`, `below-min` (exit 1) or `too-easy` (above `max_pass_rate`).

## Layout / 結構
| Path | What |
|---|---|
| `src/skill_factory/` | the `sf` CLI |
| `skills/<name>/` | skill sources: `spec.yaml` (source of truth) + `SKILL.md` + references |
| `market.yaml` | marketplace name/owner and per-plugin versions |
| `plugins/`, `.claude-plugin/` | **generated** by `sf build`; never edit by hand |
| `scripts/probe_claude.sh` | records real hook / stream-json payloads into `tests/fixtures/claude/<version>/` |
| `src/skill_factory/lint/rules.py` | lint rules SF001–SF013; waive per skill with `lint_waivers` in `spec.yaml` |
| `hooks/` | telemetry hook (stdlib only) + `hooks.json`, shipped in `sf-platform` via `assets` |
| `src/skill_factory/telemetry/` | OTLP collector, DuckDB ingest, `sf stats` |
| `docs/CLAUDE_CODE_CONTRACT.md` | Claude Code facts we depend on, confirmed by the probe |

## Status / 狀態
Phase 5 (report, status line) — see [the implementation plan](docs/IMPLEMENTATION_PLAN.en.md).
階段 5（報表、status line）— 見[實作計畫](docs/IMPLEMENTATION_PLAN.zh-TW.md)。

## References / 參考
- Uber Engineering — [How Uber Evals 1 Million Agent Skill Invocations a Month](https://www.uber.com/us/en/blog/optimizing-ubers-agent-skills/) (2026-11-23)
- Uber Engineering — [Running a Software Factory Efficiently at Uber Scale](https://www.uber.com/us/en/blog/efficient-software-factory/) (2026-08-27)
- [Agent Skills spec](https://agentskills.io)
