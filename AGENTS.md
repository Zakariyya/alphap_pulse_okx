# AlphaPulse Codex Rules

## Backtest Time Semantics

- ATR and all signal indicators must use **only the previous closed bar**.
- Do not use the current unfinished bar for indicator values or signal decisions.
- Signal generation uses `bar[t-1]` data; execution happens at `bar[t]`.
- This rule applies to all current and future strategies in this project and does not require repeated reminders.
