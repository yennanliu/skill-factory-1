# 實作計畫 — skill-factory-1

> 實作 [DESIGN.zh-TW.md](./DESIGN.zh-TW.md) 所描述的系統。
> English version: [IMPLEMENTATION_PLAN.en.md](./IMPLEMENTATION_PLAN.en.md)
>
> 撰寫於 2026-11-24，依據 Claude Code 2.1.331、uv 0.11.8、Python 3.14、Docker 29。

---

## 0. 範圍

**目標：** 為單一開發者做出完整循環：*寫 spec → 編譯 skill → lint → 評測 → 安裝 → 使用 → 量測 →
papercut → 修正*，涵蓋 5–6 個真實 skill，約 4–5 週的兼職工作量。

**範圍內**
- `sf` CLI（Python，以 **uv** 管理）
- 本 repo 即為 Claude Code plugin marketplace（分級 = plugin）
- 以 hooks + OpenTelemetry 收集遙測，存在本機
- 以 headless `claude -p` 為基礎的評測框架（golden、軌跡、作者評分、消融）
- 靜態 HTML 報表、session analyzer、status line
- GitHub Actions CI

**目前不做**
- 多使用者伺服器、託管儀表板、認證
- Codex / OpenCode / Gemini CLI 安裝目標（第 9 階段，等 Claude Code 端完整跑通後）
- 線上 skill 版本 A/B 測試、任務路由

---

## 1. 對設計文件的修正（查核最新 Claude Code 文件後）

以下會改變部分設計的實作方式。設計文件仍是「做什麼」，本計畫是「怎麼做」。

| 設計文件寫的 | 實際情況（Claude Code 2.1.x） | 對計畫的影響 |
|---|---|---|
| 用 `Skill` 的 `PostToolUse` hook 捕捉 skill 呼叫 | 只有 **Claude** 呼叫 skill 時才會觸發。使用者輸入 `/skill` 走的是 **`UserPromptExpansion`** hook（`command_name: "plugin:skill"`） | 遙測 hook 兩種事件都要處理；`trigger: model\|user` 由觸發的事件決定 |
| 單次成本 = 呼叫後 session 成本快照 − 呼叫前 | OTel 的 `claude_code.api_request` 事件在 skill 執行中會帶 `cost_usd` **與 `skill.name`** | 每個 skill 的成本 = 依 `skill.name` 分組加總 `cost_usd`；快照差只作為備援 |
| 遙測中的 plugin/skill 名稱 | 非官方 plugin 預設會被**遮蔽成 `third-party`**，除非設定 `OTEL_LOG_TOOL_DETAILS=1` | 我們的環境設定檔會設 `OTEL_LOG_TOOL_DETAILS=1`（也會加上工具參數 — collector 必須只在本機） |
| Session analyzer 讀 `~/.claude/projects/` 的 transcript | Transcript 格式是**內部格式、可能變動**；保留 30 天 | Analyzer 優先讀 OTel 日誌與評測的 stream-json；transcript 解析僅盡力而為、依版本防護、集中在單一 adapter |
| 遙測檔寫在 plugin 旁邊 | `${CLAUDE_PLUGIN_ROOT}` 每次更新都會變 | 所有狀態寫到 `${CLAUDE_PLUGIN_DATA}`（或 `$SF_HOME`） |
| Skill 內禁止 `.mcp.json` | Plugin *可以*附帶 `.mcp.json` | 由我們的 linter 禁止（Uber 的成本理由），不是 Claude Code 禁止 |
| 無塵室執行 | `--bare` 會跳過自動探索（hooks、plugins、MCP、CLAUDE.md、memory），且 **`--bare` 下即使用 `--plugin-dir`，plugin hooks 也不會執行** | 評測使用 `--bare --plugin-dir`；評測遙測來自 stream-json，不是 hooks |
| Plugin 更新 | `plugin.json` 的 `version` 沒有更新，使用者就拿不到新版 | `sf release` 會更新 plugin 版本；CI 檢查 |

