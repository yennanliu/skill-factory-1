---
name: find-skills
description: Searches the skill-factory marketplace catalog for an existing skill that fits a task and gives the exact /plugin install command for the best match. Use when the user asks "is there a skill for <task>?", "find a skill that can <task>", "what skills do we have for <topic>?", or asks you to write a new skill (check first).
allowed-tools: Bash
---

# Find Skills

Check whether a skill already exists before anyone writes a new one or starts a repetitive workflow from scratch. One search finds the answer. Report the best matches with their install commands. If nothing fits, say so plainly.

## Rules

- Use only `Bash` to run the search. Never use `Write` or `Edit`. This skill creates and changes no files.
- **If the user asked you to write a new skill, do the search first and stop there.** Show the results, then ask: "Install <match>, or go ahead with a new skill?" Do not create a skill folder, SKILL.md, spec or any other file until they answer.
- Report only skills that appear in the script output. Never make up a skill name, plugin or install command.

## Workflow

1. **Turn the request into a short task phrase.** Pull out the task words, for example `summarise pull request diffs` from "is there a skill that can summarise my PRs?". Drop filler such as "is there a skill for".
   Verify: the phrase is 3 to 10 words and names the task, not the request.

2. **Run the search.** Use the path of `scripts/find_skills.py` inside this skill's folder:
   ```bash
   python3 scripts/find_skills.py "summarise pull request diffs" --limit 5
   ```
   Verify: the command exits 0 and prints a JSON list (it can be `[]`).

3. **Sort the hits into strong and weak.** Each hit has `skill`, `plugin`, `tier`, `score` (0-1), `goal` and `install`. A score of `0.15` or more is a candidate. A score below `0.15` is a weak match. Read the `goal` of each candidate and keep only the ones that really fit the task.
   Verify: every hit you keep has a score of at least 0.15 and a goal that matches the task.

4. **Report the result.** For each match, best first, give one line:
   ```text
   <skill> (<plugin>, score <score>): <goal>
     install: <install>
   ```
   Copy the `install` value exactly, for example `/plugin install <plugin>@skill-factory`. If no hit is strong enough, say: **"No existing skill fits <task>."** You can mention the weak matches, and label them as weak.
   Verify: each install command in your reply is copied word for word from the script output.

5. **Stop, or ask before you build.** If the user only asked whether a skill exists, you are done. If they asked you to write a new skill, end with one question: install the best match, or write a new skill? Do not write anything until they answer. Then follow their choice.
   Verify: no files were created or edited in this turn, and your reply ends with the question when a new skill was requested.

## Error recovery

| Symptom | Action |
|---|---|
| Exit code 2, `catalog not found or unreadable at ...` | The bundled `references/catalog.json` is missing. It is written by `sf build`. Tell the user to rebuild with `sf build`, and say that you could not search. Do not write a catalog yourself. |
| Output is `[]` | Try once more with synonyms or a broader phrase (for example `pr review` instead of `summarise pull request diffs`). If it is still empty, report "No existing skill fits". |
| All scores are below 0.15 | Report "No existing skill fits <task>", and list the top weak hits as weak only. |
| `No such file or directory` for the script | Use the full path to this skill's `scripts/find_skills.py`. Do not recreate the script. |
| `python3: command not found` | Try `python`. If that also fails, tell the user that the search could not run. |
| The user insists on a new skill even though a match exists | Do what they choose. The search result was advice, not a block. |

<!-- sf:spec-sha256=384f95ab4ad574d00789da8a0dc910c65955b00052c48c43386a5e733b260e74 sf:version=0.1.0 -->
