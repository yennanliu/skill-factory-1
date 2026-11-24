"""`sf eval`: run a skill's eval suite through `claude plugin eval` and judge the pass rates.

skills/<name>/evals/ holds cases in Claude Code's own eval format (case.yaml or prompt.md +
graders/*.md). sf stages the skill as a one-skill plugin, runs the official harness (which adds a
no-plugin baseline arm), and turns the result into pass rates with confidence intervals checked
against the spec's evals.min_pass_rate / max_pass_rate.
"""