仍未確認、由第 0 階段探測解決：`Skill` 工具的輸入欄位名稱、stream-json `result` 的確切欄位
（`num_turns`、`is_error`、`subtype`）、transcript 中 token 用量的位置。

---

## 2. 技術選型

| 面向 | 選擇 | 原因 |
|---|---|---|
| 套件 / 環境管理 | **uv**（`uv init --package`、`uv add`、`uv run`、`uv sync --locked`、`uv tool install`） | 快、單一 lockfile、不需另外處理 venv/pip/pipx |
| Python | `>=3.12`，CI 以 `.python-version` = `3.12` 固定；本機 3.14 也可用 | 套件支援廣；uv 會自動安裝直譯器 |
| CLI | `typer` + `rich` | 子指令、說明文字、表格好看 |
| Schema | `pydantic` v2 | 驗證 `spec.yaml`、評測案例、事件；產生 JSON Schema 給編輯器 |
| YAML | `ruamel.yaml` | 保留註解（`sf release` 會原地修改檔案） |
| 模板 | `jinja2` | `SKILL.md` 編譯骨架、HTML 報表 |
| 分析 | `duckdb` | 直接用 SQL 查 JSONL 遙測與執行結果，不需資料庫伺服器 |
| 呼叫 Claude（編譯、judge） | `anthropic` SDK | 編譯/judge 直接用 Messages API；agent 執行用 `claude` CLI |
| Agent 執行（評測） | `claude -p` subprocess | 和使用者實際用的是同一個執行檔；stream-json 提供軌跡與成本 |
| Lint / 格式 / 型別 | `ruff`、`mypy`（dev） | |
| 測試 | `pytest`、`pytest-xdist`、`syrupy`（snapshot） | |
| Hooks | **只用標準函式庫的 Python**，以 `python3` 執行 | 每次工具呼叫都會跑 hook；不要 venv 啟動成本、不要相依套件 |
| OTel collector | Docker 跑 `otel/opentelemetry-collector-contrib`，`file` exporter → JSONL | 本機、不用 SaaS；DuckDB 直接讀檔 |
| 無塵室 | 含 Node + Claude Code + uv 的 Docker image | 空 `$HOME`、固定 CLI 版本 |
| CI | GitHub Actions + `astral-sh/setup-uv` | |

### uv 起步（第 0 階段）
```bash
cd ~/skill-factory-1
uv init --package --name skill-factory --python 3.12 .
uv add typer rich pydantic ruamel.yaml jinja2 duckdb anthropic
uv add --dev pytest pytest-xdist syrupy ruff mypy
# pyproject.toml 的進入點：
#   [project.scripts]
#   sf = "skill_factory.cli:app"
uv run sf --help
uv tool install --editable .     # 開發期間讓 `sf` 全域可用
```
規則：`uv.lock` 要 commit；CI 用 `uv sync --locked`；不要用 `pip install`；skill 內需要套件的 script
使用 PEP 723 inline metadata 並以 `uv run --script` 執行（若在高頻路徑上則只用標準函式庫）。

---

## 3. 目標 repo 結構

