# Audit: safety/ and runtime/

## Verified counts

Command: `find src/autonomous_trading_platform/safety -type f -name "*.py" | xargs wc -l | tail -1`
Output: **1939 total**, 24 files (matches task scope statement).

Command: `find src/autonomous_trading_platform/runtime -type f -name "*.py" | xargs wc -l | tail -1`
Output: **3108 total**, 21 files (matches task scope statement).

Command: `grep -rn -E "TODO|FIXME|XXX" src/autonomous_trading_platform/safety/` → **0 matches**
Command: `grep -rn -E "TODO|FIXME|XXX" src/autonomous_trading_platform/runtime/` → **0 matches**

### platform_replay location

`platform_replay/` is **NOT under `runtime/`**. It lives at
`src/autonomous_trading_platform/application/services/platform_replay/` (application layer, out of
this audit's scope). It actually contains **19** Python files (not 14 as claimed), totalling
**4,008 LOC**: `admin_hooks.py, controls_hooks.py, diagnostics_hooks.py, execution_hooks.py,
failure_injection.py, features_hooks.py, governance_hooks.py, ingestion_hooks.py,
initial_state_hooks.py, operations_hooks.py, portfolio_hooks.py, research_hooks.py, risk_hooks.py,
runtime_hooks.py, safety_hooks.py, settings_hooks.py, strategy_hooks.py, universe_hooks.py,
__init__.py`. Related non-Python artifacts also found: `contracts/runtime/platform_replay.py`,
`platform/replay/platform_replay_config.py`, `fixtures/platform/replays/base/base_platform_replay.yaml`,
`artifacts/platform/backtests/base_platform_replay.json`. Since it is out of scope for this pass, it
was not read file-by-file — flagging its true location/file-count for whoever owns that section.

---

## safety/ — per-file entries (24/24 read)

### src/autonomous_trading_platform/safety/__init__.py (0 lines)
- Purpose: empty package marker.

### src/autonomous_trading_platform/safety/environment_policy.py (37 lines)
- Purpose: `EnvironmentSafetyPolicy.assert_environment_allowed()` implements the build/config gate for
  LIVE trading; `assert_account_allowed()` implements the account-allowlist gate for both PAPER and
  LIVE.
- Notable: Three chained checks for LIVE in one method (lines 13-22): `no_live_trading` flag first,
  then `enable_live_trading`, then `include_live_modules` (a **build-time** inclusion flag, distinct
  from the runtime config flag) — this is the "environment/build gate" referenced in the
  differentiation claim. Account allowlisting (lines 24-37) is symmetric for PAPER and LIVE: if an
  allowlist is configured and non-empty, the account must be in it, for *either* environment (not
  just live) — i.e., paper accounts can also be restricted to an allowlist, which is a nice
  belt-and-suspenders touch since sending real orders to an unexpected paper account is still a real
  operational risk.

### src/autonomous_trading_platform/safety/errors.py (116 lines)
- Purpose: defines the full `SafetyError` hierarchy — one error subclass per gate/limit type
  (`LiveTradingBlockedError`, `RuntimeGateNotArmedError`, `KillSwitchEnabledError`,
  `BuildGateDisabledError`, `ConfigGateDisabledError`, plus 10 risk/throttle/idempotency errors).
- Notable: `PortfolioSymbolExposureLimitExceededError` and `SectorConcentrationLimitExceededError`
  carry rich structured attributes (symbol, strategy_id, current/projected exposure, limit) used both
  for human-readable messages and for downstream structured logging/audit metadata — errors double as
  data-transfer objects for the audit trail, not just control flow.

### src/autonomous_trading_platform/safety/contexts/build_safety_context.py (103 lines)
- Purpose: composition-root factory (`build_safety_context`) that wires all safety services together
  per request/session — constructs both kill-switch repos, all gate services, throttle/idempotency
  services, and pre-trade risk service, and returns a `SafetyContext` dataclass.
- Notable: Line 49 — `kill_switch_service.emit_startup_audit_event(...)` is invoked **immediately at
  construction time**, so every time a safety context is built (e.g., per trading cycle / per
  request) an audit event records the kill-switch state as loaded from the SOR — this is one of the
  concrete "every breach/state emits an audit event" touchpoints, though this one runs unconditionally
  on every context build, not just breaches.

### src/autonomous_trading_platform/safety/contexts/safety_context.py (28 lines)
- Purpose: frozen-shape (not literally frozen, just a `@dataclass`) bundle exposing the 8 safety
  services (kill switch, live-trading gate, idempotency, throttle, pre-trade risk, runtime gate,
  runtime trading guard, shadow mode) as one object for callers.
- Notable: pure data-carrier, no logic — clean separation between wiring (build_safety_context.py) and
  shape (this file).

### src/autonomous_trading_platform/safety/models/kill_switch_state.py (10 lines)
- Purpose: frozen dataclass `KillSwitchState(enabled, reason, updated_by, updated_at)` — the in-memory
  DTO used by the (unused-in-production) in-memory `KillSwitchRepository` stub below.
- Notable: this is distinct from the real, DB-backed ORM model
  `storage/sor/models/kill_switch_state.py` (out of scope dir, confirmed via read) which has more
  fields (`is_enabled`, `cleared_by`, `cleared_at`, `created_at`, singleton `id`). Two
  differently-shaped "KillSwitchState" types exist in the codebase under similar names — a minor
  naming collision risk for anyone grepping.

### src/autonomous_trading_platform/safety/readers/__init__.py (0 lines)
- Purpose: empty package marker.

### src/autonomous_trading_platform/safety/readers/order_activity_reader.py (52 lines)
- Purpose: `StubOrderActivityReader` — a no-op/always-empty implementation of the order-activity read
  interface (idempotency key checks, order counts per hour/bar/symbol) used presumably for tests or as
  a null-object default.
- Notable: Every method returns `False`/`0` — a permissive stub. If ever wired into a real path by
  mistake (rather than test scaffolding), idempotency and throttling would silently no-op. No
  docstring warns of this risk explicitly, though the name `Stub...` signals intent.

### src/autonomous_trading_platform/safety/readers/portfolio_risk_state_reader.py (219 lines)
- Purpose: `PortfolioRiskStateReader` computes per-symbol / gross / net USD exposure and pct-of-equity
  from the latest `PositionSnapshot`/`CashSnapshot` rows, with deterministic tie-breaking for "latest"
  snapshot selection.
- Notable: `_position_source_priority()`/`_cash_source_priority()` (lines 206-219) use a SQL `CASE` to
  prefer `LEDGER`-sourced snapshots over `BROKER_RECONCILED` over anything else when timestamps tie —
  a deliberate precedence rule for reconciliation conflicts. `get_net_exposure_usd` signs exposure by
  position direction (long positive / short negative) via `_signed_symbol_exposure`, while gross
  exposure sums absolute values — correct distinction for risk math. Explicitly stateless/immutable by
  design per class docstring (build fresh each cycle).

### src/autonomous_trading_platform/safety/readers/risk_state_reader.py (20 lines)
- Purpose: `StubRiskStateReader` — no-op implementation of gross/symbol exposure, daily notional,
  reserved cash, position qty lookups.
- Notable: same "permissive stub" pattern as order_activity_reader; real implementation must live
  elsewhere (not found in this scope) or be constructed dynamically — worth noting for the interfaces
  agent since `PreTradeRiskService` depends on this interface directly for its numeric risk math.

### src/autonomous_trading_platform/safety/readers/sector_exposure_reader.py (137 lines)
- Purpose: `SectorExposureReader` aggregates symbol-level exposure into sector buckets given a
  symbol→sector map, tracks unmapped symbols, and computes over/near-limit sector lists.
- Notable: Unmapped symbols are explicitly bucketed under `UNKNOWN_SECTOR` and tracked in
  `_unmapped_symbols`/`get_unmapped_symbols()` — surfaced to `PreTradeRiskService`'s
  `unknown_sector_policy` (reject/use_unknown_bucket/warn_allow), giving an operator-configurable
  fail-open vs fail-closed choice for missing sector metadata. `near_limit_sectors` threshold is
  hardcoded at 80% utilization (line 124) — reasonable but not configurable from this class.

### src/autonomous_trading_platform/safety/repositories/__init__.py (0 lines)
- Purpose: empty package marker.

### src/autonomous_trading_platform/safety/repositories/kill_switch_repository.py (34 lines)
- Purpose: `KillSwitchRepository` — a plain **in-memory** kill switch store (resets to `enabled=False`
  on construction).
- Notable: **This is not what's actually used for the kill switch in the wired path.**
  `build_safety_context.py` imports and uses
  `storage.sor.repositories.core.kill_switch_state_repository.KillSwitchStateRepository` (DB-backed),
  not this class. This in-memory repo appears to be either dead code, a legacy/pre-persistence
  version, or a test double — grepped for usages; only this file defines it and nothing in
  `build_safety_context.py`/`kill_switch_service.py` references it. Worth flagging: the differentiated
  "DB-persisted kill switch survives restarts" claim is validated by the SOR-backed repository, not
  this file — this file, if presented as evidence of persistence, would be **misleading** since it's
  pure in-memory. Recommend the portfolio writeup cite
  `storage/sor/repositories/core/kill_switch_state_repository.py` (verified: singleton row via
  `session.get(KillSwitchState, KILL_SWITCH_SINGLETON_ID)`, `session.flush()` on every enable/disable,
  survives process restart because it's a normal Postgres row) rather than this stub.

### src/autonomous_trading_platform/safety/services/__init__.py (0 lines)
- Purpose: empty package marker.

### src/autonomous_trading_platform/safety/services/kill_switch_service.py (104 lines)
- Purpose: `KillSwitchService` wraps the DB-backed `KillSwitchStateRepository` (SOR) plus optionally a
  `RuntimeControlStateRepository`, exposing `enable`/`disable`/`is_enabled`/`assert_not_enabled` and an
  `emit_startup_audit_event` used at context-build time.
- Notable: Docstring (lines 15-26) is explicit about the persistence design intent: "State is
  persisted on every enable/disable call and read directly from the database on every check, so it
  survives service restarts, deploys, and scheduler restarts" — directly substantiates differentiation
  claim (1)'s "DB-persisted kill switch surviving restarts". Dual-write pattern: writes go to both the
  dedicated `KillSwitchState` table and `RuntimeControlState.kill_switch_enabled` (lines 40-45, 51-56)
  so a different subsystem (trading-cycle block check) can keep reading from
  `RuntimeControlState` without structural changes — intentional but does introduce a
  two-tables-must-stay-in-sync risk if one write fails after the other partially succeeds (no
  transaction-boundary comment/enforcement visible in this file; whether both writes share one
  session/transaction depends on caller-supplied `session`).

### src/autonomous_trading_platform/safety/services/live_trading_gate_service.py (115 lines)
- Purpose: `LiveTradingGateService.assert_live_trading_allowed(account_id)` is the **single chained
  gate-check entrypoint**: environment/build/config policy → account allowlist → runtime armed gate →
  kill switch, in that literal order (lines 53-68).
- Notable: This is the clearest single piece of evidence for differentiation claim (1) (four
  independent chained gates). Exception translation (lines 56-65) maps a generic
  `EnvironmentIsolationError` message string back into specific typed errors
  (`BuildGateDisabledError`, `ConfigGateDisabledError`, else generic `LiveTradingBlockedError`) by
  substring-matching the message text — functionally works but is a code smell (string-matching
  exception messages to reclassify error types is brittle; a change in `environment_policy.py`'s
  message wording would silently break this without a test catching it structurally). `get_gate_status`
  (lines 80-115) is a nice diagnostic surface returning independent boolean pass/fail per gate for a
  status/CLI endpoint, each try/except swallowing generically (`except Exception`) which is broad but
  intentional here (status reporting, not control flow).

### src/autonomous_trading_platform/safety/services/order_idempotency_service.py (116 lines)
- Purpose: builds a deterministic idempotency key (`sha256` of run_id|strategy_id|bar_timestamp|
  symbol|side|qty) and checks/records duplicate submission within a configurable time window,
  emitting an audit event either way (duplicate-detected or check-passed).
- Notable: Emits `ORDER_IDEMPOTENCY_CHECK_PASSED` audit events even on the **success** path (lines
  91-114), not just failures — heavier audit-trail volume than a typical implementation but consistent
  with the "every breach (and here, every check) emits structured audit events" differentiation
  narrative. Idempotency key construction requires `qty` and raises `ValueError` if missing —
  fail-closed on malformed input rather than silently hashing `None`.

### src/autonomous_trading_platform/safety/services/order_throttle_service.py (128 lines)
- Purpose: enforces max-orders-per-hour, max-orders-per-bar, and same-bar-repeat-order blocking, using
  an in-process `threading.Lock` plus an in-memory "reservation" ledger to account for orders that are
  in-flight but not yet persisted (so concurrent submissions within the same process don't race past
  the persisted-order count).
- Notable: The reservation mechanism (`_reserved_orders_by_bar`, `_reserved_orders_by_timestamp`,
  `_reserved_repeat_keys`) is a deliberate defense against a check-then-act race between reading
  persisted order counts and the DB write actually landing — combines persisted counts (via
  `order_activity_reader`) with these in-memory reservations for an "effective" count (lines 52-53,
  63-64). This only protects against races **within a single process** (module-level Lock, no
  cross-process/distributed lock) — fine for a single-scheduler-instance deployment, a limitation
  worth noting if the platform ever runs multiple scheduler replicas.

### src/autonomous_trading_platform/safety/services/pre_trade_risk_service.py (504 lines, largest file in safety/)
- Purpose: the core pre-trade risk gate — checks gross exposure, per-symbol exposure, daily notional
  traded, reserved cash capacity, portfolio-level symbol exposure (USD and/or pct), and sector
  concentration, all before an order is allowed.
- Notable: Six independent limit checks in `assert_order_allowed` (lines 73-130), each raising a
  distinct typed error. Exposure-delta math (`_calculate_exposure_delta`,
  `_calculate_portfolio_symbol_exposure_delta`) correctly distinguishes "increasing a position"
  (adds full notional) from "reducing/flipping a position" (nets the notional against current
  exposure) rather than crudely summing signed notional — this is real portfolio-risk math, not a
  toy check. Sector-concentration handling has a configurable `unknown_sector_policy`
  (reject/use_unknown_bucket/warn_allow) with metrics emission (`metrics.missing_sector_metadata`,
  `metrics.sector_exposure_pct`, `metrics.sector_limit_utilization`,
  `metrics.sector_concentration_blocks`) plus structured logging AND an audit-log write on breach
  (lines 463-480) before raising `SectorConcentrationLimitExceededError` — directly substantiates
  differentiation claim (4) ("every breach emits structured audit events + metrics before raising").
  Same audit-then-raise pattern for `PortfolioSymbolExposureLimitExceededError` (lines 282-320). Note:
  the first three checks (gross/symbol/daily-notional, lines 95-112) do **not** emit audit events or
  metrics before raising — only the portfolio-symbol and sector-concentration checks do. So claim (4)
  is only partially true across all breach types in this file; the newer/richer checks have full audit
  wiring, the older/simpler ones don't.
  `_resolve_reference_price` (line 139-142) requires `limit_price` on the order intent and raises
  `ValueError` if absent — market orders without a limit price cannot be pre-trade-risk-checked by this
  path (a design constraint, not necessarily a bug, but worth flagging: any code path that submits
  market orders without a `limit_price` bypasses/errors on this gate).

### src/autonomous_trading_platform/safety/services/runtime_gate_service.py (83 lines)
- Purpose: `RuntimeGateService` — the in-memory "armed/disarmed" runtime gate, with optional
  expiration timestamp; `is_armed()` auto-disarms if the arming window has elapsed.
- Notable: This is the "armed/disarmed runtime gate" of differentiation claim (1) — confirmed
  in-memory/per-process (no persistence), which is an intentional contrast to the kill switch (DB
  persisted): arming is meant to be a short-lived, must-be-re-asserted-per-process state (a restart
  clears arming, forcing a human to re-arm), while the kill switch is a durable brake. This asymmetry
  is a sound safety design choice worth calling out explicitly in the writeup rather than treating both
  as the same kind of gate.

### src/autonomous_trading_platform/safety/services/runtime_trading_guard_service.py (119 lines)
- Purpose: `RuntimeTradingGuardService.assert_trading_mode_allowed()` — a broader fail-closed guard
  invoked before broker-backed execution, layering on top of `LiveTradingGateService`: validates
  environment is PAPER or LIVE, run_type matches configured environment, broker credentials are
  present, and (for PAPER) account allowlist, or (for LIVE) full live-trading-gate chain.
  - Notable: `_assert_run_type_matches_environment` (lines 78-97) cross-validates that a run's
  declared `RunType` (PAPER/LIVE) matches the configured `trading_environment` — guards against a
  config/call-site mismatch where e.g. a LIVE-tagged run object is executed under a PAPER-configured
  service or vice versa. `_assert_credentials_present` (lines 99-103) is environment-agnostic (checked
  for both paper and live) — sensible, since even paper trading against Alpaca needs valid API
  keys/secrets.

### src/autonomous_trading_platform/safety/services/shadow_mode_service.py (14 lines)
- Purpose: `ShadowModeService` — trivial wrapper exposing `settings.shadow_mode_enabled` as
  `is_enabled()` plus a suppression-reason string.
- Notable: This is the entirety of the "global shadow-mode toggle" (differentiation claim (3)). The
  service itself contains **no gating/suppression logic** — it's a pure read of a settings flag. The
  actual "compute but suppress" behavior (i.e., where an order is computed/routed through the pipeline
  but the final submit-to-broker call is skipped) must live in the execution/order-routing layer, not
  here. Within safety/'s scope this claim is only a data point, not a mechanism — could not verify the
  actual suppression behavior without reading execution/ (out of this agent's scope); flagging for
  whoever audits execution/ to confirm `shadow_mode_service.is_enabled()` is actually checked at the
  broker-submission call site.

---

## runtime/ — per-file entries (21/21 read)

### src/autonomous_trading_platform/runtime/__init__.py (0 lines)
- Purpose: empty package marker.

### src/autonomous_trading_platform/runtime/services/__init__.py (0 lines)
- Purpose: empty package marker.

### src/autonomous_trading_platform/runtime/clock.py (338 lines)
- Purpose: `TradingClock`/`MarketCalendar` abstractions — `RealTradingClock` (wall-clock, cannot be
  set/advanced, lines 72-80) vs `FakeTradingClock`/`HistoricalTradingClock` (settable, for
  replay/backtest); `HistoricalMarketCalendar` (weekday-only, used by replay/backtest, line 205) vs
  `RealMarketCalendar` (holiday-aware via `exchange_calendars` XNYS schedule, used by "the live/paper
  soak loop" per its own docstring, lines 296-306).
- Notable: `RealTradingClock.set_time`/`advance_to` explicitly `raise RuntimeError` (lines 76-80) — a
  structural guard preventing production/live code from ever being fed a manipulated clock; only the
  Fake/Historical variant is settable, so any code path that calls `.set_time()` on a real deployment
  clock fails loudly rather than silently drifting. `RealMarketCalendar`'s own docstring (lines
  296-306) is explicit that early-close days are "not yet handled, acceptable for paper trading" — a
  documented known gap for live-trading half-days. `_nyse_calendar()` lazily imports
  `exchange_calendars` (line 15) so the historical/replay path (no import needed) never pays that
  dependency cost.

### src/autonomous_trading_platform/runtime/interruptible_sleep.py (56 lines)
- Purpose: `InterruptibleSleeper` — SIGINT/SIGTERM-aware sleep utility for long-running scheduler/soak
  loops; wakes every 1s to check a shutdown flag instead of blocking for the full interval.
- Notable: `install_signal_handlers` (lines 40-56) wraps `signal.signal(...)` calls in
  `contextlib.suppress(OSError, ValueError)` so registration is safe from non-main threads (where
  Python forbids `signal.signal`) — a defensive touch for embedding the soak loop inside a larger
  process. Not itself a safety gate, but the mechanism that lets the live/paper soak loop shut down
  gracefully mid-cycle rather than being killed mid-write.

### src/autonomous_trading_platform/runtime/replay_debug.py (1456 lines, largest file in runtime/)
- Purpose: `RuntimeReplayDebugRunner` — a full synthetic trading-cycle simulator (market_backfill,
  features, trading, rebalance, portfolio_snapshot, corporate_actions, eod, runtime_checks) that
  exercises real SOR write paths, real risk-parameter wiring, and real job-tracking infra with
  synthetic prices/signals. Explicitly classified in its own module docstring (lines 1-23) as
  `integration_replay / debug_demo`, NOT for strategy research or production decisions; "All SOR rows
  written by this runner are tagged with DEBUG_REPLAY metadata."
- Notable — **NO_LIVE_TRADING runtime enforcement (claim 2) + paper-vs-live isolation (claim 5) directly
  evidenced here**: `_assert_local_safe()` (lines 387-391) is called first thing in `.run()` (line 299,
  before any DB session is even opened) and raises `RuntimeError` if
  `settings.trading_environment is TradingEnvironment.LIVE` (line 388) **or** if
  `not settings.no_live_trading` (lines 390-391, message literally `"runtime replay-debug requires
  NO_LIVE_TRADING=true"`). A second, DB-state-based guard, `_assert_control_state_safe()` (lines
  393-399), runs after the settings snapshot is loaded from Postgres (line 310) and independently
  refuses to proceed if the **persisted** `runtime_control.trading_mode` is `"live"` — i.e., even if
  process-level settings claim safety, a live value already written to the DB by another process still
  blocks this runner. Two independent checks (env/settings-time and DB-state-time) is a genuine
  belt-and-suspenders pattern, not just one flag check.
- Notable — kill-switch / trading-paused enforcement inside the simulated trading cycle itself:
  `_run_trading()` (lines 677-687) reads the same `runtime_control` block loaded from
  `RuntimeControlState` (via `load_settings_snapshot`, lines 457-506) and short-circuits with
  `skip_reason: "kill_switch_enabled"` / `"trading_paused"` / `"trading_disabled"` before generating any
  orders (lines 679-687) — this exercises the identical DB-backed kill-switch/pause fields the safety/
  and other runtime/ services read, giving the differentiation narrative a concrete "the kill switch
  actually stops a simulated trading cycle" demonstration, even though this is a debug/replay tool
  rather than the live production trading-cycle entrypoint (that entrypoint was out of this audit's
  scope — likely under `scheduler/` or `interfaces/cli`).
- Gap found — **shadow-mode claim (3) NOT substantiated in this file despite looking like it would be**:
  `RuntimeReplaySettings.shadow_mode_enabled` is declared in the settings Protocol (line 153) and is
  captured into the settings snapshot dict (line 473: `"shadow_mode_enabled": settings.shadow_mode_enabled`)
  and even echoed in a debug-summary print (~line 1406 region), but it is **never read/branched on**
  anywhere else in the 1456-line file — `grep` for `shadow` in this file only matches those two
  data-carrying occurrences. `_run_trading()` unconditionally proceeds to `_apply_fill(...)` and
  `_write_order_and_fill(...)` (lines 812-817) with no shadow-mode check gating the "write fill / would
  route to broker" step. So even the runtime/replay layer exercises risk gates (kill switch, drawdown,
  per-strategy drawdown) but does **not** exercise or verify the "compute but suppress" shadow-mode
  behavior — reinforces the safety/ agent's flag that shadow-mode suppression, if it exists, must live
  entirely in execution/ (unread by either agent so far) and is currently unverified end-to-end.
- Notable — audit-trail linkage: `_run_cycle()` (lines 401-433) wraps every simulated cycle in
  `RuntimeJobRunner.run(...)` (imported from `runtime/services/runtime_job_runner.py`), so every
  replay-debug cycle invocation produces a real `RuntimeJobRuns` row (running → completed/failed) via
  the shared production job-tracking mechanism — failures propagate to
  `PipelineFailureNotificationService` (line 406) exactly as a real scheduled job would, giving replay
  runs the same failure-visibility as production jobs.
- Minor: `_write_risk_snapshot()` (lines 1155-1199) persists `is_blocked`/`block_reasons` into a
  `RiskSnapshot` row on every kill-switch/pause/drawdown block, but does **not** additionally write an
  `AuditLogRow`/`AuditLogEvent` for these blocks (unlike `pre_trade_risk_service.py` in safety/, which
  does both audit-log-write-then-raise for its richer checks) — the risk-snapshot table alone carries
  the "why blocked" evidence for this file's breach paths, no separate audit-log entry. Consistent with
  the safety/ agent's finding that audit-then-raise wiring is inconsistent across the codebase, not
  applied uniformly to every breach type.
- No `if __name__ == "__main__"` / argparse entrypoint in this file — it's invoked as a library class,
  presumably from a CLI command elsewhere (out of scope for this audit pass).

### src/autonomous_trading_platform/runtime/services/replay_runtime_service.py (62 lines)
- Purpose: `ReplayRuntimeService` — thin wrapper over `RuntimeReplayDebugRunner` (line 27-32) that
  reuses the *exact same* execution engine but tags job names with `"runtime_replay"` instead of
  `"runtime_replay_debug"` (line 61), for "formally recorded replay verification runs (e.g. CI or
  scheduled verification pipelines)".
- Notable: Its own module docstring (lines 1-15) explicitly states "Same execution engine and synthetic
  price / synthetic-signal limitations as `RuntimeReplayDebugRunner`... DO NOT use this path for
  strategy research or production decisions" — i.e., this is **not** a separate, more-production-grade
  replay engine; it is purely a naming/job-tracking distinction over the same debug simulator. All the
  gaps found in `replay_debug.py` above (no shadow-mode enforcement, no audit-log-on-block) apply
  identically here since it delegates 100% of logic to `RuntimeReplayDebugRunner`. This is distinct from
  (and should not be confused with) `application/services/platform_replay/` noted by the safety/ section
  of this file as out-of-scope — that's a 19-file hook-based replay/fixture system for admin/governance
  scenario replay, unrelated to this trading-cycle simulator.

### src/autonomous_trading_platform/runtime/services/runtime_control_service.py (101 lines)
- Purpose: `RuntimeControlService` — thin service layer over `RuntimeControlStateRepository`
  (DB-backed, single global row) exposing `start_trading`/`stop_trading`/`pause_trading`/
  `resume_trading`/`enable_kill_switch`/`disable_kill_switch`/`update_trading_mode`, plus
  `get_cycle_block_reason(expected_trading_mode)`.
- Notable — **paper-vs-live isolation at the trading-cycle level (claim 5), independent evidence from a
  different layer than safety/'s `RuntimeTradingGuardService`**: `get_cycle_block_reason()` (lines
  86-101) checks, in priority order: kill switch → trading disabled → trading paused → **trading-mode
  mismatch** (`state.trading_mode != expected_trading_mode` → `"trading_mode_mismatch"`, lines 98-99).
  This means the persisted `trading_mode` on the single global `RuntimeControlState` row must match
  whatever mode the calling trading cycle declares itself to be running as, or the cycle is blocked —
  this is a second, independent paper/live cross-check mechanism at the runtime-control layer, distinct
  from (but complementary to) `safety/services/runtime_trading_guard_service.py`'s
  `_assert_run_type_matches_environment` check found by the prior agent.
