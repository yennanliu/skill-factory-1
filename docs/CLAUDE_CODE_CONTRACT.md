# Claude Code contract (confirmed by probe)

What `skill-factory` relies on from Claude Code, **confirmed against real output** captured by
`scripts/probe_claude.sh`. Fixtures live in `tests/fixtures/claude/<version>/`; `tests/test_claude_contract.py`
asserts every fact below, so a CLI upgrade that changes them fails CI.

- **Version probed:** 2.1.331 (pinned in `.claude-version`)
- **Probe:** a throwaway plugin `sf-probe` with skill `probe-echo` and a hook on every relevant event that dumps
  stdin. Three runs: `model_triggered` ("Run the probe-echo skill."), `user_triggered` (`/sf-probe:probe-echo`) and `permission_blocked` (an `ls` outside the working dir).
- **Re-probe** whenever Claude Code is upgraded: `scripts/probe_claude.sh` (costs about $0.01 on Haiku), then
  update `.claude-version`.

## 1. Skill invocation

| Fact | Value |
|---|---|
| Tool name when Claude invokes a skill | `Skill` |
| `tool_input` | `{"skill": "<plugin>:<skill>"}` — field is **`skill`**, value is namespaced |
| `PostToolUse.tool_response` for `Skill` | `{"commandName": "<plugin>:<skill>", "success": true}` |
| User types `/<plugin>:<skill>` | **No `Skill` tool call at all.** Fires `UserPromptExpansion` with `command_name`, `command_args`, `command_source: "plugin"`, `expansion_type: "slash_command"` |
| Stream-json for a user-typed skill | The expansion does **not** appear in the stream; only the following tool calls do. The `init` event lists the skill in `skills` and `slash_commands` |

Consequences:
- Telemetry: `PostToolUse(tool_name=Skill)` → `trigger=model`; `UserPromptExpansion(command_source=plugin)` → `trigger=user`.
- Eval trajectory grading: for a user-typed case, "skill was used" is known from the prompt, not from the stream.

## 2. Hook payloads

Common fields on every event: `session_id`, `transcript_path`, `cwd`, `hook_event_name`. Most also carry
`permission_mode`, `prompt_id`, `effort: {"level": ...}`.

| Event | Extra fields seen |
|---|---|
| `SessionStart` | `source` (`startup`) |
| `UserPromptSubmit` | `prompt` |
| `UserPromptExpansion` | `prompt`, `command_name`, `command_args`, `command_source`, `expansion_type` |
| `PreToolUse` | `tool_name`, `tool_input`, `tool_use_id` |
| `PostToolUse` | `tool_name`, `tool_input`, `tool_response`, `tool_use_id`, `duration_ms` |
| `PostToolUseFailure` | `tool_name`, `tool_input`, `tool_use_id`, `error` (string, e.g. `"Exit code 1\n..."`), `is_interrupt`, `duration_ms` |
| `Stop` | `last_assistant_message`, `stop_hook_active`, `background_tasks`, `session_crons` |
| `SessionEnd` | (common fields) |

A failing tool call fires `PostToolUseFailure` **instead of** `PostToolUse` (`error` starts with `Exit code <n>`, n is platform-dependent: GNU `ls` exits 2, BSD 1). A permission block (e.g. a path outside
the working dirs) fires neither; the stream shows `system/permission_denied`.

## 3. Headless stream-json (`claude -p --output-format stream-json --verbose`)

| Event | Notes |
|---|---|
| `system/init` | `claude_code_version`, `model`, `cwd`, `tools`, `skills`, `slash_commands`, `plugins[{name,path,source,version}]`, `mcp_servers`, `permissionMode` |
| `system/hook_started`, `system/hook_response` | one pair per hook run |
| `system/permission_denied` | `tool_name`, `tool_use_id`, `message` |
| `assistant` | `message.content[]` of `thinking` / `text` / `tool_use{id,name,input}` |
| `user` | `message.content[]` of `tool_result{tool_use_id, content, is_error}` (and the expanded skill body as `text` after a model-invoked `Skill`) |
| `result` | `subtype` (`success`), `is_error`, `num_turns`, `total_cost_usd`, `duration_ms`, `duration_api_ms`, `usage{input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}`, `modelUsage{<model>: {costUSD, ...}}`, `permission_denials`, `result` (final text), `terminal_reason` |

Built-in plugins (`cc-plugin-*@builtin`) are always listed in `init.plugins`; filter on our marketplace name.

## 4. Flags used by the probe (all accepted by 2.1.331)

