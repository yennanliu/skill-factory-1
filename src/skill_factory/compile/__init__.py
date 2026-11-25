"""`sf compile` / `sf new`: spec.yaml is the source of truth; SKILL.md and draft evals are compiled.

The compiler asks Claude (structured output) for the description, the SKILL.md body and draft eval
cases, then lints the result and feeds failures back (up to 2 retries). Nothing is overwritten
silently: output goes to SKILL.md.new plus a diff until `--apply`.
"""