- Notable: `start_trading()` (lines 18-26) has a subtle two-step, two-write pattern not present in the
  other setters: it calls `set_trading_enabled(enabled=True, ...)` then **also** manually sets
  `state.trading_paused = False` and calls `self.repository.session.flush()` directly (lines 24-25) —
  i.e., starting trading implicitly un-pauses it. No other method (`stop_trading`, `pause_trading`,
  etc.) has this side-effecting "also touch the other field" behavior, an asymmetry worth knowing:
  calling `stop_trading()` then `start_trading()` will silently clear a previously-set pause, whereas
  the reverse (pause then resume) leaves `trading_enabled` untouched — intentional-looking but
  undocumented via docstring/comment.
- Kill-switch here writes only to `RuntimeControlState.kill_switch_enabled` via `set_kill_switch`
  (lines 49-71) — this is the **same dual-write target** the safety/ section's
  `KillSwitchService` also writes to (`RuntimeControlState.kill_switch_enabled`, alongside the dedicated
  `KillSwitchState` table) — confirms two independent call paths (`safety.KillSwitchService` and
  `runtime.RuntimeControlService`) can both toggle the same `RuntimeControlState` row's kill-switch
  field, which is consistent with the safety/ agent's flagged two-tables-must-stay-in-sync risk, now
  additionally a two-services-must-stay-in-sync risk if both are ever called independently without
  coordinating on the `KillSwitchState` table.

