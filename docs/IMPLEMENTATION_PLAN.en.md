# Implementation Plan — skill-factory-1

> Builds the system described in [DESIGN.en.md](./DESIGN.en.md).
> 中文版：[IMPLEMENTATION_PLAN.zh-TW.md](./IMPLEMENTATION_PLAN.zh-TW.md)
>
> Written 2026-11-24 against Claude Code 2.1.331, uv 0.11.8, Python 3.14, Docker 29.

---

## 0. Scope

**Goal:** a working loop for a single developer: *write spec → compile skill → lint → eval → install →
use → measure → papercut → fix*, for 5–6 real skills, in about 4–5 weeks of part-time work.

**In scope**
- `sf` CLI (Python, managed with **uv**)
- A Claude Code plugin marketplace in this repo (tiers = plugins)
- Telemetry via hooks + OpenTelemetry, stored locally
- Eval harness on headless `claude -p` (golden, trajectory, judged, ablation)
- Static HTML report, session analyzer, status line
- CI on GitHub Actions

**Out of scope (for now)**
- Multi-user servers, a hosted dashboard, auth
- Codex / OpenCode / Gemini CLI install targets (Phase 9, after Claude Code works end to end)
- A/B testing between live skill versions, task router

---

## 1. Corrections to the design doc (from checking the current Claude Code docs)

These change how parts of the design are built. The design doc stays as the "what"; this plan is the "how".

| Design doc said | Actual (Claude Code 2.1.x) | Effect on plan |
|---|---|---|
| Catch skill calls with a `PostToolUse` hook on `Skill` | That only fires when **Claude** calls the skill. A user typing `/skill` goes through the **`UserPromptExpansion`** hook (`command_name: "plugin:skill"`) | Telemetry hook handles both events; `trigger: model\|user` comes from which event fired |
| Cost per call = session-cost snapshot after − before | OTel `claude_code.api_request` events carry `cost_usd` **and `skill.name`** when a skill is active | Cost per skill = sum of `cost_usd` grouped by `skill.name`. Snapshot diff becomes a fallback only |
| Plugin/skill names in telemetry | Non-official plugins are **redacted to `third-party`** unless `OTEL_LOG_TOOL_DETAILS=1` | Our env profile sets `OTEL_LOG_TOOL_DETAILS=1` (it also adds tool params — keep the collector local) |
| Session analyzer reads transcripts in `~/.claude/projects/` | The transcript format is **internal and can change**; kept 30 days | Analyzer reads OTel logs + stream-json from eval runs first; transcript parsing is best effort, version-guarded, behind one adapter |
| Write telemetry next to the plugin | `${CLAUDE_PLUGIN_ROOT}` changes on every update | All state goes to `${CLAUDE_PLUGIN_DATA}` (or `$SF_HOME`) |
| `.mcp.json` blocked in skills | Plugins *can* ship `.mcp.json` | Keep it blocked by our linter (cost reason from Uber), not by Claude Code |
| Clean-room runs | `--bare` skips auto-discovery (hooks, plugins, MCP, CLAUDE.md, memory), and **plugin hooks are skipped under `--bare` even with `--plugin-dir`** | Eval runs use `--bare --plugin-dir`; telemetry for evals comes from stream-json, not hooks |
| Plugin updates | `version` in `plugin.json` must be bumped or users don't get the update | `sf release` bumps plugin versions; CI checks it |

Still unconfirmed, settled by the Phase 0 probe: the `Skill` tool's input field names, the exact
stream-json `result` fields (`num_turns`, `is_error`, `subtype`), the token-usage location in transcripts.

---

## 2. Tech stack decisions

