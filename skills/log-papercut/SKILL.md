---
name: log-papercut
description: Files a short structured papercut report when a skill, script or tool misbehaves, is confusing, or needs a workaround, even if you recovered. Use when skill instructions were wrong, stale or ambiguous, a command it told you to run failed or surprised you, you needed a workaround, or the user says "log a papercut for <skill>".
allowed-tools: Bash
---

# Log a Papercut

A papercut is a small, structured report that tells a skill's owner what went wrong, so they can fix it. File one every time a skill, script or tool gets in your way, **even when you worked around it**. Filing one takes a single command. It is never the main task: file it, tell the user in one line, and go back to their work.

## When to file

- A skill's instructions were wrong, stale or ambiguous.
- A script or command that a skill told you to run failed, or returned something unexpected.
- You needed a workaround to finish the user's task.
- The user asks: "log a papercut for <skill>".

File **one papercut per distinct problem**. Do not file one for mistakes you made yourself, or for the user's own code failing.

## Rules

- Use only `Bash` to run the script. Never use `Write` or `Edit` to create or change the inbox, or any other file, for a papercut.
- Never put secrets, tokens, prompt text, file contents or personal data (such as emails) in any field.
- Keep each field short. The script cuts every field to 500 characters. In `--happened`, quote no more than 3 lines of the error.

## Workflow

1. **Identify the skill or tool at fault.** Name the skill, script or tool whose behaviour caused the problem, for example `deploy-helper` or `scripts/build.sh of release-notes`. If you know its version, note it.
   Verify: you can say in one sentence which component misbehaved and why it is not your own error.

2. **Draft the four facts.** Write one short sentence for each:
   - `tried`: what you were trying to do.
   - `expected`: what the skill said, or implied, would happen.
   - `happened`: what actually happened, including no more than 3 lines of the error.
   - `suggested-fix`: what would fix it, if you know. Otherwise leave it out.
   Verify: each sentence is specific and free of secrets and file contents.

3. **Pick a severity.** Use exactly one of `low`, `med` or `high`:
   - `low`: confusing or cosmetic. You continued without trouble.
   - `med`: you needed a workaround, or a step failed.
   - `high`: the task was blocked, or the wrong output was produced silently.
   Verify: the value is one of the three allowed values.

4. **Run the script** from the user's current directory, using the script's full path. `$SKILL_DIR` stands for this skill's base directory, which Claude Code shows when the skill loads ("Base directory for this skill: …"):
   ```bash
   python3 "$SKILL_DIR/scripts/log_papercut.py" \
     --skill "deploy-helper" \
     --skill-version "1.2.0" \
     --tried "Run the skill's step 3 build command" \
     --expected "make build produces dist/app" \
     --happened "make: *** No rule to make target 'build'. Stop." \
     --suggested-fix "Step 3 should say 'make all'; the Makefile has no build target" \
     --severity med
   ```
   Leave out `--skill-version` and `--suggested-fix` if you do not know them.
   Verify: the output contains `papercut <id> filed for <skill>`.

5. **Tell the user in one line, then continue.** For example: `Filed papercut <id> for deploy-helper (make build target missing).` Then go on with the user's task. If the papercut was the whole request, stop here.
   Verify: your reply has exactly one line about the papercut, and the user's task is not dropped.

## Error recovery

| Symptom | Action |
|---|---|
| stderr shows `could not write the papercut inbox (...); report: {...}` | The sandbox could not write the inbox. Tell the user that the papercut could not be saved, and include the echoed JSON line in your reply. Do not write the file yourself. |
| `error: the following arguments are required: ...` | Add the missing `--skill`, `--tried`, `--expected` or `--happened` flag, then run the script again. |
| `error: argument --severity: invalid choice` | Use `low`, `med` or `high`. Do not use `medium` or `critical`. |
| `python3: command not found` | Try `python` instead. If that also fails, tell the user the papercut in one line and continue. |
| `No such file or directory` for the script | Use the full path to this skill's `scripts/log_papercut.py`. Do not recreate the script. |
| Unsure whether the problem deserves a papercut | File it with `--severity low`. A duplicate report costs less than a lost one. |

<!-- sf:spec-sha256=e6aed1a40fac70b608862830cdcdd8fea62b502ea39a502bd6bd1f888043172f -->