```
skill-factory-1/
├── pyproject.toml / uv.lock / .python-version
├── src/skill_factory/
│   ├── cli.py                  # typer app：sf <cmd>
│   ├── models/                 # pydantic：spec.py、eval_case.py、event.py、run.py
│   ├── spec/                   # 載入、驗證、hash
│   ├── compile/                # spec → SKILL.md + 評測草稿（anthropic SDK）
│   ├── lint/                   # rules/*.py，一個規則一個檔，加上 registry
│   ├── market/                 # 產生 marketplace.json / plugin.json、版本更新
│   ├── eval/
│   │   ├── runner.py           # claude -p subprocess、N 次、平行
│   │   ├── stream.py           # stream-json 解析 → Trajectory
│   │   ├── graders/            # golden.py、trajectory.py、judge.py
│   │   ├── ablation.py
│   │   └── sandbox.py          # 本機 worktree / docker 後端
│   ├── telemetry/              # 把 JSONL + OTel 檔案匯入 duckdb view
│   ├── analyze/                # session 反模式偵測
│   ├── report/                 # jinja2 HTML 報表
│   └── papercuts/              # 分流、分組、轉成評測案例
├── hooks/                      # 只用標準函式庫，複製進 platform plugin
│   ├── sf_telemetry.py
│   └── sf_statusline.py
├── skills/                     # 唯一真實來源，每個 skill 一個資料夾
│   └── <name>/{spec.yaml, SKILL.md, scripts/, references/, evals/{golden,real,judged}/, fixtures/}
├── plugins/                    # 由 `sf build` 產生 — 不要手改
│   ├── sf-platform/            # hooks + log-papercut + find-skills（常駐）
│   ├── sf-core/
│   └── sf-domain-<name>/
├── .claude-plugin/marketplace.json   # 產生
├── profiles/{default.yaml, otel.env}
├── docker/{eval.Dockerfile, otel-collector.yaml, compose.yaml}
├── tests/                      # 單元 + snapshot + 選擇性執行的 live 套件
├── docs/
└── .github/workflows/{ci.yml, nightly-evals.yml}
```

關鍵決定：**`skills/` 是原始碼，`plugins/` 是建置產物。** `sf build` 依 tier 把每個 skill 複製到對應
plugin，並重新產生 `marketplace.json` / `plugin.json`。一個 skill、一個資料夾、一個 tier 欄位 —
不用 symlink（在 Windows 和某些安裝器會壞）。

---

## 4. 資料契約

先在 `src/skill_factory/models/` 定義這些；其他所有東西都依賴它們。用 `sf schema` 匯出 JSON Schema，
讓編輯器可以驗證 `spec.yaml` 與評測 YAML。

### 4.1 `spec.yaml`
```python
class Spec(BaseModel):
    name: str                      # kebab-case，== 資料夾名稱
    version: SemVer
    tier: Literal["always-on", "core", "domain", "experimental"]
    domain: str | None = None      # tier == "domain" 時必填
    owner: str
    goal: str
    triggers: list[str]            # → description
    outputs: list[str]
    tools: Tools                   # required / forbidden / scripts
    targets: Targets               # agents、models（第一個為預設）
    budget: Budget                 # max_body_lines=500、target_cost_usd
    evals: EvalPolicy              # min_pass_rate=0.7、max_pass_rate=0.95、runs=5
    side_effects: Literal["none", "local", "external"] = "none"   # external ⇒ 評測必須 mock/dry-run
```

### 4.2 遙測事件（`events.jsonl`，由 hooks 寫入）
```json
{"v":1,"ts":"…","event":"skill_invoked","session_id":"…","skill":"lc-add","plugin":"sf-domain-leetcode",
 "skill_version":"1.3.0","trigger":"model|user","model":"…","cwd_hash":"sha256:…"}
```
其他事件：`session_start`、`session_end`、`tool_error`（來自 `PostToolUseFailure`）、`papercut`。
絕不記錄：prompt 文字、檔案內容、原始路徑。

### 4.3 評測案例（`skills/<n>/evals/**/*.yaml`）
```python
class EvalCase(BaseModel):
    id: str
    kind: Literal["golden", "trajectory", "judged"]
    prompt: str
    fixture: str | None            # skills/<n>/fixtures/ 下的資料夾
    mocks: dict[str, Mock] = {}    # script 路徑 → stdout/exit code
    expect: GoldenExpect | None
    expect_trajectory: TrajectoryExpect | None
    rubric: str | None             # judged
    reference_grade: int | None    # 作者評分，用於校準 judge
    tags: list[str] = []
```