| Concern | Choice | Why |
|---|---|---|
| Package / env manager | **uv** (`uv init --package`, `uv add`, `uv run`, `uv sync --locked`, `uv tool install`) | Fast, one lockfile, no separate venv/pip/pipx steps |
| Python | `>=3.12`, pinned with `.python-version` = `3.12` for CI; works on local 3.14 | Wide support for deps; uv installs the interpreter |
| CLI | `typer` + `rich` | Subcommands, help text, nice tables |
| Schemas | `pydantic` v2 | Validate `spec.yaml`, eval cases, events; generate JSON Schema for editors |
| YAML | `ruamel.yaml` | Round-trips comments (`sf release` edits files in place) |
| Templates | `jinja2` | `SKILL.md` compile scaffold, HTML report |
| Analytics | `duckdb` | Query JSONL telemetry and run outputs directly with SQL, no DB server |
| Claude calls (compile, judge) | `anthropic` SDK | Direct Messages API for compile/judge; agent runs use the `claude` CLI |
| Agent runs (evals) | `claude -p` subprocess | Same binary users run; stream-json gives trajectory + cost |
| Lint / format / types | `ruff`, `mypy` (dev) | |
| Tests | `pytest`, `pytest-xdist`, `syrupy` (snapshot) | |
| Hooks | **stdlib-only Python**, run with `python3` | Hooks run on every tool call; no venv startup, no deps |
| OTel collector | `otel/opentelemetry-collector-contrib` in Docker, `file` exporter → JSONL | Local, no SaaS; DuckDB reads the files |
| Clean room | Docker image with Node + Claude Code + uv | Empty `$HOME`, pinned CLI version |
| CI | GitHub Actions + `astral-sh/setup-uv` | |

