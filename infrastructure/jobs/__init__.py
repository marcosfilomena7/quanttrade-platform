"""Scheduled operational jobs — the pieces TASKS.md's Phase 1 repeatedly
calls "a daily job" / "a job that...".

These wire concrete infrastructure together (a venue REST client, a
database connection) to do one batch unit of work per invocation.
Nothing here implements a scheduler: TASKS.md's "schedule to run daily"
is an operational/deployment concern (cron, a systemd timer, an
orchestrator) external to this codebase's runtime — this package only
implements what each job does once invoked.
"""