### 4.4 執行紀錄（`runs/<date>/<skill>/<case>/<n>.json`）
`{case_id, skill, skill_version, model, arm: with|without|minus:<section>, passed, score, cost_usd,
turns, tool_calls:[{name, ok}], duration_ms, cli_version, stream_path}`

---

## 5. 階段

每個階段 = 一個 branch + 一個 PR，結束時有可以實際跑的 demo。估計以專注工作天計。

### 第 0 階段 — 起步與探測（1 天）
**工作**
1. `uv init` + 套件（第 2 節），在 `pyproject.toml` 設定 `ruff`/`mypy`/`pytest`，`sf --help` 可用。
2. **探測 script** `scripts/probe_claude.sh`：安裝一個暫用 plugin，其 hook 把 stdin 傾印下來，涵蓋
   `PostToolUse(Skill)`、`UserPromptExpansion`、`SessionStart`、`Stop`、`PostToolUseFailure`；用
   `claude -p ... --output-format stream-json --verbose` 跑兩次：一次由模型觸發 skill、一次用 `/skill`。
   輸出存到 `tests/fixtures/claude/2.1.331/`。
3. 把確認後的欄位名稱寫進 `docs/CLAUDE_CODE_CONTRACT.md`（Skill 輸入欄位、stream-json `result` 欄位）。
   這些 fixture 會成為解析器的測試。
4. 原封不動複製第一批 skill：`lc-add`、`lc-coach`（來自 `~/.claude/skills/`）、
   `python-code-reviewer`、`fastapi-master`（來自 `ai_experiment/claude_code/skills_poc/`）。

**完成條件：** `uv run sf --help` 可執行；探測 fixture 已 commit；四個 skill 已放進 `skills/`。

### 第 1 階段 — Spec、建置、marketplace（2 天）
**工作**
1. `models/spec.py` + `sf spec validate [skill|--all]`。
2. 為四個 skill 手寫 `spec.yaml`（從現有 `SKILL.md` 反推）。
3. `sf build`：把 `skills/*` 複製到 `plugins/sf-<tier>/skills/`，為每個 plugin 產生 `plugin.json`，並產生
   `.claude-plugin/marketplace.json`（`name`、`owner.name`、`plugins[]`，`source: "./plugins/sf-core"`）。
4. `sf release <skill> --bump patch|minor|major`：更新 skill 版本與所屬 plugin 的 `version`。
5. Spec hash：`sf build` 在每個建置出的 `SKILL.md` 底部寫入 `<!-- sf:spec-sha256=… -->`。

**完成條件：**
```bash
uv run sf build
claude plugin marketplace add ~/skill-factory-1   # 或在 Claude Code 內用 /plugin marketplace add
claude plugin install sf-core@skill-factory
```
可以安裝，且 skill 出現在 Claude Code 中。（`claude plugin` 子指令請以安裝版本的 `claude plugin --help` 為準。）

### 第 2 階段 — Linter（1–2 天）
**工作**
1. 規則 registry：每條規則是 `(SkillDir) -> list[Finding]` 函式，含 id、嚴重度、修正提示。
2. 規則（來自設計 §6）：frontmatter、description 長度與「何時使用」用語、本文 ≤ 500 行、步驟編號、
   每步驗證、具體指令區塊、錯誤處理表、禁止 `.mcp.json`、禁止 secret（regex；若有安裝則用 `gitleaks`）、
   禁止共用 `/tmp/...` 路徑、引用的 script 存在且可執行、spec↔SKILL hash 相符、
   `side_effects: external` ⇒ 至少一個 mock 評測。
3. `sf lint [skill|--all] [--format text|json|github]`（`github` 輸出 `::error file=…` 註記）。
4. 透過 `.pre-commit-config.yaml` 的 pre-commit hook 呼叫 `uv run sf lint --changed`。

