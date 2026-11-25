# skill-factory-1

[English](README.md) | **繁體中文**

一個縮小版、Uber 風格的 agent skill 平台：撰寫、評測、量測並持續改善 agent skill
（以 Claude Code 為主，也支援 Codex / OpenCode / Gemini CLI）。

## 文件
- 專案網站：<http://yennj12.js.org/skill-factory-1/>
- 設計文件：[docs/DESIGN.zh-TW.md](docs/DESIGN.zh-TW.md)（[English](docs/DESIGN.en.md)）
- 實作計畫：[docs/IMPLEMENTATION_PLAN.zh-TW.md](docs/IMPLEMENTATION_PLAN.zh-TW.md)（[English](docs/IMPLEMENTATION_PLAN.en.md)）
- Claude Code 契約（英文）：[docs/CLAUDE_CODE_CONTRACT.md](docs/CLAUDE_CODE_CONTRACT.md)

## 快速開始
需要 [uv](https://docs.astral.sh/uv/) 與 Claude Code。

```bash
uv sync                      # 依 uv.lock 建立 .venv 並安裝套件
uv run sf --help
uv run sf version            # sf、已安裝的 Claude Code、固定的契約版本
uv run sf spec validate      # 驗證 skills/*/spec.yaml 與 SKILL.md frontmatter
uv run sf build              # 重新產生 plugins/ 與 .claude-plugin/marketplace.json
uv run sf release lc-add --bump patch   # 更新 skill 與 plugin 版本並重新建置
uv run sf lint               # 依規則 lint；--list-rules、--changed、--format github、--strict
uv run sf new my-skill --tier experimental --goal "..."   # 建立 spec.yaml、stub 與起始評測
uv run sf compile my-skill   # spec.yaml -> SKILL.md.new + 評測草稿（Claude Opus 5.5）；--apply 接受
uv run sf compile --check    # CI：已編譯 skill 的 spec 若在編譯後被修改即失敗
uv run sf eval lc-add        # 執行 skill 的評測（透過 `claude plugin eval`），含無 skill 對照組
uv run sf eval --all --runs 3 -j 8   # 所有 skill；結果寫在 runs/<skill>/<時間戳>/
uv run sf ablate lc-add      # 有/無 skill，並逐一拿掉每個 `##` 段落 -> 判定
uv run sf bench lc-coach --models claude-haiku-5-5,claude-sonnet-5-5   # 達標中最便宜的模型
uv run sf judge calibrate lc-coach   # LLM judge 與你在 evals/_calibration/ 的標註比對（Cohen's kappa）
uv run sf papercuts list     # log-papercut skill 回報的問題；group / show / promote / close
uv run sf search "review python code"   # 搜尋既有 skill（含安裝指令）
uv run sf analyze --since 7d # 最貴的 session 與浪費的模式，每項附建議修正
uv run sf install --agent codex      # 也支援 opencode、gemini；--scope project、--dry-run
uv run sf agent run weekly-skill-digest   # 託管 agent：無頭執行並檢查成果
uvx pre-commit install       # 每次 commit 時 lint 變更的 skill 並檢查產生的檔案
scripts/probe_otel.sh        # 升級 CLI 後重新擷取 OTel 資料（約 $0.01）
uv run pytest                # 單元測試與 Claude Code 契約測試
uv run ruff check . && uv run ruff format --check . && uv run mypy
scripts/probe_claude.sh      # 升級 CLI 後重新擷取 Claude Code 資料（約 $0.01）
scripts/e2e_local.sh         # 以真正的 Claude Code CLI 做端到端檢查（約 $0.50）
```

## 安裝 skill
```
/plugin marketplace add yennanliu/skill-factory-1
/plugin install sf-core@skill-factory
/plugin install sf-domain-leetcode@skill-factory
```

| 分級（`spec.yaml`） | Plugin |
|---|---|
| `always-on` | `sf-platform` |
| `core` | `sf-core` |
| `domain` + `domain: x` | `sf-domain-x` |
| `experimental` | `sf-experimental` |

## 遙測
兩種來源，都只存在本機（`$SF_HOME`，預設 `~/.skill-factory/`）：

1. **Hooks**：安裝 `sf-platform`（`/plugin install sf-platform@skill-factory`）。每次 skill 呼叫、每個工具錯誤
   （歸屬到當時的 skill）以及每個 session 的開始與結束都會寫入 `telemetry/events.jsonl`。不記錄 prompt、
   工具輸入或路徑。
2. **OpenTelemetry**：提供每個 skill 的成本。
   ```bash
   uv run sf otel up                 # 標準函式庫 collector，監聽 127.0.0.1:4318 -> ~/.skill-factory/otel/
   eval "$(uv run sf otel env)"      # 然後從這個 shell 啟動 claude
   uv run sf stats --since 1d        # 呼叫次數、模型/使用者觸發、session 數、單次成本、工具錯誤
   ```
3. **報表**：`uv run sf report` 產生 `report/index.html`，單一自足頁面（無 JS、無 CDN）。每個 skill 顯示：
   呼叫數、模型/使用者比例、單次成本（平均與 p90）、工具錯誤、repo 數、活躍週數、使用中的版本、
   一起使用的 skill、14 天走勢、context 成本，以及最近一次評測與對照組判定。
4. **Status line**：`uv run sf statusline install [--budget 2]` 先顯示 diff，再於 `~/.claude/settings.json`
   設定 `statusLine`，顯示 `$0.42 · Opus 5.5 · ctx 31% · lc-add`。成本達 session 預算 50% 變黃、80% 變紅；
   不設硬上限。

## 撰寫
`spec.yaml` 是唯一真實來源（Uber 的心得：先寫 spec，意圖才能撐過模型更替）。`sf compile` 以結構化輸出
請 Claude 產生 description、本文與 3–5 個評測草稿，接著對結果執行 linter，並把失敗回饋給模型（最多重試 2 次）。
它寫入 `SKILL.md.new` 並顯示 diff，除非加 `--apply` 否則不會覆寫任何東西。編譯後的 `SKILL.md` 帶有
`<!-- sf:spec-sha256=… -->`，因此 spec 改了卻沒重新編譯時，`sf lint`（SF012）與 `sf compile --check` 都會發現。
後端：
- `--backend claude-cli`（預設）：`claude -p --json-schema`，不開放工具，使用你的 Claude Code 登入。
- `--backend api`：Anthropic SDK（`ANTHROPIC_API_KEY` 或 `ant auth login`），串流、adaptive thinking、
  伺服器端拒答備援。

評測草稿標記為 `draft`，一旦存在就不會被覆寫。信任它們之前請先審閱。

## 評測
案例放在 `skills/<name>/evals/<case>/case.yaml`，使用 Claude Code 自己的
[plugin eval 格式](https://code.claude.com/docs/en/plugin-evals.md)。共用的 scaffold script 放在 `evals/_shared/`。
`sf eval` 把 skill 包成單一 skill 的 plugin，以 `--no-publish`、`--scaffold`、spec 的工具授權與無 plugin 的
**對照組** 執行 `claude plugin eval`，再依案例與 skill 回報：通過率與 95% Wilson 信賴區間、對照組通過率、
平均分數，以及 skill 實際被觸發的頻率。每個 skill 的狀態依其 spec 判定：`ok`、`below-min`（exit 1）或
`too-easy`（高於 `max_pass_rate`）。

## skill 值得它的 token 嗎？
- **`sf ablate`** 讓完整 skill 與無 skill 對照組比較。判定：`keep`、`optimize`（有幫助但成本超過對照組 1.5 倍）、
  `shorten or retire`（±10% 內打平）或 `retire`。接著逐一拿掉每個 `##` 段落：通過率沒降就 `delete section`，
  否則 `keep section`；若套件已高於 `max_pass_rate`（看不出下降）則為 `inconclusive`。一次只刪一個段落並
  重新消融，因為兩個段落可能互相補位。
- **`sf bench`** 在多個模型上執行套件，推薦達到 `min_pass_rate` 的最便宜模型。`--write` 會把它設為 spec 的預設模型。
- **`sf judge calibrate`** 以多數決讓 judge 評分作者標註的回應（`evals/_calibration/*.yaml`），回報一致率與
  Cohen's κ。低於 0.6 就別信任該 skill 的 LLM 評分器。
- 無塵室：每次 `claude plugin eval` 執行都有暫存的 home 與設定、shell 工具的 OS 沙箱，且只載入受測 plugin，
  因此不需要另外的 Docker 映像。

## 改善循環
常駐的 `sf-platform` plugin 內含兩個 skill：
- **`log-papercut`**：skill、script 或工具行為異常時，agent 會提交結構化報告
  （`scripts/log_papercut.py` → `$SF_HOME/papercuts/inbox.jsonl`），即使它已自行恢復。
- **`find-skills`**：寫新 skill 之前，先搜尋建置時打包的目錄（`references/catalog.json`），並回傳安裝指令。
  不需要 CLI。

循環：`sf papercuts list`，再 `sf papercuts promote <id>`（產生回歸案例草稿）。讓案例重現問題並確認它失敗，
修改 **spec**，`sf compile --apply`，確認案例通過，最後 `sf papercuts close <id>`。`sf analyze` 處理 skill
*之間* 的浪費：唯讀工作用了昂貴模型、大型工具結果、休息後快取過期、預載 context 過重、輪詢、搜尋打轉與重試迴圈。

## 其他 agent
`sf install --agent codex|opencode|gemini [--scope user|project]` 把建置好的 skill 複製到
`~/.codex/skills`、`~/.config/opencode/skills` 或 `~/.gemini/skills`（專案範圍：`.codex/skills`、
`.opencode/skills`、`.gemini/skills`）。它會移除 Claude 專屬的 frontmatter（`allowed-tools`），且只安裝 spec
的 `targets.agents` 有列出該 agent 的 skill（`--all-skills` 可覆寫），也絕不取代非它安裝的 skill。
遙測 hooks 僅支援 Claude Code。

## 託管 agent
`managed/<name>.yaml` 定義一個依排程無頭執行、以 skill 為基礎的工作，各自有 prompt、plugin、模型、預算與
成果檢查（必須出現的字串、每個 repo skill 都被提及、成本上限）。`sf agent run` 把成本、輪數與成果記錄在
`runs/agents/<name>/`。範例：`weekly-skill-digest`。

## CI
- `ci.yml`（每個 PR）：
  - checks：`uv sync --locked`、ruff、mypy、pytest、`sf spec validate`、`sf build --check`、
    `sf lint --format github`（行內註記）、`sf compile --check`
  - evals：`sf eval --changed` 執行變更 skill 的套件並更新一則摘要留言。需要 `ANTHROPIC_API_KEY` secret，
    沒有時會略過。
- `nightly-evals.yml`：每天跑完整套件、每週一跑消融，報表以 artifact 上傳。
- `managed-agents.yml`：每週 `sf agent run --all`。
- `pages.yml`：`site/` 變更時部署專案網站。

## 結構
| 路徑 | 內容 |
|---|---|
| `src/skill_factory/` | `sf` CLI |
| `skills/<name>/` | skill 原始碼：`spec.yaml`（真實來源）+ `SKILL.md` + references |
| `market.yaml` | marketplace 名稱/擁有者與各 plugin 版本 |
| `plugins/`、`.claude-plugin/` | 由 `sf build` **產生**；不要手動修改 |
| `scripts/probe_claude.sh` | 把真實的 hook / stream-json 資料記錄到 `tests/fixtures/claude/<version>/` |
| `src/skill_factory/lint/rules.py` | lint 規則 SF001–SF014；可在 `spec.yaml` 以 `lint_waivers` 豁免 |
| `hooks/` | 遙測 hook（僅標準函式庫）+ `hooks.json`，透過 `assets` 隨 `sf-platform` 發佈 |
| `src/skill_factory/telemetry/` | OTLP collector、DuckDB 匯入、`sf stats` |
| `site/` | 專案網站（靜態頁面，中英雙語） |
| `docs/CLAUDE_CODE_CONTRACT.md` | 我們依賴的 Claude Code 行為，經探測確認 |

## 狀態
[實作計畫](docs/IMPLEMENTATION_PLAN.zh-TW.md) 的 10 個階段（0–9）皆已完成，另有三個實驗性 skill
（`commit-message-writer`、`changelog-writer`、`cron-explainer`）。`scripts/e2e_local.sh` 以真正的
Claude Code CLI 執行端到端檢查。

## 參考
- Uber Engineering — [How Uber Evals 1 Million Agent Skill Invocations a Month](https://www.uber.com/us/en/blog/optimizing-ubers-agent-skills/)（2026-11-23）
- Uber Engineering — [Running a Software Factory Efficiently at Uber Scale](https://www.uber.com/us/en/blog/efficient-software-factory/)（2026-08-27）
- [Agent Skills 規範](https://agentskills.io)
