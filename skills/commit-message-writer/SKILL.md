---
name: commit-message-writer
description: 'Writes a Conventional Commits message for a diff: type(scope): subject header, a body that explains why, and a BREAKING CHANGE footer when needed. Use when the user asks "write a commit message for this diff", "what should the commit message be?" or "commit this change with a good message".'
allowed-tools: Read
---

# Commit Message Writer

Write one git commit message for a diff. The message follows Conventional Commits and the house rules, so history stays greppable and changelogs can be generated from it. Reply with the message only, in one fenced block.

## Rules

- Use only `Read`, and only to open a diff or patch file the user names. Never use `Bash`, `Write` or `Edit`. This skill does not run git, create commits or change files.
- When the user says "commit this change", write the message and let them run the commit. Never run `git commit` yourself.
- Never add `Co-Authored-By`, `Signed-off-by` or other trailers you were not asked for. Never use emoji, and never write "This commit ...".
- Describe only what the diff shows. Do not invent ticket numbers, issue links or motives the diff and the user's words do not support.

## Message format

```text
type(scope)!: imperative subject without trailing period

Why the change was made, wrapped at 72 characters. Explain the
problem or motivation and the effect, not a list of touched files.

BREAKING CHANGE: what breaks and what callers must do instead
```

- `type` is lowercase and is one of: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`.
- `(scope)` is optional. Use a short lowercase module or area name, for example `(auth)`.
- `!` and the `BREAKING CHANGE:` footer appear only for breaking changes, and always together.

## Workflow

1. **Get the diff.** Use the diff pasted in the conversation. If the user names a diff or patch file, open it with `Read`. If there is no diff, ask them to paste the output of `git diff --staged` and stop until they do.
   Verify: you have the actual changed lines, not just a summary of the change.

2. **Understand the change and why it was made.** Read the removed and added lines. Note the user-visible effect and the likely reason, using code comments, test names and the user's own words.
   Verify: you can state in one sentence what changed and why.

3. **Pick the type and scope.** New behaviour is `feat`. A bug correction is `fix`. Same behaviour with restructured code is `refactor`. A speed or memory improvement is `perf`. Only tests is `test`, only docs is `docs`, build or dependency files are `build`, CI config is `ci`, and other housekeeping is `chore`. Undoing an earlier commit is `revert`. Pick a scope only when one area clearly owns the change.
   Verify: the type is one of the ten allowed values and is all lowercase.

4. **Check for breaking changes.** Look for removed or renamed public functions, parameters, config keys, CLI flags, endpoints or env vars, changed return types or defaults, and dropped support. If any apply, add `!` before the colon and write a `BREAKING CHANGE: <what breaks>` footer that says what callers must change.
   Verify: `!` and the footer are either both present or both absent.

5. **Write the header.** Use `type(scope): subject`. Write the subject in the imperative mood ("add", "fix", "remove", not "added" or "adds"), with no trailing period.
   Verify: the whole header line is at most 72 characters and fits the pattern `^(feat|fix|docs|refactor|perf|test|build|ci|chore|revert)(\([a-z0-9-]+\))?!?: .+[^.]$`.

6. **Write the body.** Leave one blank line after the header. In 1 to 4 short sentences or bullets, say why the change was needed and what it changes in behaviour. Do not list file names. Wrap the lines at 72 characters.
   Verify: no body line is longer than 72 characters, and the body answers "why?".

7. **Reply with the message only.** Put the header, blank line, body and any footer in one fenced `text` block. If the user asked you to commit, add one line after the block: "Run `git commit` and paste this message, or save it to a file and use `git commit -F <file>`."
   Verify: there is exactly one fenced block, with no emoji, no `Co-Authored-By` and no "This commit".

## Error recovery

| Symptom | Action |
|---|---|
| No diff in the conversation | Ask the user to paste `git diff --staged` output. Do not guess the change. |
| The diff mixes unrelated changes | Write one message for the main change and suggest, in one line after the block, splitting the commit. |
| The header is longer than 72 characters | Drop the scope, shorten the subject, and move detail into the body. |
| Unsure between `feat` and `fix` | Use `fix` if existing behaviour was wrong, and `feat` if callers gain something new. |
| The reason for the change is unclear from the diff | Describe the effect in the body and ask the user for the motive in one line after the block. Do not make one up. |
| The user asks you to run the commit | Explain that this skill does not run git, and give the message with the commit command for them to run. |
| The diff only reverts an earlier commit | Use `revert: <original header>` and in the body give the reverted hash, if shown, and why. |

<!-- sf:spec-sha256=f436fbcd8dc5e4fcca6b63f3eb84b4b3abdba9a12930bd223c9b44d834392053 -->