**完成條件：** 每個 skill 都 lint 通過，或在 `spec.yaml` 中有記錄的豁免（`lint_waivers: [rule-id]`）。
單元測試：每條規則各一個通過與一個失敗的 fixture。

### 第 3 階段 — 遙測（2 天）
**工作**
1. `hooks/sf_telemetry.py`（只用標準函式庫，< 50 ms）：讀 stdin，對應
   `PostToolUse` 且 `tool_name == "Skill"` → `skill_invoked, trigger=model`；
   `UserPromptExpansion` → `skill_invoked, trigger=user`；
   `PostToolUseFailure` → `tool_error`（若已知則附上目前的 skill）；
   `SessionStart` / `SessionEnd` → session 事件。
   追加到 `${CLAUDE_PLUGIN_DATA}/events.jsonl`（備援 `$SF_HOME/telemetry/`）。絕不阻擋或中斷 session：
   捕捉所有例外，exit 0。
2. 透過 `plugins/sf-platform/hooks/hooks.json` 發佈，使用 `${CLAUDE_PLUGIN_ROOT}/hooks/sf_telemetry.py`。
3. OTel：`profiles/otel.env`（`CLAUDE_CODE_ENABLE_TELEMETRY=1`、`OTEL_LOGS_EXPORTER=otlp`、
   `OTEL_METRICS_EXPORTER=otlp`、`OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`、
   `OTEL_LOG_TOOL_DETAILS=1`）；`docker/compose.yaml` 執行 collector，以 file exporter 寫到
   `~/.skill-factory/otel/*.jsonl`。`sf otel up|down|status`。
4. `telemetry/ingest.py`：在 `events.jsonl` 與 OTel 檔案上建立 DuckDB view：`skill_calls`、
   `api_requests`（含 `skill_name`、`cost_usd`、tokens）、`tool_results`。

**完成條件：** 正常工作一天後，`uv run sf stats --since 1d` 能列出每個 skill 的呼叫數、單次成本（來自 OTel
`skill.name`）、模型 vs 使用者觸發次數。
測試：用第 0 階段的探測 fixture 重播 hook；對輸出的 JSONL 做 snapshot。

### 第 4 階段 — 評測框架：golden + 軌跡（4–5 天）
**工作**
1. `eval/stream.py`：把 stream-json 解析成 `Trajectory`（依序的工具呼叫，含名稱、輸入摘要、成功/錯誤，
   以及 `result.total_cost_usd`、`usage`）。以第 0 階段 fixture 測試。
2. `eval/sandbox.py` — **本機**後端：把 fixture 複製到暫存資料夾、`git init`，執行
   ```bash
   claude -p "$PROMPT" --bare --plugin-dir <建置好的 plugin> \
     --output-format stream-json --verbose \
     --model <model> --max-turns <n> --max-budget-usd <上限> \
     --permission-mode acceptEdits --no-session-persistence
   ```
   `cwd` = 暫存資料夾。Mock：把 stub script 放在 `PATH` 最前面／在暫存副本中替換 `scripts/*`。
3. 評分器：`golden.py`（建立的檔案、內容/regex、README 列）、`trajectory.py`（必須呼叫的 skill、
   有序子序列比對、禁用工具、最大輪數）。
4. `runner.py`：每個案例跑 N 次（預設取自 spec），以 semaphore 平行（`--jobs`），寫入執行紀錄；
   `sf eval <skill> [--suite golden|trajectory] [--runs N] [--model M]`。
5. 輸出：每個案例與每個 skill 的通過率，附 Wilson 95% 信賴區間；標記 `> max_pass_rate`（「評測太簡單」）
   與 `< min_pass_rate`。
6. 撰寫評測：`lc-add` 3 個 golden + 6 個軌跡；`lc-coach` 5 個軌跡；兩個 `skills_poc` skill 各 3 個軌跡。
   真實 prompt 取自自己的歷史紀錄（複製並去識別化）。