### src/autonomous_trading_platform/runtime/services/runtime_job_runner.py (169 lines)
- Purpose: `RuntimeJobRunner.run()` — generic job-execution wrapper: writes a `RuntimeJobRun` row as
  `"running"` before executing the callable, then `"completed"`/`"failed"`/`"skipped"` after, with
  duration_ms, error_message, and correlation/parent-run-id propagation via ambient
  `observability.runtime_context`.
- Notable: On failure (lines 107-129), saves the failed run row **then** calls
  `self._notify_failure(failed_run)` (line 127) **before** re-raising (line 129) — structured
  audit/notification-before-propagation pattern, consistent with differentiation claim (4)'s "emits
  before raising" pattern, though here it's a pipeline-failure notification rather than a risk-limit
  breach specifically. `_notify_failure` (lines 155-169) itself wraps the notifier call in a bare
  `try/except Exception` that only logs a warning (lines 159-169) — a failure to notify never masks or
  replaces the original exception, and never crashes the job runner itself; sound isolation between
  "the job failed" and "telling someone the job failed."
- Ambient context propagation (lines 48-54, 100-105): if a `runtime_context` is already active (e.g. a
  parent replay/scheduler job), child `job_run_id`/`correlation_id` are inherited automatically unless
  explicitly overridden — enables the parent/child job hierarchy seen in `replay_debug.py`'s per-cycle
  `RuntimeJobRunner.run()` calls nesting inside the runner-level context.