`-p`, `--plugin-dir`, `--setting-sources project,local`, `--model`, `--output-format stream-json`, `--verbose`,
`--max-turns`, `--max-budget-usd`, `--allowedTools`, `--no-session-persistence`. Redirect stdin from `/dev/null`
or the CLI waits 3 s for piped input.

## 5. OpenTelemetry (confirmed by `scripts/probe_otel.sh`)

Fixtures: `tests/fixtures/otel/<version>/{logs,metrics}.jsonl` (OTLP JSON, one export request per line).
Env: `profiles/otel.env` — `OTEL_EXPORTER_OTLP_PROTOCOL=http/json` works, so a stdlib HTTP receiver is
enough (no Docker collector needed).

| Fact | Value |
|---|---|
| Log events seen | `user_prompt`, `skill_activated`, `api_request`, `tool_decision`, `tool_result`, `assistant_response`, `plugin_loaded`, `mcp_server_connection`, `managed_settings_resolved` (body `claude_code.<name>`, attr `event.name` = `<name>`) |
| `skill_activated` | `skill.name` (`<plugin>:<skill>`), `invocation_trigger` = **`claude-proactive`** (model) or **`user-slash`** (typed), `skill.source`, `plugin.name`, `marketplace.name` |
| `api_request` | `cost_usd`, `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `duration_ms`, `prompt.id`, and **`skill.name` on every request made after the skill activated**. The request that *decided* to call the skill has no `skill.name` |
| `tool_result` | `tool_name`, `success`, `duration_ms`, `tool_input` (needs `OTEL_LOG_TOOL_DETAILS=1`), sizes |
| Prompt / response text | redacted by Claude Code (`<REDACTED>`) unless `OTEL_LOG_USER_PROMPTS` is set |
| Metrics | `claude_code.cost.usage` and `claude_code.token.usage` carry `skill.name` + `model`; also `session.count`, `active_time.total` |
| Common attributes | `session.id`, `event.timestamp`, `event.sequence`, plus user/org/account attributes (the probe sanitizer redacts them) |

Consequence: **cost per skill = `sum(api_request.cost_usd) GROUP BY skill.name`**; calls and trigger come from
`skill_activated`. Hook events add tool-error attribution and work without the OTel env.

## 6. `claude plugin eval` (the harness `sf eval` wraps)

Docs: <https://code.claude.com/docs/en/plugin-evals.md>. Real output fixture:
`tests/fixtures/claude-eval/<version>/lc-coach.json`.

| Fact | Value |
|---|---|
| Case | a dir with `case.yaml` (`schema_version: "1.1"`, `name`, `execution.{prompt,max_turns,timeout_seconds,allowed_tools}`, `context.scaffold_script`, `graders`) or `prompt.md` + `graders/*.md` |
| Graders | `regex` (`target`: `last_message`, `trace`, `files`, `{source: file, path}`), `tool_used` (`min`/`max`, `input_match`), `tool_order`, `file_exists`, `llm` (2 of 3 judge votes), `baseline`. No code graders |
| Fixtures | only via `context.scaffold_script`, which runs only with `--scaffold`; paths must stay **inside the case dir** (`..` is rejected), hence sf copies `evals/_shared/` into each case |
| Tools | read-only tools by default; `Bash`/`Write`/`Edit`/`WebFetch` need `--allow-tools` and run under an OS sandbox |
| Baseline arm | `--ablation with-without` (default when a plugin resolves) re-runs every case with no plugin. `tool_used: Skill` graders become an indicator, not part of the score |
| Output (`--json`) | `schemaVersion: 1`, `costUsd`, `partial`, `cases[].graders[]` (definitions with `type`, `config`), `cases[].arms.{with,without}[]` runs with `score`, `passed`, `turns`, `costUsd`, `judgeCostUsd`, `durationSeconds`, `error`, `graders[]` verdicts (`passed`, `explanation`, `withOnly`, `scored`, `judgeVotes`) |
| Exit codes | 0 done, 1 a case below `--threshold` (sf passes `0` and judges itself), 2 partial (cost ceiling) |
| Publishing | the HTML report is **published to claude.ai by default**; sf always passes `--no-publish` |
| Slash prompts | a case whose prompt is `/skill …` never records a `Skill` tool call (§1), so don't grade it with `tool_used: Skill` |

## 7. Still unverified

- Token usage location inside `~/.claude/projects/*.jsonl` transcripts (we avoid depending on it).
- Behaviour of plugin skills under `--bare`: no longer needed, since evals run through `claude plugin eval`,
  which gives each run its own isolated home and config.