**完成條件：** `uv run sf eval --all --runs 3` 在 15 分鐘、5 美元內跑完，並印出通過率表。
成本防護：每次執行 `--max-budget-usd`，每次呼叫設總額 `--budget`，超過即中止。

### 第 5 階段 — 報表與 status line（2 天）
**工作**
1. `sf report [--out report/index.html]`：每個 skill — 呼叫數、使用者/repo 數、留存（≥2 週有用過）、
   單次成本平均與 p90、輪數、工具錯誤、版本、共用、最新評測通過率、消融判定（若有）。
   Jinja2 + inline CSS + CDN 上的小型圖表函式庫。
2. `hooks/sf_statusline.py`：讀 statusLine JSON，輸出
   `$<cost.total_cost_usd> · <model.display_name> · ctx <context_window.used_percentage>% · <最後的 skill>`。
   最後的 skill 取自該 `session_id` 在 `events.jsonl` 的最後幾行。`sf statusline install` 在顯示 diff 後
   把 `statusLine` 寫入 `~/.claude/settings.json`。
3. 預算提醒：`profiles/default.yaml: daily_budget_usd`；status line 在 50% 變黃、80% 變紅、100% 顯示 `!`
   （不設硬上限 — Uber 的選擇）。

**完成條件：** 報表能打開且數字與 `sf stats` 一致；status line 顯示即時成本。

### 第 6 階段 — Spec 編譯器與 `sf new`（2–3 天）
**工作**
1. `sf new <name> --tier domain --domain leetcode`：建立資料夾骨架 + `spec.yaml` 模板。
2. `sf compile <skill>`：prompt = spec + lint 規則（當作檢查清單）+ 2 個範例 skill；呼叫 Messages API
   （撰寫預設用 `claude-opus-5-5`）；寫出 `SKILL.md` 與 `evals/trajectory/*.yaml` 草稿；接著跑 `sf lint`，
   把失敗回饋給模型，最多重試 2 次。
3. 絕不默默覆寫：寫到 `SKILL.md.new`、顯示 diff，加 `--apply` 才接受。
4. `sf compile --all --check`：CI 步驟，若 spec 改了但 `SKILL.md` 中的 hash 沒變就失敗。

**完成條件：** 一個全新 skill 從 `sf new` 到 lint 通過、完成評測、安裝，在 30 分鐘內完成。

### 第 7 階段 — Judge、消融、無塵室（4–5 天）
**工作**
1. `graders/judge.py`：盲評成對 judge（A/B 隨機、judge 永遠不知道哪個是哪組），rubric 來自案例，回傳
   `{winner, reasons}`。校準：`sf judge calibrate <skill>` 比較 judge 評分與 `reference_grade`，回報一致率
   （Cohen's κ）；κ < 0.6 時警告。
2. `ablation.py`：組別 `with`、`without`（不安裝 skill）、`minus:<section>`（建置時拿掉一個 `##` 段落的變體）。
   相同 prompt、相同模型，各跑 N 次。輸出：勝率、Δ成本、Δ輪數、Δ工具錯誤，以及設計 §7.3 的判定表
   （保留／優化／縮短／退役／刪除段落）。
3. `sandbox.py` — **docker** 後端：`docker/eval.Dockerfile`（node LTS、固定為 `.claude-version` 版本的
   `@anthropic-ai/claude-code`、uv、git、python）。只掛載 fixture 與建置好的 plugin，`HOME=/tmp/home`，
   `ANTHROPIC_API_KEY` 由環境變數傳入。`sf eval --sandbox docker`。
4. 模型基準：`sf eval <skill> --models haiku,sonnet,opus` → 選出達到 `min_pass_rate` 的最便宜模型，並以
   PR 提議調整 `targets.models` 順序。

**完成條件：** 每個 core skill 在報表中都有消融判定；至少找到一個「只在我電腦上能跑」的問題，或明確確認沒有。

