"""
Platform replay service hooks.

Each module exposes one or two public functions that the PlatformBacktestRunner
calls directly — never through CLI subprocesses.

Naming conventions:
  run_<domain>_at_timestamp(*, session, timestamp, ctx)   → mutating tick
  snapshot_<domain>_at_timestamp(*, session, timestamp, ctx) → read-only
  apply_<domain>_event(*, session, timestamp, event, ctx) → operator event
  inject_<failure>(*, session, ...)                        → test failure injection
"""