### uv bootstrap (Phase 0)
```bash
cd ~/skill-factory-1
uv init --package --name skill-factory --python 3.12 .
uv add typer rich pydantic ruamel.yaml jinja2 duckdb anthropic
uv add --dev pytest pytest-xdist syrupy ruff mypy
# entry point in pyproject.toml:
#   [project.scripts]
#   sf = "skill_factory.cli:app"
uv run sf --help
uv tool install --editable .     # makes `sf` available globally while developing
```
Rules: commit `uv.lock`; CI uses `uv sync --locked`; never `pip install`; scripts inside skills that need
deps use PEP 723 inline metadata and run with `uv run --script` (stdlib-only when they're on a hot path).

---

## 3. Target repo layout

```
skill-factory-1/
├── pyproject.toml / uv.lock / .python-version
├── src/skill_factory/
│   ├── cli.py                  # typer app: sf <cmd>
│   ├── models/                 # pydantic: spec.py, eval_case.py, event.py, run.py
│   ├── spec/                   # load, validate, hash
│   ├── compile/                # spec → SKILL.md + eval drafts (anthropic SDK)
│   ├── lint/                   # rules/*.py, one rule per file, registry
│   ├── market/                 # marketplace.json / plugin.json generation, release bumps
│   ├── eval/
│   │   ├── runner.py           # claude -p subprocess, N runs, parallel
│   │   ├── stream.py           # stream-json parser → Trajectory
│   │   ├── graders/            # golden.py, trajectory.py, judge.py
│   │   ├── ablation.py
│   │   └── sandbox.py          # local worktree / docker backends
│   ├── telemetry/              # ingest JSONL + OTel files into duckdb views
│   ├── analyze/                # session anti-pattern detectors
│   ├── report/                 # jinja2 HTML report
│   └── papercuts/              # triage, group, promote to eval case
├── hooks/                      # stdlib-only, copied into the platform plugin
│   ├── sf_telemetry.py
│   └── sf_statusline.py
├── skills/                     # source of truth, one dir per skill
│   └── <name>/{spec.yaml, SKILL.md, scripts/, references/, evals/{golden,real,judged}/, fixtures/}
├── plugins/                    # GENERATED by `sf build` — do not hand-edit
│   ├── sf-platform/            # hooks + log-papercut + find-skills (always-on)
│   ├── sf-core/
│   └── sf-domain-<name>/
├── .claude-plugin/marketplace.json   # GENERATED
├── profiles/{default.yaml, otel.env}
├── docker/{eval.Dockerfile, otel-collector.yaml, compose.yaml}
├── tests/                      # unit + snapshot + an opt-in live suite
├── docs/
└── .github/workflows/{ci.yml, nightly-evals.yml}
```

Key choice: **`skills/` is the source, `plugins/` is a build output.** `sf build` copies each skill into
the plugin for its tier and regenerates `marketplace.json` / `plugin.json`. One skill, one directory, one
tier field — no symlinks (they break on Windows and in some installers).

---

## 4. Data contracts

Define these in `src/skill_factory/models/` first; everything else depends on them. Export JSON Schema with
`sf schema` so editors validate `spec.yaml` and eval YAML.

### 4.1 `spec.yaml`
```python
class Spec(BaseModel):
    name: str                      # kebab-case, == dir name
    version: SemVer
    tier: Literal["always-on", "core", "domain", "experimental"]
    domain: str | None = None      # required when tier == "domain"
    owner: str
    goal: str
    triggers: list[str]            # → description
    outputs: list[str]
    tools: Tools                   # required / forbidden / scripts
    targets: Targets               # agents, models (first = default)
    budget: Budget                 # max_body_lines=500, target_cost_usd
    evals: EvalPolicy              # min_pass_rate=0.7, max_pass_rate=0.95, runs=5
    side_effects: Literal["none", "local", "external"] = "none"   # external ⇒ evals must mock/dry-run
```

### 4.2 Telemetry event (`events.jsonl`, written by hooks)
```json
{"v":1,"ts":"…","event":"skill_invoked","session_id":"…","skill":"lc-add","plugin":"sf-domain-leetcode",
 "skill_version":"1.3.0","trigger":"model|user","model":"…","cwd_hash":"sha256:…"}
```
Other events: `session_start`, `session_end`, `tool_error` (from `PostToolUseFailure`), `papercut`.
Never: prompt text, file contents, raw paths.

### 4.3 Eval case (`skills/<n>/evals/**/*.yaml`)
```python
class EvalCase(BaseModel):
    id: str
    kind: Literal["golden", "trajectory", "judged"]
    prompt: str
    fixture: str | None            # dir under skills/<n>/fixtures/
    mocks: dict[str, Mock] = {}    # script path → stdout/exit code
    expect: GoldenExpect | None
    expect_trajectory: TrajectoryExpect | None
    rubric: str | None             # judged
    reference_grade: int | None    # author's grade, for judge calibration
    tags: list[str] = []
```

### 4.4 Run record (`runs/<date>/<skill>/<case>/<n>.json`)
`{case_id, skill, skill_version, model, arm: with|without|minus:<section>, passed, score, cost_usd,
turns, tool_calls:[{name, ok}], duration_ms, cli_version, stream_path}`

---

## 5. Phases

Each phase = one branch + one PR, ends with a demo you can run. Estimates are focused days.

### Phase 0 — Bootstrap & probe (1 day)
**Tasks**
1. `uv init` + deps (section 2), `ruff`/`mypy`/`pytest` config in `pyproject.toml`, `sf --help` works.
2. **Probe script** `scripts/probe_claude.sh`: installs a throwaway plugin with a hook that dumps stdin for
   `PostToolUse(Skill)`, `UserPromptExpansion`, `SessionStart`, `Stop`, `PostToolUseFailure`; runs
   `claude -p ... --output-format stream-json --verbose` once with a model-triggered skill and once with
   `/skill`. Save outputs to `tests/fixtures/claude/2.1.331/`.
3. Write down the confirmed field names in `docs/CLAUDE_CODE_CONTRACT.md` (Skill input fields, stream-json
   `result` fields). These fixtures become the parser tests.
4. Copy in the first skills as-is: `lc-add`, `lc-coach` (from `~/.claude/skills/`),
   `python-code-reviewer`, `fastapi-master` (from `ai_experiment/claude_code/skills_poc/`).

**Done when:** `uv run sf --help` runs; probe fixtures are committed; four skills are under `skills/`.

### Phase 1 — Specs, build, marketplace (2 days)
**Tasks**
1. `models/spec.py` + `sf spec validate [skill|--all]`.
2. Hand-write `spec.yaml` for the four skills (reverse-engineered from their current `SKILL.md`).
3. `sf build`: copy `skills/*` into `plugins/sf-<tier>/skills/`, generate `plugin.json` per plugin and
   `.claude-plugin/marketplace.json` (`name`, `owner.name`, `plugins[]` with `source: "./plugins/sf-core"`).
4. `sf release <skill> --bump patch|minor|major`: bumps the skill version and its plugin's `version`.
5. Spec hash: `sf build` writes `<!-- sf:spec-sha256=… -->` at the bottom of each built `SKILL.md`.

**Done when:**
```bash
uv run sf build
claude plugin marketplace add ~/skill-factory-1   # or /plugin marketplace add inside Claude Code
claude plugin install sf-core@skill-factory
```
installs, and the skills show up in Claude Code. (Check the exact `claude plugin` subcommands against
`claude plugin --help` for the installed version.)

### Phase 2 — Linter (1–2 days)
**Tasks**
1. Rule registry: each rule is a function `(SkillDir) -> list[Finding]` with id, severity, fix hint.
2. Rules (from design §6): frontmatter, description length/“when” wording, body ≤ 500 lines, numbered
   steps, per-step verification, concrete command blocks, error-recovery table, no `.mcp.json`, no secrets
   (regex; optional `gitleaks` if installed), no shared `/tmp/...` paths, referenced scripts exist and are
   executable, spec↔SKILL hash match, `side_effects: external` ⇒ at least one mocked eval.
3. `sf lint [skill|--all] [--format text|json|github]` (`github` prints `::error file=…` annotations).
4. pre-commit hook via `.pre-commit-config.yaml` calling `uv run sf lint --changed`.

**Done when:** every skill lints clean or has a tracked waiver in `spec.yaml` (`lint_waivers: [rule-id]`).
Unit tests: one passing and one failing fixture per rule.

### Phase 3 — Telemetry (2 days)
**Tasks**
1. `hooks/sf_telemetry.py` (stdlib only, < 50 ms): reads stdin, maps
   `PostToolUse` with `tool_name == "Skill"` → `skill_invoked, trigger=model`;
   `UserPromptExpansion` → `skill_invoked, trigger=user`;
   `PostToolUseFailure` → `tool_error` (with the active skill if known);
   `SessionStart` / `SessionEnd` → session events.
   Appends to `${CLAUDE_PLUGIN_DATA}/events.jsonl` (fallback `$SF_HOME/telemetry/`). Never blocks or fails
   the session: catch everything, exit 0.
2. Ship it in `plugins/sf-platform/hooks/hooks.json` using `${CLAUDE_PLUGIN_ROOT}/hooks/sf_telemetry.py`.
3. OTel: `profiles/otel.env` (`CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_LOGS_EXPORTER=otlp`,
   `OTEL_METRICS_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`,
   `OTEL_LOG_TOOL_DETAILS=1`); `docker/compose.yaml` runs the collector with a file exporter to
   `~/.skill-factory/otel/*.jsonl`. `sf otel up|down|status`.
4. `telemetry/ingest.py`: DuckDB views over `events.jsonl` and OTel files: `skill_calls`, `api_requests`
   (with `skill_name`, `cost_usd`, tokens), `tool_results`.

**Done when:** after a normal day of work, `uv run sf stats --since 1d` prints calls, cost/call (from OTel
`skill.name`), and model-vs-user trigger counts per skill.
Tests: replay the Phase 0 probe fixtures through the hook; snapshot the JSONL output.

### Phase 4 — Eval harness: golden + trajectory (4–5 days)
**Tasks**
1. `eval/stream.py`: parse stream-json into a `Trajectory` (ordered tool calls with name, input summary,
   ok/error, plus `result.total_cost_usd`, `usage`). Tested against Phase 0 fixtures.
2. `eval/sandbox.py` — **local** backend: copy the fixture into a temp dir, `git init` it, run
   ```bash
   claude -p "$PROMPT" --bare --plugin-dir <built plugin> \
     --output-format stream-json --verbose \
     --model <model> --max-turns <n> --max-budget-usd <cap> \
     --permission-mode acceptEdits --no-session-persistence
   ```
   with `cwd` = temp dir. Mocks: put stub scripts first on `PATH` / replace `scripts/*` in the temp copy.
3. Graders: `golden.py` (files created, contents/regex, README rows), `trajectory.py` (must-call skill,
   ordered subsequence match, forbidden tools, max turns).
4. `runner.py`: N runs per case (default from spec), parallel with a semaphore (`--jobs`), writes run
   records; `sf eval <skill> [--suite golden|trajectory] [--runs N] [--model M]`.
5. Output: pass rate per case and per skill with a Wilson 95% interval; flag `> max_pass_rate`
   ("eval too easy") and `< min_pass_rate`.
6. Write evals: `lc-add` 3 golden + 6 trajectory; `lc-coach` 5 trajectory; the two `skills_poc` skills
   3 trajectory each. Real prompts come from your own history (copy, anonymize).

**Done when:** `uv run sf eval --all --runs 3` finishes in < 15 min and < $5, and prints a pass-rate table.
Cost guard: `--max-budget-usd` per run and a total `--budget` per invocation that aborts when exceeded.

### Phase 5 — Report & status line (2 days)
**Tasks**
1. `sf report [--out report/index.html]`: per skill — calls, users/repos, retention (used in ≥2 weeks),
   cost/call mean & p90, turns, tool errors, versions, co-use, latest eval pass rate, ablation verdict
   (when present). Jinja2 + inline CSS + a small chart lib from a CDN.
2. `hooks/sf_statusline.py`: reads statusLine JSON, prints
   `$<cost.total_cost_usd> · <model.display_name> · ctx <context_window.used_percentage>% · <last skill>`.
   Last skill comes from the tail of `events.jsonl` for this `session_id`. `sf statusline install` writes
   the `statusLine` entry to `~/.claude/settings.json` (after showing the diff).
3. Budget nudges: `profiles/default.yaml: daily_budget_usd`; status line turns yellow at 50%, red at 80%,
   `!` at 100% (no hard cap — Uber's choice).

**Done when:** the report opens and matches `sf stats`; the status line shows live cost.

### Phase 6 — Spec compiler & `sf new` (2–3 days)
**Tasks**
1. `sf new <name> --tier domain --domain leetcode`: scaffold dir + `spec.yaml` template.
2. `sf compile <skill>`: prompt = spec + lint rules (as a checklist) + 2 exemplar skills; call the Messages
   API (default `claude-opus-5-5` for authoring); write `SKILL.md` and draft `evals/trajectory/*.yaml`;
   then run `sf lint` and loop up to 2 times feeding back failures.
3. Never overwrite silently: write to `SKILL.md.new`, show a diff, `--apply` to accept.
4. `sf compile --all --check`: CI step that fails if a spec changed but its hash in `SKILL.md` didn't.

**Done when:** a brand-new skill goes from `sf new` to lint-clean, evaluated and installed in < 30 min.

### Phase 7 — Judge, ablation, clean room (4–5 days)
**Tasks**
1. `graders/judge.py`: blind pairwise judge (randomized A/B, the judge never sees which arm is which),
   rubric from the case, returns `{winner, reasons}`. Calibration: `sf judge calibrate <skill>` compares
   judge grades with `reference_grade` and reports agreement (Cohen's κ); warn if κ < 0.6.
2. `ablation.py`: arms `with`, `without` (skill not installed), `minus:<section>` (built variants with one
   `##` section removed). Same prompts, same model, N runs each. Output: win rate, Δcost, Δturns,
   Δtool-errors, and the verdict table from design §7.3 (keep / optimize / shorten / retire / delete section).
3. `sandbox.py` — **docker** backend: `docker/eval.Dockerfile` (node LTS, `@anthropic-ai/claude-code`
   pinned to the version in `.claude-version`, uv, git, python). Run with only the fixture and built plugin
   mounted, `HOME=/tmp/home`, `ANTHROPIC_API_KEY` from env. `sf eval --sandbox docker`.
4. Model benchmark: `sf eval <skill> --models haiku,sonnet,opus` → picks the cheapest model meeting
   `min_pass_rate`, proposes a `targets.models` reorder in a PR.

**Done when:** every core skill has an ablation verdict in the report; at least one "works only on my laptop"
issue found or explicitly none.

### Phase 8 — Papercuts, find-skills, session analyzer (3 days)
**Tasks**
1. `skills/log-papercut` (always-on, in `sf-platform`): appends a structured line to
   `${CLAUDE_PLUGIN_DATA}/papercuts.jsonl` via a tiny stdlib script (the skill calls the script; the model
   doesn't hand-write JSON).
2. `sf papercuts list|group|show`; `sf papercuts promote <id>` → creates an eval case from the papercut and
   opens a branch.
3. `skills/find-skills` (always-on): runs `sf search "<task>" --json` (spec text match + quality score
   from the report data) and suggests install commands.
4. `sf analyze [--since 7d]`: detectors (design §9.1) over OTel `api_request`/`tool_result` events first;
   transcript adapter optional, guarded by CLI version. Output: top sessions by cost with anti-patterns and
   a suggested fix.

**Done when:** a papercut filed in a real session ends up as a failing eval case, then a fix PR, then a passing
eval; `sf analyze` produces a weekly report.

### Phase 9 — CI, nightly, managed agents, other agents (3–4 days)
**Tasks**
1. `ci.yml` (on PR): `uv sync --locked` → `ruff` → `mypy` → `pytest` → `sf spec validate --all` →
   `sf build --check` (generated files up to date) → `sf lint --all --format github` →
   `sf compile --all --check` → golden evals for changed skills (needs `ANTHROPIC_API_KEY` secret; skipped
   on forks) → PR comment with pass-rate deltas vs `main`.
2. `nightly-evals.yml`: full trajectory suite on the default model, ablation weekly, uploads the report as an
   artifact.
3. Managed agent example: a scheduled workflow running one skill headless with an outcome metric
   (e.g. `job-apply-104` in **dry-run**, or a weekly `sf analyze` digest).
4. `sf install --agent codex|opencode|gemini`: copy skills into each agent's skill dir with agent-specific
   frontmatter. Verify each agent's current skill location before implementing.

**Done when:** a PR that breaks a skill gets a red check with the failing case; nightly report is published.

### Timeline
| Week | Phases |
|---|---|
| 1 | 0, 1, 2 |
| 2 | 3, start 4 |
| 3 | finish 4, 5 |
| 4 | 6, 7 |
| 5 | 8, 9 |

---

## 6. CLI reference (target)

| Command | Purpose |
|---|---|
| `sf new <name>` | Scaffold a skill + spec |
| `sf spec validate` | Validate spec(s) |
| `sf compile <skill> [--apply\|--check]` | Spec → SKILL.md + eval drafts |
| `sf lint [--changed\|--all]` | Run lint rules |
| `sf build [--check]` | Generate plugins/ and marketplace.json |
| `sf release <skill> --bump …` | Bump skill + plugin version |
| `sf eval <skill> [--suite][--runs][--models][--sandbox local\|docker][--budget]` | Run evals |
| `sf eval --ablate <skill>` | Ablation |
| `sf judge calibrate <skill>` | Judge vs author agreement |
| `sf stats [--since]` | Quick telemetry table |
| `sf report` | HTML report |
| `sf analyze` | Session anti-patterns |
| `sf papercuts …` | Triage / promote papercuts |
| `sf search "<task>"` | Find skills |
| `sf otel up\|down\|status` | Local OTel collector |
| `sf statusline install` | Install status line |
| `sf schema` | Export JSON Schemas |

---

## 7. Testing strategy

| Layer | What | How |
|---|---|---|
| Unit | Models, lint rules, graders, stream parser, detectors | `pytest`, fixtures in `tests/fixtures/` |
| Contract | Our parsers vs real Claude Code output | Probe fixtures per CLI version (`tests/fixtures/claude/<version>/`); re-probe when bumping `.claude-version` |
| Snapshot | Generated `plugin.json`, `marketplace.json`, compiled scaffolds, report HTML | `syrupy` |
| Integration (no API) | `sf build` → `claude plugin validate` (if available) | CI |
| Live (opt-in) | Real `claude -p` eval of one tiny skill | `pytest -m live`, needs `ANTHROPIC_API_KEY`, nightly only |

Run everything with `uv run pytest -n auto`; live tests with `uv run pytest -m live`.

---

## 8. Cost & safety guardrails

- Every `claude -p` run has `--max-turns` and `--max-budget-usd`; every `sf eval` invocation has `--budget`.
- Default eval model is the cheapest in `targets.models` that has passed before; Opus only for authoring and
  judging.
- Skills with `side_effects: external` (e.g. `job-apply-104` submitting real applications) can only run evals
  against mocks or a `dry_run` flag; the linter enforces it and the runner refuses otherwise.
- No credentials in skills, specs, fixtures or telemetry (lint rule + `.gitignore`); the 104 login stays in
  the user's keychain/env.
- `OTEL_LOG_TOOL_DETAILS=1` adds tool parameters to OTel logs: the collector writes only to local disk,
  never to a remote endpoint, and `~/.skill-factory/` is outside the repo.

---

## 9. Decisions log

| # | Decision | Alternatives | Reason |
|---|---|---|---|
| D1 | uv for everything Python | pip+venv, poetry | User requirement; fastest, single lockfile |
| D2 | `skills/` source, `plugins/` generated | Skills directly in plugin dirs | One tier field moves a skill; no symlinks |
| D3 | Agent runs via `claude -p` CLI | Agent SDK | Same binary as real use; SDK can be added later behind the same runner interface |
| D4 | Hooks are stdlib-only | `uv run --script` hooks | Hooks run on every tool call; startup time matters |
| D5 | DuckDB over JSONL | SQLite ingest, Postgres | No ingest step, SQL over files |
| D6 | OTel `skill.name` for cost | Snapshot diff | Built in and exact per request |
| D7 | Local collector, no SaaS | Honeycomb/Grafana Cloud | Privacy; single user |
| D8 | Visibility + nudges, no hard caps (except eval budgets) | Hard caps | Uber's experience; caps only where runs are unattended |

---

## 10. First PR checklist (Phase 0)

- [ ] `uv init --package`, deps added, `uv.lock` committed, `.python-version`
- [ ] `src/skill_factory/cli.py` with `sf --help` and `sf version`
- [ ] `ruff`, `mypy`, `pytest` configured; `uv run pytest` green (one smoke test)
- [ ] `scripts/probe_claude.sh` + fixtures under `tests/fixtures/claude/2.1.331/`
- [ ] `docs/CLAUDE_CODE_CONTRACT.md` with confirmed field names
- [ ] `skills/` with the four existing skills copied in unchanged
- [ ] `.claude-version` containing `2.1.331`