### 第 8 階段 — Papercuts、find-skills、session analyzer（3 天）
**工作**
1. `skills/log-papercut`（常駐，在 `sf-platform`）：透過一個極小的標準函式庫 script，把結構化的一行寫入
   `${CLAUDE_PLUGIN_DATA}/papercuts.jsonl`（skill 呼叫 script；模型不手寫 JSON）。
2. `sf papercuts list|group|show`；`sf papercuts promote <id>` → 從 papercut 建立評測案例並開一個 branch。
3. `skills/find-skills`（常駐）：執行 `sf search "<task>" --json`（spec 文字比對 + 報表資料中的品質分數），
   並建議安裝指令。
4. `sf analyze [--since 7d]`：偵測器（設計 §9.1）優先使用 OTel 的 `api_request`/`tool_result` 事件；
   transcript adapter 為選用，依 CLI 版本防護。輸出：依成本排序的 session、找到的反模式與建議修正。

**完成條件：** 在真實 session 中提交的 papercut，能變成一個失敗的評測案例 → 修正 PR → 評測通過；
`sf analyze` 產出每週報告。

### 第 9 階段 — CI、nightly、託管 agent、其他 agent（3–4 天）
**工作**
1. `ci.yml`（PR 時）：`uv sync --locked` → `ruff` → `mypy` → `pytest` → `sf spec validate --all` →
   `sf build --check`（產生的檔案是最新的）→ `sf lint --all --format github` →
   `sf compile --all --check` → 對變動的 skill 跑 golden 評測（需要 `ANTHROPIC_API_KEY` secret；fork 時跳過）
   → 以 PR 留言貼出與 `main` 的通過率差異。
2. `nightly-evals.yml`：以預設模型跑完整軌跡套件，每週跑消融，把報表上傳為 artifact。
3. 託管 agent 範例：排程 workflow 無頭執行一個 skill，並帶成果指標
   （例如 **dry-run** 模式的 `job-apply-104`，或每週的 `sf analyze` 摘要）。
4. `sf install --agent codex|opencode|gemini`：把 skill 複製到各 agent 的 skill 資料夾並加上該 agent 專屬
   frontmatter。實作前先確認各 agent 目前的 skill 位置。

**完成條件：** 弄壞 skill 的 PR 會得到紅色檢查並列出失敗案例；nightly 報表會發佈。

### 時程
| 週 | 階段 |
|---|---|
| 1 | 0、1、2 |
| 2 | 3、開始 4 |
| 3 | 完成 4、5 |
| 4 | 6、7 |
| 5 | 8、9 |

---

## 6. CLI 指令一覽（目標）

| 指令 | 用途 |
|---|---|
| `sf new <name>` | 建立 skill + spec 骨架 |
| `sf spec validate` | 驗證 spec |
| `sf compile <skill> [--apply\|--check]` | Spec → SKILL.md + 評測草稿 |
| `sf lint [--changed\|--all]` | 執行 lint 規則 |
| `sf build [--check]` | 產生 plugins/ 與 marketplace.json |
| `sf release <skill> --bump …` | 更新 skill + plugin 版本 |
| `sf eval <skill> [--suite][--runs][--models][--sandbox local\|docker][--budget]` | 執行評測 |
| `sf eval --ablate <skill>` | 消融 |
| `sf judge calibrate <skill>` | Judge 與作者一致率 |
| `sf stats [--since]` | 快速遙測表 |
| `sf report` | HTML 報表 |
| `sf analyze` | Session 反模式 |
| `sf papercuts …` | 分流 / 轉換 papercut |
| `sf search "<task>"` | 搜尋 skill |
| `sf otel up\|down\|status` | 本機 OTel collector |
| `sf statusline install` | 安裝 status line |
| `sf schema` | 匯出 JSON Schema |

---

## 7. 測試策略

