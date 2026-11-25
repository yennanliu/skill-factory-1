---
name: cron-explainer
description: Explains a standard 5-field cron expression field by field, summarises it in plain English, and works out its next 3 run times from a given "now". Use when asked "what does this cron expression mean?", "when will '<cron>' run next?", or "explain this crontab line".
allowed-tools: Read
---

# Cron Explainer

Explain a standard 5-field cron expression and list its next 3 run times. Work out the run times step by step. Never guess them. This skill only reads. It creates and changes no files.

## Rules

- Use `Read` only, and only when the user points to a file such as a crontab. Never use `Write`, `Edit` or `Bash`. Do not run `date`, `crontab` or any script to check your work. Work it out by hand.
- **If the user gives no "now", ask for it.** Do not assume today's date or the system clock. You can still give the table and summary, but list no run times until the user gives a "now".
- Use the timezone the user states. If they state none, use UTC and say so.
- Run times are strictly **after** "now". If "now" itself matches, it does not count.

## Field reference

| # | Field | Range | Notes |
|---|---|---|---|
| 1 | minute | 0-59 | |
| 2 | hour | 0-23 | 24-hour clock |
| 3 | day of month | 1-31 | A day that does not exist in a month is skipped (for example 31 in April) |
| 4 | month | 1-12 | `JAN`-`DEC` are allowed |
| 5 | day of week | 0-7 | 0 and 7 are both Sunday. `SUN`-`SAT` are allowed |

Syntax: `*` means every value. `a-b` is a range. `a,b,c` is a list. `*/n` means every n-th value from the start of the range. `a-b/n` means every n-th value from a up to b.

## Workflow

1. **Get the expression, the "now" and the timezone.** Take the cron line from the message, or `Read` the file the user names. In a crontab line, the first 5 fields are the schedule and the rest is the command. Note the "now" and the timezone if the user gave them.
   Verify: you have exactly 5 schedule fields. If the user did not give a "now", plan to ask for one in step 6.

2. **Expand each field into its set of values.** For example `*/15` in minutes gives {0,15,30,45}, and `1-5` in day of week gives {Mon..Fri}. Change 7 in day of week to 0. Change month and day names to numbers.
   Verify: every value is inside the field's range. If any value is out of range, or the syntax is not standard (`L`, `W`, `#`, `?`, a 6th seconds field, `@reboot`), follow the error table.

3. **Decide how days are matched.** A field is *restricted* when it does not start with `*`.
   - If only day of month is restricted, a day matches when its date is in the day-of-month set.
   - If only day of week is restricted, a day matches when its weekday is in the day-of-week set.
   - **If both are restricted, a day matches when EITHER one matches (OR).** Say this in your reply.
   Verify: you have written down which of the three cases applies.

4. **Build the table and summary.** Give one row per field: field, value, meaning. Then write one plain-English sentence, for example "Every 15 minutes from 09:00 to 17:45, Monday to Friday."
   Verify: the table has exactly 5 rows, in the order minute, hour, day of month, month, day of week.

5. **Compute the next 3 run times.** Start one minute after "now", in the stated timezone. Then:
   1. Walk forward one day at a time. Keep a day only if its month is in the month set and it passes the day rule from step 3. Remember leap years: February has 29 days when the year divides by 4, except centuries that do not divide by 400.
   2. On a kept day, list the times (hour in set × minute in set) in ascending order. On the starting day, list only the times later than "now".
   3. Collect times until you have 3.
   Work out weekdays by counting from a known date. 2026-01-01 is a Thursday, and 2027-01-01 is a Friday. Write the weekday beside each date you check.
   Verify: check each result again: the minute, hour and month are in their sets, the day passes the day rule, the weekday is correct, and the time is after "now".

6. **Reply in this format.**
   ```text
   | Field | Value | Meaning |
   |---|---|---|
   | minute | */15 | every 15 minutes (0, 15, 30, 45) |
   ...

   Summary: <one sentence>

   Next 3 runs after <now> (<timezone>):
   1. YYYY-MM-DD HH:MM
   2. YYYY-MM-DD HH:MM
   3. YYYY-MM-DD HH:MM
   ```
   Add the OR note when step 3 found both day fields restricted. If no "now" was given, replace the list with: "What date and time should I count from (and which timezone)?"
   Verify: the times use zero-padded `YYYY-MM-DD HH:MM`, the list is numbered, and the timezone is named.

## Error recovery

| Symptom | Action |
|---|---|
| No "now" given | Give the table and summary, then ask for the start time and timezone. Do not use today's date. |
| 6 fields, or the first looks like seconds | Say that this is not standard 5-field cron (it may be Quartz or a seconds-based format). Ask which format they use before you explain it. |
| `L`, `W`, `#` or `?` in a field | Say that these are non-standard extensions (Quartz and similar). Explain the standard fields only if the user wants that. |
| `@daily`, `@hourly`, `@weekly` and so on | Expand the shortcut (for example `@daily` = `0 0 * * *`) and continue. `@reboot` has no next run times, so say so. |
| A value is out of range (for example minute 60, or month 13) | Name the field and say that the expression is invalid. Do not compute run times. |
| The schedule never fires (for example `0 0 30 2 *`) | Say that no matching date exists, and why. Do not list run times. |
| A run time falls in a DST gap or overlap | Say that the result depends on how that cron daemon handles DST, and give the wall-clock time. |
| Unsure about a weekday | Count again from 2026-01-01 (Thursday). Do not guess. |

<!-- sf:spec-sha256=8ac9ef192f7284afd82259787b4b720181f62b7a35ca849e39ab13b9af9100af sf:version=0.1.0 -->
