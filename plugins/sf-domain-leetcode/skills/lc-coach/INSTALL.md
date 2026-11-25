# Installing the LC Interview Coach

The skill is four plain markdown files with no dependencies, so any coding agent that can be
given a system prompt can run it. `SKILL.md` is the whole coach; the three files under
`references/` are loaded on demand.

```text
lc-coach/
├── SKILL.md                    # the coach — modes, review loop, output contract
└── references/
    ├── rubric.md               # four signals, level anchors, self-score card
    ├── patterns.md             # cue / invariant / target / classic bug per pattern
    └── talk-track.md           # what to say in the room, phase by phase
```

## Claude Code

In this repo the skill lives at `skills/lc-coach/` and ships in the `sf-domain-leetcode`
plugin: `/plugin install sf-domain-leetcode@skill-factory`. It loads by description when
you ask for a review or a mock, or invoke it by name:

```text
/lc-coach review leetcode_python/Sliding_Window/sliding_window_maximum.py
```

Without the plugin, copy `skills/lc-coach/` to `~/.claude/skills/lc-coach/` (all repos) or
`.claude/skills/lc-coach/` (one repo).

## Claude (claude.ai / desktop)

**Customize → Skills → + → + Create skill → Upload a skill**, with the directory zipped:

```bash
cd skills && zip -r lc-coach.zip lc-coach
```

Leave the YAML frontmatter intact. `description` is what Claude matches a request against when
deciding to load the skill on its own, so it is the one field worth keeping precise. `name` is
the display label; the *directory* name is what supplies the slash command — which is why this
repo keeps the two identical, and why `sf spec validate` fails when they drift.

## Codex

Append a pointer to `AGENTS.md` at the repo root — Codex reads it automatically:

```markdown
## Interview coaching
When asked to review a solution, run a mock interview, teach a pattern, or analyse a
bottleneck, follow `skills/lc-coach/SKILL.md` and its `references/` (or wherever you copied it).
```

## Gemini CLI

Same shape, in `GEMINI.md`. Or point at it per session:

```bash
gemini -p "Act as the coach defined in skills/lc-coach/SKILL.md. Review @solution.py"
```

## Cursor / Windsurf / other IDE agents

Add a rule file (`.cursor/rules/interview-coach.mdc`, or the editor's equivalent) whose body
is the pointer above. Keep the pointer, not a copy — one source of truth means a fix to
`SKILL.md` reaches every agent at once.

## Anything else

Paste `SKILL.md` in as the system prompt. It is self-contained; the `references/` files are
optional depth, and the coach works without them (with shallower pattern recall).