| 層級 | 測什麼 | 怎麼測 |
|---|---|---|
| 單元 | Models、lint 規則、評分器、stream 解析器、偵測器 | `pytest`，fixture 放在 `tests/fixtures/` |
| 契約 | 我們的解析器 vs 真實 Claude Code 輸出 | 每個 CLI 版本一組探測 fixture（`tests/fixtures/claude/<version>/`）；更新 `.claude-version` 時重新探測 |
| Snapshot | 產生的 `plugin.json`、`marketplace.json`、編譯骨架、報表 HTML | `syrupy` |
| 整合（不呼叫 API） | `sf build` → `claude plugin validate`（若有提供） | CI |
| Live（選擇性） | 真的用 `claude -p` 評測一個極小 skill | `pytest -m live`，需要 `ANTHROPIC_API_KEY`，只在 nightly 跑 |

全部用 `uv run pytest -n auto` 執行；live 測試用 `uv run pytest -m live`。

---

## 8. 成本與安全防護

- 每次 `claude -p` 執行都有 `--max-turns` 與 `--max-budget-usd`；每次 `sf eval` 呼叫都有 `--budget`。
- 預設評測模型是 `targets.models` 中曾通過的最便宜模型；Opus 只用於撰寫與 judge。
- `side_effects: external` 的 skill（例如會送出真實應徵的 `job-apply-104`）只能對 mock 或 `dry_run` 旗標
  執行評測；linter 強制檢查，runner 會拒絕其他情況。
- Skill、spec、fixture、遙測中都不得有帳密（lint 規則 + `.gitignore`）；104 登入資訊留在使用者的
  keychain/環境變數。
- `OTEL_LOG_TOOL_DETAILS=1` 會把工具參數加進 OTel 日誌：collector 只寫本機磁碟、絕不送到遠端，
  且 `~/.skill-factory/` 位於 repo 之外。

---

## 9. 決策紀錄

| # | 決策 | 替代方案 | 原因 |
|---|---|---|---|
| D1 | Python 一律用 uv | pip+venv、poetry | 使用者要求；最快、單一 lockfile |
| D2 | `skills/` 為原始碼、`plugins/` 為產物 | Skill 直接放在 plugin 資料夾 | 改一個 tier 欄位就能搬移 skill；不用 symlink |
| D3 | Agent 執行用 `claude -p` CLI | Agent SDK | 和實際使用同一個執行檔；之後可在同一個 runner 介面後面加上 SDK |
| D4 | Hooks 只用標準函式庫 | 用 `uv run --script` 跑 hook | Hook 每次工具呼叫都會跑；啟動時間很重要 |
| D5 | DuckDB 直接查 JSONL | 匯入 SQLite、Postgres | 不需要匯入步驟，直接對檔案下 SQL |
| D6 | 用 OTel `skill.name` 算成本 | 快照差 | 內建且精確到每個請求 |
| D7 | 本機 collector、不用 SaaS | Honeycomb/Grafana Cloud | 隱私；單一使用者 |
| D8 | 可視化 + 提醒，不設硬上限（評測預算除外） | 硬上限 | Uber 的經驗；只有無人看管的執行才設上限 |

---

## 10. 第一個 PR 檢查清單（第 0 階段）

- [ ] `uv init --package`、加入套件、commit `uv.lock`、`.python-version`
- [ ] `src/skill_factory/cli.py`，含 `sf --help` 與 `sf version`
- [ ] 設定 `ruff`、`mypy`、`pytest`；`uv run pytest` 通過（一個 smoke test）
- [ ] `scripts/probe_claude.sh` + `tests/fixtures/claude/2.1.331/` 下的 fixture
- [ ] `docs/CLAUDE_CODE_CONTRACT.md`，記錄確認後的欄位名稱
- [ ] `skills/` 內含原封不動複製進來的四個既有 skill
- [ ] `.claude-version`，內容為 `2.1.331`
