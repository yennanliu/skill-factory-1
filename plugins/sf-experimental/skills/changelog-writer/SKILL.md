---
name: changelog-writer
description: Turns git log output into a user-facing Keep a Changelog release section (Added/Changed/Fixed…, breaking changes first). Use when the user says "write the changelog for these commits", "draft release notes from this git log", or "update CHANGELOG.md for version X".
allowed-tools: Read
---

# Changelog Writer

Turn a list of commits into one release section in [Keep a Changelog](https://keepachangelog.com) format. Write it for the project's **users**: what they can now do, what behaves differently and what was fixed. Do not describe how the code changed inside.

## Rules

- Use only `Read`, and only to look at an existing `CHANGELOG.md` or a commit list the user points to. Never use `Write`, `Edit` or `Bash`. Never run `git log` yourself. Return the section in your reply, and the user pastes it in.
- Never make up a version, a date, a PR number or a commit hash. If the version or date is missing, ask for it.
- Each entry must come from a real commit in the input.

## Workflow

1. **Collect the inputs.** You need three things: the commit list (git log output), the version (for example `1.4.0`) and the release date (`YYYY-MM-DD`). If the version or the date is missing, ask one short question for both and stop until the user answers. If the user mentions `CHANGELOG.md` and the file exists, `Read` it to see the previous version and the existing style.
   Verify: you have a commit list, a version and a date in `YYYY-MM-DD` form, and none of them is a guess.

2. **Parse each commit.** For each line or block, note:
   - short hash (first 7 characters)
   - type and optional scope from a Conventional Commit prefix (`feat(api): ...`)
   - breaking flag: `!` before the colon (`feat!:`), or a `BREAKING CHANGE:` footer
   - PR reference such as `(#42)` in the subject, or `Merge pull request #42`
   If a commit has no prefix, work out the type from its verb: "Add …" means feat and "Fix …" means fix.
   Verify: every commit has a hash or a PR number, and a type.

3. **Drop the noise.** Leave out `chore`, `ci`, `test`, docs-only (`docs`) and merge commits (`Merge branch …`, `Merge pull request …`). If a merge commit holds the only PR number for a change you keep, use that number. Leave out `style` and `build` commits too, unless users would notice the change.
   Verify: no kept entry comes from a chore, ci, test, docs or merge commit.

4. **Map each commit to a section.**

   | Commit | Section |
   |---|---|
   | `feat` | Added |
   | `fix` | Fixed |
   | `perf` / `refactor` that changes behaviour users can see | Changed (drop it if nothing changes for users) |
   | `revert` of a released feature | Removed |
   | `revert` that undoes a bad change | Fixed |
   | any type marked breaking (`!` or `BREAKING CHANGE`) | Changed, listed first |
   | a commit that marks something as deprecated | Deprecated |
   | a commit that removes a feature, flag or API | Removed |
   | a vulnerability or security fix (CVE, XSS, injection, auth bypass) | Security |

   Verify: each kept commit is in exactly one section.

5. **Rewrite each entry for users.** Write one sentence in plain words about the effect, not the code. End it with the reference in parentheses: the PR number if there is one, otherwise the short hash.
   - `feat(export): add csv writer (#42)` → `You can now export reports as CSV files. (#42)`
   - `fix: null deref in parseDate when tz missing` → `Dates without a time zone no longer crash the importer. (abc1234)`

   Start breaking entries with `**Breaking:**` and say what users must do, for example `**Breaking:** The \`--out\` flag is now \`--output\`; update your scripts. (#57)`.
   Verify: each entry is one sentence, mentions no internal function or file names unless users call them directly, and ends with `(#n)` or `(hash)`.

6. **Assemble the section.** Use this layout. Include only sections that have entries, in this fixed order:
   ```markdown
   ## [1.4.0] - 2026-11-20

   ### Added
   - ...
   ### Changed
   - **Breaking:** ...
   - ...
   ### Deprecated
   ### Removed
   ### Fixed
   ### Security
   ```
   Put breaking entries at the top of `### Changed`.
   Verify: the heading matches `## [<version>] - <YYYY-MM-DD>` exactly, there are no empty subsections, and the order is Added, Changed, Deprecated, Removed, Fixed, Security.

7. **Hand it over.** Reply with the section in one fenced `markdown` block. Under it, add one line listing which commits you left out and why (for example `Left out: 3 chore, 1 ci, 2 merge.`). If the user asked to update `CHANGELOG.md`, tell them to paste the section above the previous release (and below `## [Unreleased]`, if the file has one). Do not edit the file.
   Verify: the reply has exactly one release section, and no file was created or changed.

## Error recovery

| Symptom | Action |
|---|---|
| Version or date not given | Ask for both in one question. Do not use today's date or the next semver number on your own. |
| Date given in another format (`Nov 20, 2026`) | Convert it to `2026-11-20`. If it is ambiguous (`03/04/2026`), ask. |
| User asks you to run `git log` or edit `CHANGELOG.md` | Explain that this skill only reads and drafts. Ask them to paste the log, or paste the section into the file themselves. |
| Every commit is chore/ci/test/docs/merge | Do not invent entries. Tell the user there are no user-facing changes, and ask whether they still want a section. |
| Commit subject is too vague to explain to users (`fix stuff`) | Use the commit body if there is one. Otherwise write the most cautious accurate sentence and flag it under the block for the user to check. |
| Not sure whether a refactor/perf commit changes behaviour | Leave it out and mention it in the "Left out" line so the user can decide. |
| `CHANGELOG.md` not found when asked to update it | Draft the section anyway and say the file was not found. Do not create it. |

<!-- sf:spec-sha256=6e668e1274dfe87a43e041cf7476ed0e530ba4e89a5a86a170c46513de5224e9 sf:version=0.1.0 -->