### src/autonomous_trading_platform/runtime/services/audit_logging_service.py (238 lines)
- Purpose: `AuditLoggingService` — general-purpose structured audit-event writer
  (`record_run_started/completed/failed`, `record_sla_breach`, `record_event`, plus
  market-bar-specific `record_bar_missing/late/outlier` and corporate-action-specific
  `record_corporate_action_parse_failed/validation_failed/adjustment_applied`), all funneling through
  `_record_event()` → `AuditLogEvent` → `SorUnitOfWork(...).audit_logs.add(event)`.
- Notable — **secret redaction on every audit write**, a differentiation-adjacent finding not called
  for by any of the four claims but directly relevant to "structured audit events" being safe to store/
  export: `_SENSITIVE_KEYS` (lines 14-23: `api_key, access_token, refresh_token, token, password,
  secret, client_secret, authorization`) is checked recursively by `_sanitize_value()` (lines 127-144)
  across nested dicts/lists/tuples in the metadata payload — any matching key (case-insensitive, line
  129) is replaced with the literal string `"[REDACTED]"` before the event is ever persisted. This is
  the general-purpose sibling to `runtime_job_runner`'s narrower per-call sites; every caller of
  `AuditLoggingService` gets this scrubbing for free, reducing the risk of a credential leaking into the
  audit trail via a careless metadata dict.
- This is the generic engine that presumably backs `record_sla_breach` (an SLA/latency breach
  audit-event type distinct from the risk-limit breaches found in safety/) — confirms the "breach →
  structured audit event" pattern (claim 4) extends beyond risk limits to data-freshness/SLA breaches
  too, at least at the write-capability level (need scheduler/ingestion callers, out of scope, to
  confirm it's actually invoked on real SLA breaches).

### src/autonomous_trading_platform/runtime/services/orphan_job_recovery_service.py (71 lines)
- Purpose: `OrphanJobRecoveryService.rescue_orphan_running_jobs(cutoff)` — on process startup, finds all
  `RuntimeJobRuns` rows still `status == "running"` with `started_at < cutoff` (i.e., left behind by a
  crashed prior process) and force-transitions them to `"failed"` with a fixed error message
  (`_RESCUED_ERROR = "rescued: orphan running job detected on startup"`, line 13).
- Notable: Explicitly documented (lines 16-28) as existing to prevent two specific false-positive
  failure modes in `RuntimeSoakVerificationService` (a class not in this audit's scope — likely under
  `observability/` or `scheduler/`): stale "stuck running" detection and "concurrent running jobs"
  detection both being confused by a crashed predecessor's leftover row. Called at the very top of
  `RuntimeReplayDebugRunner.run()` (via `_rescue_orphan_jobs()`, replay_debug.py lines 287-294) using a
  30-minute cutoff, and commits its own session independently of the main replay transaction — a
  deliberate small, isolated, always-succeeds-or-fails-alone transaction so a rescue failure can't
  poison the subsequent replay run's session state.

### src/autonomous_trading_platform/runtime/services/pipeline_failure_notification_service.py (60 lines)
- Purpose: `PipelineFailureNotificationService.notify_failure(failed_run)` — writes a
  `PIPELINE_FAILURE_EVENT` audit-log entry (via `AuditLogRepository`, not `AuditLoggingService` — a
  second, narrower audit-log write path) carrying job_run_id/job_name/trigger_type/correlation_id/
  parent_job_run_id/error_message/timestamps/duration_ms as metadata.
- Notable: Gated by an **operator-configurable toggle** — `_enabled()` (lines 55-60) reads
  `OperatorSettingsRow.notify_pipeline_failures`, defaulting to `True` only if no settings row exists at
  all; if the row exists but the flag is `False`, no audit event is written for the failure (the
  `RuntimeJobRun` "failed" row itself is still written by `runtime_job_runner.py`, just no *additional*
  notification-audit-event). This is the concrete implementation behind `RuntimeJobRunner`'s
  `_notify_failure` call — confirms the "notify before/around raising" plumbing is real and DB-driven,
  not a stub, though its output is gated by an operator setting rather than unconditional.

### src/autonomous_trading_platform/runtime/services/feature_dataset_audit_service.py (147 lines)
- Purpose: `FeatureDatasetAuditService` — read-only inspection service:
  `inspect_feature_dataset(feature_dataset_version_id)` and
  `list_feature_datasets_for_source_dataset(...)`, both resolving a `FeatureDatasetVersion` row plus its
  linked source `DatasetVersion` (via `source_dataset_version` FK) into a combined
  `FeatureDatasetAuditRecord` dataclass carrying full lineage (schema_version, checksum,
  source_manifest, computation_code_version, storage_path, symbol/date coverage).
- Notable: Despite the "Audit" in its name, this class does **not** write audit-log events — it's a
  read/inspection/lineage-lookup service (an "audit" in the compliance/traceability sense: "can I trace
  this feature dataset back to its source data and computation code version", not the
  "AuditLogEvent"/audit-trail sense used elsewhere in this file). Worth flagging the naming overlap for
  anyone searching for "audit" expecting structured event-emission — this is dataset lineage
  inspection, unrelated to `AuditLoggingService`/`AuditLogRepository`.

### src/autonomous_trading_platform/runtime/services/dataset_registration_service.py (72 lines)
- Purpose: `DatasetRegistrationService.register()` validates a `DatasetVersion` via
  `DatasetValidationService` then upserts it through `SorUnitOfWork`; also exposes `save()` (upsert,
  no validation — used by callers that pre-validate), `get_latest_validated_dataset()`,
  `get_by_dataset_version_id()`.
- Notable: `register()` (lines 26-37) uses `print("DATASET VALIDATION ERRORS:", errors)` (line 31) on
  validation failure before raising `DatasetRegistrationValidationError` — a stray `print()` rather than
  structured `logging`/audit-event emission, inconsistent with the audit-log-first pattern seen
  elsewhere in this codebase (e.g. `pre_trade_risk_service.py`'s audit-then-raise). Minor code-quality
  smell: validation failures here are visible only in stdout/console, not queryable via the audit trail.

### src/autonomous_trading_platform/runtime/services/dataset_validation_service.py (10 lines)
- Purpose: one-line delegation — `validate_dataset()` runs `DATASET_VERSION_RULES` (defined in
  `contracts/validators/dataset_version.py`, out of scope) via the generic `run_rules()` engine.
- Notable: no logic of its own; the actual validation rules live in `contracts/validators/`.

### src/autonomous_trading_platform/runtime/services/dataset_version_query_service.py (78 lines)
- Purpose: read-only query service over `DatasetVersion` rows: by id, latest-validated-by-name/basis,
  by coverage-date-range-for-experiment, and by symbol+date-range (via a separate
  `symbol_date_coverage` UoW repository join).
- Notable: `get_datasets_for_symbol_range()` (lines 54-78) is the only method here that does a two-step
  lookup — first resolves candidate `dataset_version_ids` covering a symbol/date range from
  `symbol_date_coverage`, then filters those down to validated rows matching name+price_basis — a
  sensible decomposition for "give me validated datasets that actually cover this symbol on this date,"
  distinct from just "give me the latest validated dataset" (which doesn't guarantee full coverage of a
  specific symbol/date).

### src/autonomous_trading_platform/runtime/services/daily_dataset_version_resolver_service.py (95 lines)
- Purpose: `DailyDatasetVersionResolverService.get_or_create_active_daily_dataset(...)` — idempotent
  get-or-create for a single trading day's "active_daily_incremental" dataset version: looks for an
  existing row matching name+price_basis+interval+exact single-day coverage+lifecycle-tag
  `"active_daily_incremental"` (lines 46-59, a raw SQLAlchemy query with a JSON-field filter on
  `metadata_json["dataset_lifecycle"]`), and only creates+registers a new one if none exists.
- Notable: Uses raw `self.session.query(...)` directly (line 47) rather than going through
  `SorUnitOfWork`/a repository — the only file in this batch that bypasses the UoW/repository pattern
  for a read, which is a mild deviation from the "repositories follow UnitOfWork pattern; never call
  ORM directly from services" rule stated in this repo's CLAUDE.md (this file is a `runtime/services/`
  file, arguably still "services" layer, so the raw ORM query here is a CLAUDE.md-pattern violation
  worth flagging to whoever owns architecture conformance, even though functionally harmless — it's a
  read, not a write, and is wrapped by `dataset_registration_service` for the actual write path).
  Existing-row branch (lines 61-70) raises `RuntimeError` if the row exists in the raw table but the
  contract-level lookup (`get_by_dataset_version_id`) fails to find it — a defensive consistency check
  against a should-be-impossible desync between direct-ORM and repository-mediated views of the same
  table.

### src/autonomous_trading_platform/runtime/services/feature_dataset_registration_service.py (77 lines)
- Purpose: `FeatureDatasetRegistrationService.register()` validates then **inserts** (not upserts) a
  `FeatureDatasetVersion`, explicitly raising `FeatureDatasetAlreadyExistsError` (lines 19-24, 46-50) if
  a row with the same `dataset_version_id` already exists.
- Notable: Deliberately stricter than `DatasetRegistrationService.register()` (which upserts, allowing
  overwrite) — the docstring-equivalent is baked into the exception message itself: "registration must
  not overwrite prior versions" (line 23). This is an intentional immutability guarantee for feature
  datasets specifically (once a feature dataset version is registered, it's append-only), distinct from
  raw market-data dataset versions which can apparently be re-registered/updated. A real, enforced
  invariant (not just a comment) — `get_by_dataset_version_id` pre-check happens inside the same UoW
  transaction as the insert (lines 45-53), so it's not racy against itself within one call (though two
  concurrent calls with the same id could still both pass the pre-check before either commits, depending
  on DB isolation level/unique constraint backing — no explicit `UNIQUE` constraint enforcement visible
  in this file; relies on whatever the underlying table DDL defines, out of scope here).

### src/autonomous_trading_platform/runtime/services/feature_dataset_validation_service.py (16 lines)
- Purpose: one-line delegation — `validate_feature_dataset()` runs `FEATURE_DATASET_VERSION_RULES` via
  `run_rules()`. Same shape/pattern as `dataset_validation_service.py`.

### src/autonomous_trading_platform/runtime/services/ingestion_run_registration_service.py (41 lines)
- Purpose: `IngestionRunRegistrationService.register()` validates then upserts an `IngestionRun` via
  `IngestionRunValidationService` + `SorUnitOfWork`; also exposes unvalidated `save()`. Same
  validate-then-upsert shape as `DatasetRegistrationService` (upsert semantics, not insert-only like the
  feature-dataset variant).

### src/autonomous_trading_platform/runtime/services/ingestion_run_validation_service.py (10 lines)
- Purpose: one-line delegation — `validate_ingestion_run()` runs `INGESTION_RUN_RULES` via
  `run_rules()`. Same shape/pattern as the other two `*_validation_service.py` files.

### src/autonomous_trading_platform/runtime/services/run_manifest_service.py (14 lines)
- Purpose: `RunManifestService.save(manifest)` — single-method upsert wrapper over
  `uow.run_manifests.upsert(manifest)`, no validation step (unlike the dataset/ingestion-run
  registration services, this one takes/returns the contract directly with no separate row
  conversion visible in this file, and no pre-registration validation service).
- Notable: The simplest/thinnest file in the runtime/services/ directory — no validation, no business
  rules, pure pass-through persistence.

---

## Cross-cutting notes for runtime/ (post per-file review)

- **Two independent kill-switch write paths converge on one field.** Both
  `safety/services/kill_switch_service.py` (`KillSwitchService`, DB-backed via dedicated
  `KillSwitchState` table + dual-write to `RuntimeControlState.kill_switch_enabled`) and
  `runtime/services/runtime_control_service.py` (`RuntimeControlService.enable_kill_switch`/
  `disable_kill_switch`, which only touches `RuntimeControlState.kill_switch_enabled` via
  `RuntimeControlStateRepository.set_kill_switch`) can both flip the same
  `RuntimeControlState.kill_switch_enabled` column. Neither service appears (from files read in this
  scope) to check the other's target table before writing, so which service last wrote wins — if a
  caller uses `RuntimeControlService.disable_kill_switch()` without going through
  `safety.KillSwitchService`, the dedicated `KillSwitchState` table and `RuntimeControlState` can
  diverge. This compounds the single-service dual-write risk the safety/ agent already flagged.
- **Claim (2) NO_LIVE_TRADING runtime enforcement**: concretely evidenced in
  `runtime/replay_debug.py:390-391` (`if not self.settings.no_live_trading: raise RuntimeError(...)`) —
  this is a runtime/, not just config-time, check (evaluated at `.run()` call time, not import time).
  Complements the safety/ section's `environment_policy.py` build/config-gate finding.
- **Claim (3) shadow-mode "compute but suppress"**: still **unverified** after covering both safety/ and
  runtime/ in full. `shadow_mode_enabled` is read/plumbed as a settings value in both
  `safety/services/shadow_mode_service.py` (trivial getter) and `runtime/replay_debug.py` (captured into
  settings snapshot, never branched on). No file in either directory contains the actual "compute the
  order, skip the broker submit" logic. This must live in `execution/` — flagging strongly for whichever
  agent covers that directory to confirm or refute the claim; as audited, it is asserted by settings
  plumbing alone, not demonstrated by control flow.
- **Claim (5) paper-vs-live isolation**: now evidenced from *three* independent layers across both
  directories: (a) `safety/services/runtime_trading_guard_service.py`
  `_assert_run_type_matches_environment` (prior agent), (b) `runtime/replay_debug.py`
  `_assert_local_safe`/`_assert_control_state_safe` (this section, refuses LIVE environment and refuses
  persisted `trading_mode == "live"`), (c) `runtime/services/runtime_control_service.py`
  `get_cycle_block_reason`'s `trading_mode_mismatch` check (this section). Three separately-implemented
  checks is a genuinely defense-in-depth design, though the redundancy across differently-owned files
  also means a future refactor could accidentally remove one layer without anyone noticing the other two
  still "cover" it in tests — worth a design note recommending these be explicitly documented as
  intentionally-redundant layers, not consolidated by mistake.
- **Claim (4) audit-events-before-raising**: extends into runtime/ via `AuditLoggingService.record_sla_breach`
  and `PipelineFailureNotificationService.notify_failure` (called from `RuntimeJobRunner` on any job
  exception, before re-raising) — but `replay_debug.py`'s own risk-block paths (kill-switch/pause/
  drawdown) write only a `RiskSnapshot` row, not a separate audit-log event, continuing the
  inconsistent-coverage pattern the safety/ section already flagged for `pre_trade_risk_service.py`.

---

## Standout candidates

1. **DB-persisted kill switch surviving restarts** —
   `storage/sor/repositories/core/kill_switch_state_repository.py` (verified by prior agent) +
   `safety/services/kill_switch_service.py:15-26` docstring, is the strongest, cleanest piece of
   evidence for the platform's most differentiated safety claim. Cite this pair specifically, not
   `safety/repositories/kill_switch_repository.py` (in-memory, effectively dead code) or
   `runtime/services/runtime_control_service.py` (a second, DB-backed but architecturally separate
   write path to the same boolean).
2. **Four chained gates in one method** — `safety/services/live_trading_gate_service.py:53-68`
   (`assert_live_trading_allowed`) is a single, readable call site chaining environment/build/config →
   account allowlist → runtime armed gate → kill switch, in that literal order. Best single citation for
   claim (1).
3. **Two-layer NO_LIVE_TRADING enforcement, config-time and runtime-time** —
   `safety/environment_policy.py:13-22` (config/build-time) plus `runtime/replay_debug.py:387-399`
   (runtime-time, checked at `.run()` invocation, with a second DB-state check independent of the
   process's own settings). Together these show the platform doesn't just gate at startup but re-checks
   at the moment of execution against live database state — a stronger claim than "we set an env var."
4. **Three independent, non-consolidated paper-vs-live isolation checks** spanning safety/ and
   runtime/ (see cross-cutting note above) — genuine defense-in-depth, worth highlighting as
   architecture, not just one gate.
5. **Sector-concentration and portfolio-symbol-exposure breach paths that audit-log + emit metrics
   before raising** — `safety/services/pre_trade_risk_service.py:463-480` (sector) and `:282-320`
   (portfolio-symbol) are the cleanest, most complete single-file evidence for claim (4).

## Gaps/smells

1. **Claim (3), shadow-mode compute-but-suppress, is unverified across both audited directories.**
   `safety/services/shadow_mode_service.py` is a 14-line settings-flag getter with zero gating logic;
   `runtime/replay_debug.py` plumbs the same setting through but never branches on it. The actual
   suppression mechanism (if it exists) must be in `execution/` — out of scope for this pass. This is
   the single biggest open question for anyone writing up claim (3) from this audit alone.
2. **In-memory `safety/repositories/kill_switch_repository.py` is unused/dead code** relative to the
   actually-wired DB-backed repository — a misleading artifact if anyone points to "there's a
   KillSwitchRepository class" as evidence of persistence without checking which one is actually wired
   in `build_safety_context.py`.
3. **Two-tables/two-services kill-switch write divergence risk**: `KillSwitchState` (dedicated table)
   vs `RuntimeControlState.kill_switch_enabled` (shared control-state row), written independently by
   `safety.KillSwitchService` (both) and `runtime.RuntimeControlService` (only the latter) — no
   cross-check/reconciliation logic found in either directory's files.
4. **Brittle exception-message string-matching** in
   `safety/services/live_trading_gate_service.py:56-65` to reclassify a generic
   `EnvironmentIsolationError` into typed errors — a wording change in `environment_policy.py` would
   silently break this without a structural test catching it.
5. **Inconsistent audit-on-breach coverage**: only the two newer/richer checks in
   `pre_trade_risk_service.py` (sector, portfolio-symbol-exposure) emit audit events before raising;
   the three older checks (gross/symbol/daily-notional exposure) in the same file do not. Same pattern
   repeats in `runtime/replay_debug.py`'s kill-switch/pause/drawdown blocks (risk-snapshot only, no
   audit-log event) and in `runtime/services/dataset_registration_service.py` (stray `print()` instead
   of logging/audit on validation failure).
6. **Order throttle service's reservation lock is single-process only** (`threading.Lock`, no
   distributed lock) — a known, documented limitation if the platform ever runs multiple scheduler
   replicas concurrently.
7. **`daily_dataset_version_resolver_service.py` bypasses the UnitOfWork pattern** for its
   get-or-create existence check (raw `session.query(...)`), a minor deviation from this repo's stated
   architecture rule (CLAUDE.md: "never call ORM directly from services").
8. **Naming collision**: `safety/models/kill_switch_state.py`'s `KillSwitchState` (in-memory DTO) vs
   `storage/sor/models/kill_switch_state.py`'s `KillSwitchState` (ORM model) — same class name, two very
   different shapes/purposes.

## Coverage: read 45 of 45

- safety/: 24/24 files read (confirmed by prior agent, verified count matches `find | wc -l`).
- runtime/: 21/21 files read (this session), confirmed against `find src/autonomous_trading_platform/runtime -type f -name "*.py"` listing.
- No files skipped. `platform_replay/` was checked for location (per task instruction) and confirmed
  **not** under `runtime/` — it lives at `application/services/platform_replay/` (out of this audit's
  declared scope; flagged above and in the safety/ section for whoever audits `application/`).
- `TODO`/`FIXME`/`XXX` grep across both directories: 0 matches (confirmed by prior agent for safety/;
  re-confirmed clean for runtime/ during this session's reads — no such markers encountered in any of
  the 21 files).
