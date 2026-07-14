# Audit: visualization/, artifacts/, fixtures/ (+ root loose items)

Scope command: `git ls-files -- visualization artifacts fixtures` (155 tracked files)

## File counts per directory per extension

Commands run:
```
git ls-files -- visualization | sed 's/.*\.//' | sort | uniq -c
git ls-files -- artifacts     | sed 's/.*\.//' | sort | uniq -c
git ls-files -- fixtures      | sed 's/.*\.//' | sort | uniq -c
```

- `visualization/`: 22 `.py`, 6 `.md`, 26 `.png`  (54 total)
- `artifacts/`: 71 `.json`  (71 total)
- `fixtures/`: 29 `.yaml`, 1 `.md`  (30 total)

Root loose items examined briefly (not tracked in git except where noted):
- `trading_platform_screens.html` — tracked, present at repo root
- `.task_progress.json` — present at repo root
- `.runtime/` — directory, contains `replay-debug/`
- `.tmp/` — directory, contains `settings-export.json`, `settings-snapshot.json`

---

## Root loose items — characterization (all git-tracked; confirmed via `git ls-files`)

- **`trading_platform_screens.html`** (1,719 lines, ~69 KB) — the frontend visual reference mockup named explicitly in CLAUDE.md ("match it exactly"). Legitimate, intentional design artifact — not clutter.
- **`.task_progress.json`** (tracked, small) — `{"completed": [0,1], "failed": [2,3], "last_task": 3, "start_time": "2026-05-25T16:48:20", "end_time": "2026-05-25T17:11:13"}`. This reads as leftover state from an interrupted automated multi-task run (2 tasks completed, 2 failed) — **it's listed in `.gitignore` (line 232) but was already committed before that rule was added, so it remains tracked and stale.** This is exactly the kind of file an interviewer would flag: a bare numeric task-index progress file with failures, no context on what the tasks were, sitting at repo root.
- **`.runtime/replay-debug/latest.json`** — tracked despite the `.runtime` name implying transient/generated output; contains a single ad-hoc `replay-debug` CLI run dump (SPY/QQQ, Jan 2024, same shape as `artifacts/runtime/replay-summary.json`). `.runtime/` is **not** in `.gitignore` at all — likely should be.
- **`.tmp/settings-export.json`** and **`.tmp/settings-snapshot.json`** — tracked despite the `.tmp` name; two near-identical settings dumps (`max_drawdown_limit: "0.0010"`, `max_strategy_drawdown: "0.9900"` — note these look like deliberately extreme/edge-case test values, not realistic settings, suggesting a debug/edge-case export). `.tmp/` is also **not** in `.gitignore`.

**Pattern:** `.task_progress.json`, `.runtime/`, `.tmp/` all read as accidentally-committed local/dev-session scratch state — directory names that conventionally signal "don't commit this" but lack `.gitignore` coverage (or have partial/late coverage). This mirrors the `artifacts/backtesting/` and `artifacts/diagnostics/` duplicate-export pattern found above — a recurring theme of committed dev scratch output across the repo, not isolated to one directory.

---

## visualization/ — Python modules (full audit)

**Package purpose (from `__init__.py` docstring):** reads `PlatformBacktestArtifact` JSON files produced by `atp platform backtest run --output artifacts/platform/backtests/<name>.json` and turns them into a slide-deck of storytelling PNG charts + markdown reports. This is a standalone offline reporting tool, **not** imported anywhere in `src/` or `tests/` — confirmed via `grep -r "visualization\." src/ tests/` → zero matches. It's invoked only via `python -m visualization.run_all`. Not covered by pytest, no CI wiring found for it.

- **`__init__.py`** (3 lines) — just the docstring described above.

- **`loader.py`** (226 lines) — `load(path)` parses one artifact JSON into an `ArtifactData` dataclass: identity fields, 10 raw domain-summary dicts (portfolio/risk/governance/execution/research/strategy_catalog/controls/settings/safety/operations), a per-tick `tick_df` pandas DataFrame (`_build_tick_df`), timeline events, warnings/errors, and the raw dict. Critically it **auto-detects synthetic mode**: `is_synthetic = total_orders == 0 and total_fills == 0 and pnl_pct == 0.0` — i.e. every artifact in this repo (no live/paper trading has happened) is flagged synthetic and downstream code fabricates financial performance for it (see `synthetic.py`).

- **`metrics.py`** (163 lines) — pure pandas/numpy helper library: rolling returns/vol/Sharpe/beta/alpha/drawdown, `max_drawdown_details` (peak/trough/recovery date detection), `compute_cagr`, `compute_sharpe`, `hurdle_final_value`. Clean, well-documented, no I/O. Generic enough it could be reused for real analytics later.

- **`theme.py`** (169 lines) — matplotlib/seaborn dark theme that **mirrors the frontend design tokens exactly** (`BG #070B0F`, `ACCENT #00E5A0`, `RED #FF4D6D`, etc. — same hex values as `frontend/src/index.css`). Defines figure sizes (16:9 slide-friendly), a governance-event color/label map, `apply()`, `subtitle()`, `watermark()` ("Autonomous Trading Platform" footer). This is polish clearly aimed at demo/portfolio presentation, not internal debugging.

- **`synthetic.py`** (334 lines) — **key finding**: when `ArtifactData.is_synthetic` is True (always, currently), `augment()` fabricates an entire correlated GBM platform-vs-benchmark equity history: 24%/14%vol platform, 21%/16%vol SPY-like benchmark, correlation 0.72, reproducible via a seed hashed from `replay_id`. It even injects synthetic drawdown shocks anchored to *real* timeline events (`safety_emergency_halt`, `controls_paused`) so the fake equity dips line up with real governance events. Also fabricates execution-quality synthetics (avg slippage ~4.2bps, adverse fill rate ~10%, latency ~42ms via `_add_synthetic_execution`). Computes a full synthetic performance-stats dict (Sharpe, Sortino, Calmar, info ratio, etc.) that chart 04 (performance table) and others print verbatim. If real fills existed, `_add_real_derived_columns` would use them instead — that path is currently dead code (never exercised since no artifact has real fills).

- **`reporting.py`** (499 lines) — generates 3 markdown companion docs per run: `00_methodology_assumptions.md`, `chart_explanations.md`, `robustness_next_steps.md`. Notably **self-disclosing/honest**: explicit banner "This report is generated from a platform replay artifact. It is a backtest/replay demonstration, not live client performance", a Data Mode table stating whether fills/orders are real, and per-chart caveats explicitly say things like "Do not cite as live performance" / "Do not cite these figures as measured trading costs." `robustness_next_steps.md` is an explicit unexecuted test-matrix scaffold (Monte Carlo, walk-forward, bear-market regime tests — all "Not run"). This honesty is itself a portfolio talking point (shows awareness of synthetic-data risk) but also confirms none of the displayed numbers are real trading results.

- **`run_all.py`** (258 lines) — CLI entrypoint (`argparse`): `--artifact`, `--out-dir`, `--starting-cash`, `--charts` (subset by number), `--list-artifacts`. Loads → augments with synthetic data → renders a registry of 14 charts (see below) → generates the 3 markdown reports → prints a summary with per-chart timing/size and error list. Defaults to `artifacts/platform/backtests/full_year_demo.json`, outputs to `visualization/outputs/<fixture_slug>/`. Each chart/report call is individually try/excepted so one failure doesn't kill the run.

### `visualization/charts/*.py` — 14 chart modules (all read in full, ~271 lines avg, matplotlib/seaborn, dark theme via `theme.apply()`, every chart saved at `theme.DPI=150`, watermarked "Autonomous Trading Platform")

All 14 follow an identical structure: `render(data: ArtifactData, out_dir: Path) -> Path`, one function per file, no classes, no tests. Every chart's title/subtitle explicitly states `[synthetic financial data]` vs `[live data]` based on `data.is_synthetic`. Numbered 01–14 matching `run_all.py`'s registry:

1. **`equity_curve.py`** (192 lines) — platform vs synthetic-SPY vs cash line, dual y-axis ($ and cumulative %), timeline-event triangle markers, 15/20/25% "target hurdle" reference lines explicitly labeled "not financial advisory standards."
2. **`drawdown.py`** (225 lines) — drawdown fill + benchmark overlay, drawdown-ladder threshold lines (WARNING/PROBATION/SUSPENDED, scaled from `settings.max_drawdown_limit`), peak/trough/recovery annotation via `metrics.max_drawdown_details`, underwater-duration subplot.
3. **`monthly_returns.py`** (167 lines) — seaborn heatmap (year × month, red→green diverging) with an "Annual" summary column, plus a return-distribution histogram/KDE panel.
4. **`performance_table.py`** (183 lines) — 14-row matplotlib-table "scorecard" (Total Return, CAGR, Sharpe, Sortino, Max DD, Calmar, Win Rate, Information Ratio, Final Value, P&L, etc.), platform vs benchmark vs excess, color-coded green/red by direction.
5. **`governance_timeline.py`** (229 lines) — 3-panel: equity context, event "swim lanes" (Safety/Governance/Settings/Allocation categories) with applied (●) vs skipped (✗) markers, and a strategies-in-breach area chart. This is flagged in `reporting.py` itself as "the most reliable chart — real governance events, not synthetic."
6. **`operational_health.py`** (191 lines) — tick-execution calendar heatmap (green/red by day), system-health-status bar strip, alert-count area chart, tick-completion donut. Uses **real** artifact fields (no synthetic augmentation needed here).
7. **`platform_contribution.py`** (265 lines) — a "directional contribution score" per platform domain (Research/Governance/Allocation/Risk Controls/Portfolio Constr./Execution), computed via an ad-hoc heuristic formula baked directly into the chart function (e.g. `Governance = promotions*5 - demotions*2`, `Portfolio Constr. = (sharpe-1.0)*10`) — explicitly labeled "DIRECTIONAL ESTIMATE ONLY... not exact attribution" both on-chart and in code comments. Also strategy-catalog donut, research-funnel mini bars, execution/risk summary bars.
8. **`execution_quality.py`** (267 lines) — 5-panel: slippage distribution, adverse-fill-rate over time, latency distribution, slippage over time (with halt-period shading), slippage-vs-volatility scatter with trendline. All driven by the synthetic execution columns from `synthetic.py` when no real fills exist (i.e. always, currently).
9. **`benchmark_gauntlet.py`** (271 lines) — bar chart + table comparing platform to: cash/RFR, synthetic SPY, 15/20/25% hurdles (all computable), plus 3 same-universe baselines and 4 external references (SPY/QQQ/VTI/60-40) explicitly shown as "Unavailable" / "N/A" — the module docstring states "No fake benchmark returns are generated" for the unavailable rows, contrasting with the synthetic SPY row which IS fabricated.
10. **`cost_sensitivity.py`** (284 lines) — re-estimates return/CAGR/final-value under +0/5/10/25/50 bps cost scenarios; turnover is *estimated* from `rebalance_frequency` × an assumed ~20%-churn-per-rebalance rule of thumb (since `total_fills=0` in every current artifact) — explicit footnote: "ROUGH SENSITIVITY ESTIMATE... Re-run with real fill data for precise cost impact."
11. **`rolling_risk.py`** (215 lines) — 4-panel rolling(30d) return/vol/Sharpe/drawdown, platform vs benchmark; has a graceful "insufficient data" fallback panel if <10 days of returns.
12. **`exposure_allocation.py`** (257 lines) — cash-vs-invested stacked area (falls back to an explicit "100% cash, no real fills" placeholder panel when synthetic), open-positions count, end-of-run gross/net exposure snapshot table — explicitly notes per-symbol weight history "Not available."
13. **`strategy_lifecycle.py`** (249 lines) — cumulative promotions/demotions, monthly governance-event bars, strategies-evaluated-vs-in-breach area, catalog donut. Explicitly renders a "0 governance transitions fired this run" banner when applicable (currently true for the demo artifacts — see below).
14. **`research_funnel.py`** (309 lines) — monthly research funnel bars (total runs → stage1 → stage2 → stage3 → deployable), top-composite-score line, regime-diversity bars, cumulative-deployable step chart, summary panel. Explicitly renders "0 candidates passed Stage 1 in any month... filter thresholds may be too strict or simulation metrics are not populating correctly" when applicable.

Charts 13 and 14 are **not** in the original 12-chart `full_year_demo/` output dir (only `01`–`12` + reports exist there) but **are** present in `full_year_demo_v2/` (`13_strategy_lifecycle.png`, `14_research_funnel.png` added) — consistent with the "storytelling-visualization" branch merge adding governance/research charts in a second pass.

`charts/__init__.py` (16 lines) — just re-exports all 14 render modules (note: `noqa: F401`, and it's a straight import list, no registry logic — the actual ordered registry lives in `run_all.py`).

## visualization/outputs/ — the 6 `.md` companion reports (full audit — generated output, not hand-authored)

Both `full_year_demo/` and `full_year_demo_v2/` runs produced identical-structure `00_methodology_assumptions.md`, `chart_explanations.md`, `robustness_next_steps.md` (byte-for-byte generated from the templates in `reporting.py` above — content matches exactly except run-specific numbers).

- **`00_methodology_assumptions.md`** — v1 run: replay `replay-ac02c36987d9`, 6 symbols (SPY/QQQ/AAPL/MSFT/GLD/TLT), daily rebalance, 15% max-DD limit. v2 run: replay `replay-3febf27c6f3f`, 13 symbols (adds IWM/NVDA/GOOGL/JPM/JNJ/XOM/WMT), weekly rebalance, 12% max-DD limit. Both explicitly state "Synthetic financial data," "No real fills recorded," "Live trading performance is NOT present in this artifact."
- **`chart_explanations.md`** — identical narrative text in both (only the header period/symbols line differs); this is the per-chart Q&A/caveat content already captured verbatim in the `reporting.py` `_EXPLANATIONS` list above, so not re-transcribed here.
- **`robustness_next_steps.md`** — byte-identical in both runs; the unexecuted test-matrix scaffold described above (2022 bear market, walk-forward, Monte Carlo, etc., all "Not run").

Note: `full_year_demo_v2` also has `chart_explanations.md`-adjacent numbered charts 13/14, but reporting.py's `_EXPLANATIONS` list only covers charts 01–12 — so `chart_explanations.md` in **both** dirs stops at chart 12 even though v2's directory contains `13_strategy_lifecycle.png` and `14_research_funnel.png`. **Gap:** the two newest charts (added for the "storytelling-visualization" merge) have no explanation entries in `reporting.py`'s `_EXPLANATIONS` list — likely an oversight when charts 13/14 were added.

## visualization/outputs/ — PNGs (listed only, per audit scope — not opened)

`full_year_demo/`: `01_equity_curve.png` … `12_exposure_allocation.png` (12 files, charts 1–12).
`full_year_demo_v2/`: `01_equity_curve.png` … `14_research_funnel.png` (14 files, charts 1–14, adds strategy_lifecycle + research_funnel).
26 PNGs total. File naming is consistent zero-padded `NN_chart_name.png` matching the `run_all.py` chart registry order.

---

## artifacts/ — 71 JSON files (all opened/parsed via script; identical-schema groups summarized collectively)

**Usage check (grep src/ + tests/):** `artifacts/` paths appear only as **default output/search locations** in CLI commands (`cli/commands/{platform,research,operations,governance,backtesting}.py` — e.g. `platform.py` defaults `artifacts_dir` to `Path("artifacts/platform/backtests")` and searches it for `--run-id` lookups/resume). Nothing in `src/` or `tests/` reads a *specific committed* artifact file by name — these are dev-run outputs that happened to get committed, not fixtures consumed by the test suite or app code. `tests/cli/commands/test_risk_cli.py`, `test_portfolio_cli.py`, `test_features.py` reference the `artifacts/...` default-path strings only as CLI-arg assertions (i.e. testing that the CLI defaults to that path), not reading file contents.

**Key finding — every full platform-backtest artifact has zero real trading activity.** Checked `total_orders`/`total_fills`/`portfolio.total_pnl_pct` across all 32 non-checkpoint files in `artifacts/platform/backtests/`: **100% show `orders=0, fills=0, pnl_pct=0`.** This confirms every chart in `visualization/` is rendering fabricated GBM data (per the `synthetic.py` finding above) — there is no artifact in the repo with real simulated trading results. The lone exception, `two_year_full.checkpoint.json` (703 bytes — a resume/cadence checkpoint, different schema, not a full artifact), shows `total_orders=3928, total_fills=3928` for a run that reached tick 88/~500 before presumably being interrupted — but the corresponding full `two_year_full.json` artifact doesn't exist in the repo, so this partial evidence of "real" fills isn't visualizable or verifiable from committed files.

### Groups by schema (44 files: `artifacts/platform/backtests/*.json`, one large shared schema)

All share the same top-level `PlatformBacktestArtifact` schema (`actor, admin, completed_at, controls, diagnostics, dry_run, end_date, errors, execution, features, fixture_name, governance, ingestion, inject_failures, operations, portfolio, replay_id, research, risk, run_id, runtime, safety, settings, start_date, started_at, strategy_catalog, tick_results, timeline_events_applied, warnings, ...`) — this is exactly what `visualization/loader.py` parses. `admin` is a fixed health stamp (`db_ok/config_ok/alembic_version` all identical across every file — `alembic_version: "kk11ll22mm33"` looks like a fake/placeholder migration ID, not a real Alembic revision hash, worth double-checking that's intentional test scaffolding and not a bug). `actor` is always `"platform-backtest"`.

33 files total under `artifacts/platform/backtests/` (32 full artifacts + 1 checkpoint), sized 703 B to 814 KB, tick counts from 3 (`smoke_minimal`) to 261 (`full_year_demo*`). Names split into clear families:
- **Smoke/base**: `smoke_minimal` (3 ticks), `smoke_healthy` (5), `base_platform_replay` (10)
- **Failure-injection (`inject_failures: true`)**: `execution_failures`, `governance_failures`, `ingestion_failures`, `risk_failures`, `runtime_failures`, `combined_failures`, and duplicated `fi_*` variants (`fi_execution`, `fi_governance`, `fi_ingestion`, `fi_risk`, `fi_runtime`, `fi_combined`) — **the `fi_*` files appear to be near-duplicates of the non-prefixed failure files** (same tick counts: e.g. `fi_execution.json` 22 ticks / 69,993 B vs `execution_failures.json` 22 ticks / 70,087 B; `fi_combined.json` 43 ticks/131,647 B vs `combined_failures.json` 43 ticks/133,058 B) — looks like a naming-convention rename mid-project left both old and new copies committed.
- **Interaction scenarios**: `allocation_override`, `controls_lifecycle`, `governance_and_allocation`, `governance_transitions`, `safety_events`, `safety_with_recovery`, `settings_change`, `settings_then_controls`, `strategy_lifecycle` (22–43 ticks each)
- **Duration ladder**: `two_month_healthy`/`two_month_with_events` (43 ticks), `medium_healthy`/`medium_with_events` (43 ticks — near-duplicate pairing pattern again), `three_month_research_validation` (63), `six_month_regression` (129), `full_year_demo`/`full_year_demo_v2` (261), `two_year_full.checkpoint` (incomplete, 88/500-ish ticks)

These map 1:1 by name to `fixtures/platform/replays/**/*.yaml` (see fixtures section below) — strong evidence each artifact was produced by running `atp platform backtest run --fixture fixtures/platform/replays/.../X.yaml --output artifacts/platform/backtests/X.json`.

### Groups by schema (12 files: `artifacts/backtesting/*.json`, dev/debug audit dumps)

- **`notification_events_*.json`** (12 files, timestamped `20260513_*` and `20260601_232555`–`20260604_002748`, 1.9–8.5 KB) — schema `{run_id, results: [...]}`, 3–14 result entries each. These look like **repeated manual re-runs of the same debug script during development** (12 timestamped dumps of essentially the same notification-pipeline check across ~3 weeks of dev sessions, 2026-05-13 through 2026-06-04).
- **`risk_parameter_effects_*.json`** (4 files, `20260513_040313`/`_042419`/`20260604_002737`/`_004557`) — schema `{run_id, symbols, start, end, starting_cash, random_seed, results}`, always `AAPL`/`MSFT`-style 2-symbol Jan-2024 sweeps. Same "repeated manual debug run" pattern.
- **`auto_demotion_audit_20260604_011522.json`** / **`auto_promotion_audit_20260604_011505.json`** — one-off governance-automation audit dumps (disabled/enabled/duplicate path comparisons, audit_events, notification_status), reference `settings_fixture: "fixtures\\settings.yaml"`.
- **`governance_allocation_audit_*.json`** (4 files, `20260513_054234`/`_054319`/`_054417`/`20260604_010626`) — largest of this group (15–25 KB), full settings-field classification / wiring audit, references `controls_fixture`/`settings_fixture` pointing at `fixtures\controls.yaml` / `fixtures\settings.yaml`. Same repeated-manual-run pattern (3 runs 45–90s apart on 05-13, 1 more on 06-04).

**Pattern across all of `artifacts/backtesting/`:** every filename is a debug/audit script's default output path stamped with a run timestamp — this whole subdirectory reads as **committed scratch output from interactive dev-testing sessions**, not deliberately curated artifacts. 20 files, all in this one bucket.

### Remaining files (15, each effectively unique schema — small, all read in full)

- `controls/current.json` (2.3 KB) — CLI export snapshot of kill-switch/trading-mode/allocation-override state; all values placeholder-truncated in export but real field set.
- `diagnostics/runtime-snapshot.json` **and** `diagnostics/snap.json` (426 B each) — **near-identical duplicate**: same schema, all-empty/null runtime snapshot (`operator_controls: null`, empty lists), timestamps 5 minutes 29s apart (`00:47:29` vs `00:52:58` on 2026-06-02). Looks like the same manual export command run twice and both outputs got committed — classic accidental commit clutter.
- `execution/sync_broker_state.json` (457 B) — tiny broker-sync-status stub, `synced_broker_order_ids: []` / `synced_fill_ids: []` (empty — consistent with no real trading anywhere in this repo).
- `features/lineage.json`, `features/run.json` — feature-pipeline export/run-log stubs (dataset versioning metadata, `include_regime`/`include_volatility` flags), consistent with the `storage/parquet/versioning.py` dataset system described in CLAUDE.md.
- `governance/momentum__906ff...json` **and** `governance/momentum_v1_review.json` — **same near-duplicate pattern**: identical schema, identical `strategy_id`, differ only in `export_id`/`exported_at` (8ms apart — `2026-06-04T01:15:47.131090` vs `.123570`). One is almost certainly a stray duplicate export; the human-readable name (`momentum_v1_review.json`) suggests intentional keep, the hash-named file looks like the accidental extra.
- `operations/soak_report_test.json` (10 KB) — a **failed** soak-test report (`"status": "failed"`, environment `paper`, 14 checks) — worth noting this is a failing report committed to the repo; could read as "we know about this failure" (fine) or as clutter if unintentional.
- `portfolio/allocation-config.json`, `portfolio/current.json` — allocation policy snapshot (2 strategies, `momentum` + `moving_average_crossover`, override-applied caps) and a full portfolio CLI export (holdings/allocation/risk/performance/equity-curve/cash+position snapshots — all real schema, populated with live-shaped fields).
- `research/plan.json` (899 B) — a `dry_run: true` experiment plan stub (2-symbol `AAPL`/`MSFT` sweep, `elite_monte_carlo` stage) — this is a planning/dry-run artifact, not an executed result.
- `research/regimes.json` — regime-analysis-request wrapper around one experiment's summary (best_sharpe/best_return placeholders).
- `risk/current.json` — risk CLI export (limits, latest snapshot, empty `drawdown_states: []`, `latest_risk_budget: null`).
- `runtime/replay-summary.json` (5.8 KB) — a single ad-hoc replay's summary (inputs/execution/trading/rebalancing/portfolio/risk_metrics sections) — this is a different/older summary format than the `PlatformBacktestArtifact` schema used everywhere else, suggesting it predates the platform-backtest artifact format.
- `strategy/momentum_params.json` (67 B) — trivial `{lookback: 20, buy_above: 0.0, sell_below: 0.0}` — looks like leftover scratch/default-params output, borderline not worth committing on its own.

---

## fixtures/ — 29 `.yaml` + 1 `.md` (brief entry each, per audit scope)

**Consumed by** (confirmed via grep): `fixtures/controls.yaml` and `fixtures/settings.yaml` are exercised directly in `tests/cli/commands/test_risk_cli.py` and `test_backtesting_governance_audit.py` as `--controls`/`--settings` CLI-arg fixtures (asserted as default paths, and used as `settings_fixture`/`controls_fixture` in the `artifacts/backtesting/governance_allocation_audit_*.json` dumps above). The 24 `fixtures/platform/replays/**/*.yaml` files aren't referenced by literal path in `src/`/`tests/` (no hardcoded string match) — they're consumed exclusively via the `atp platform backtest run --fixture <path>` CLI invocation documented in `fixtures/platform/replays/README.md`, and each maps 1:1 by filename to an artifact under `artifacts/platform/backtests/`.

- **`fixtures/platform/replays/README.md`** (259 lines) — exceptionally thorough fixture-suite documentation: directory-structure map, a full config index table (timespan/trading-days/purpose per fixture), supported timeline-event types, supported injection kinds with SOR effects, CLI usage examples for `plan`/`run`/`inspect`/`report`, and an honest **"Known Gaps / Future Scenarios"** section (e.g. "`trading_mode_changed` event type is supported in schema but not yet tested in isolation," "research domain hook is P2, failure injection not yet implemented"). This is the best-documented corner of the whole scope — portfolio-worthy on its own as an example of test-fixture engineering discipline.

- **`fixtures/controls.yaml`** — hand-authored controls seed: 3 strategies (MA crossover 60% live, momentum 30% paper, mean-reversion 0% pending-research) with inline comments mapping each `governance_state` to its frontend display label ("shows as Running/Paper/Pending Promotion").
- **`fixtures/settings.yaml`** — hand-authored settings seed: risk/governance/simulation/notification config block, heavily commented with valid-value hints (e.g. `risk_tolerance: high # low | medium | high`).
- **`fixtures/ma_crossover_debug.yaml`** / **`fixtures/mean_reversion_debug.yaml`** — paired one-off debug fixtures (each carries the exact CLI invocation as a header comment: `backtesting seed-fixture` → `runtime replay-debug --symbols SPY,QQQ --start 2024-01-01 --end 2024-01-31`) for manually contrasting the two strategy types; not part of the `platform/replays/` suite or its README index — standalone debug scratch fixtures, still useful but organizationally separate from the documented suite.
- **`fixtures/platform/full_user_replay.yaml`** — a hand-built "simulated user story" replay (3 symbols, 2024-01-01→03-01, 8 timeline events narrating a realistic operator session: risk-tolerance change → strategy disable → injected missing-bar failure → pause/resume → governance promotion → risk breach injection → emergency halt). Also **not** in the README's config index or referenced by any artifact — appears to have no corresponding `artifacts/platform/backtests/full_user_replay.json`, i.e. this fixture may never have actually been run.

### `fixtures/platform/replays/**/*.yaml` (24 files, documented exhaustively in the README table above — brief pass confirms actual file contents match the README's claims for the 2 spot-checked files: `base/base_platform_replay.yaml` matches its documented "10 trading days, all jobs, no events" baseline exactly, including `research: enabled: false`):

| Subdir | Files | Per README |
|---|---|---|
| `base/` | 1 | `base_platform_replay.yaml` — 2024-01-02→01-15, 10 days, 0 events, baseline |
| `short/` | 2 | `smoke_healthy.yaml` (5 days), `smoke_minimal.yaml` (3 days, SPY only) |
| `timeline_events/` | 6 | one config per event category (`settings_change`, `controls_lifecycle`, `strategy_lifecycle`, `allocation_override`, `governance_transitions`, `safety_events`) — all Jan 2024, 23 days, 2 events each |
| `interactions/` | 3 | `settings_then_controls`, `governance_and_allocation` (43 days, 5 events), `safety_with_recovery` (33 days, 5 events) — cross-domain scenarios |
| `failure_injection/` | 6 | `ingestion_failures`, `risk_failures`, `governance_failures`, `execution_failures`, `runtime_failures` (23 days, 1–2 injections each), `combined_failures` (43 days, 5 injections across all 5 domains) — require `--inject-failures` flag or events are silently skipped |
| `medium/` | 3 | `two_month_healthy.yaml` (43 days, 0 events), `two_month_with_events.yaml` (43 days, 8 events), `three_month_research_validation.yaml` (63 days, per artifact tick count — not in README table, likely added after README was last updated) |
| `long/` | 3 | `six_month_regression` (~130 days, 7 events), `full_year_demo` (~261 days, 12 events), `two_year_full` (longest, maps to the incomplete checkpoint artifact) |

All 24 replay fixtures share one schema: `platform_replay` (name/mode/symbols/start/end/starting_cash/random_seed), `initial_state` (explicitly **documentation only** — not auto-applied; requires a separate `atp platform fixture seed` step), `scheduled_jobs` (per-job cadence), `timeline_events` (list of dated event injections), `outputs` (which artifact sections to write). Every fixture file examined uses the same 3–13 symbol universe drawn from SPY/QQQ/AAPL/MSFT/GLD/TLT/IWM/NVDA/GOOGL/JPM/JNJ/XOM/WMT and `random_seed: 42` — consistent, reproducible test data, not organically varied.

---

## Standouts (demo-worthy)

- **`visualization/reporting.py` + every chart's on-figure caveat text** — the self-disclosure discipline (every chart literally labels itself `[synthetic financial data]` vs `[live data]`, and `reporting.py` bakes in "Do not cite as live performance" language) is a genuinely strong engineering-maturity signal — most portfolio backtest demos don't admit their numbers are fabricated.
- **`fixtures/platform/replays/README.md`** — 259 lines of exemplary test-fixture documentation: config index table, supported event/injection catalogs, CLI usage examples, and an honest "Known Gaps / Future Scenarios" section. Best-documented artifact in this whole scope.
- **`visualization/theme.py`** — dark theme color tokens are pulled 1:1 from `frontend/src/index.css`, giving the chart deck a genuinely "native to the platform" look rather than generic matplotlib defaults.
- **The `visualization/charts/*.py` → `run_all.py` → `reporting.py` pipeline as a whole** — 14 charts + 3 companion markdown docs generated from one CLI command, each chart individually fault-isolated (try/except per chart so one failure doesn't kill the run), consistent numbering/naming, graceful "insufficient data" fallback panels (`rolling_risk.py`, `strategy_lifecycle.py`, `research_funnel.py` all handle the zero-data case explicitly instead of crashing).
- **`fixtures/platform/replays/**` fixture suite design** — clean separation of concerns (base/short/timeline_events/interactions/failure_injection/medium/long), each fixture 1:1 traceable to a produced artifact, `--inject-failures` gating for the failure-injection tier.

## Gaps / smells

- **Every single platform-backtest artifact (32/32 non-checkpoint files) has `total_orders=0, total_fills=0, portfolio.total_pnl_pct=0`** — no real trading has ever happened in this repo's committed history. All equity curves, Sharpe ratios, drawdowns shown in the 26 committed PNGs are fabricated GBM (`visualization/synthetic.py`), not backtest results. An interviewer who digs one level will find this immediately (it's disclosed, but still worth being ready to explain proactively).
- **`chart_explanations.md`'s `_EXPLANATIONS` list in `reporting.py` only covers charts 01–12** — charts 13 (`strategy_lifecycle`) and 14 (`research_funnel`), present in `full_year_demo_v2/`, have no explanation entries; looks like an oversight from the chart-13/14 addition pass.
- **Duplicate/near-duplicate committed exports** in several places: `fi_*.json` vs non-prefixed failure-injection artifacts (e.g. `fi_execution.json` vs `execution_failures.json`, same tick counts, different byte sizes — looks like a mid-project rename left both); `diagnostics/runtime-snapshot.json` vs `diagnostics/snap.json` (5m29s apart, otherwise identical); `governance/momentum__<hash>.json` vs `governance/momentum_v1_review.json` (8ms apart, otherwise identical).
- **`artifacts/backtesting/` (20 files)** reads entirely as committed interactive-dev-session scratch output — 12 `notification_events_*` and 4 `risk_parameter_effects_*` timestamped dumps spanning 2026-05-13 to 2026-06-04, none referenced by src/tests, no obvious curation.
- **Root-level `.task_progress.json`, `.runtime/`, `.tmp/`** — all git-tracked despite naming that signals transient/scratch state; `.task_progress.json` is in `.gitignore` but was already tracked before the rule was added (stale-ignore pattern); `.runtime/` and `.tmp/` aren't ignored at all. `.task_progress.json` itself shows a failed automated run (`"failed": [2,3]`) with no context — the kind of file that invites "what is this?" in an interview.
- **`artifacts/operations/soak_report_test.json`** is a committed **failing** soak-test report — intentional-or-not is unclear from the file alone.
- **`fixtures/platform/full_user_replay.yaml`** and **`fixtures/ma_crossover_debug.yaml`/`mean_reversion_debug.yaml`** sit outside the documented `platform/replays/` suite and its README index; `full_user_replay.yaml` appears to have no corresponding committed artifact, suggesting it was authored but never actually run.
- **`visualization/` has zero test coverage and zero imports from `src/`/`tests/`** — confirmed via grep, it's a fully standalone CLI tool, not wired into CI in any way found in this scope (though CI config itself was out of scope here).
- **Frontend duplication check**: `visualization/` charts are static PNG generation for the storytelling/demo deck, while `frontend/` renders the same conceptual chart types (equity curve, drawdown, allocation) with Recharts against mock data (`frontend/src/mock/data.ts`, per CLAUDE.md). They don't share code or data — two independent chart-generation paths for similar concepts, which is reasonable (one's a slide-deck generator, one's a live UI) but worth knowing they're not unified.

## Coverage

- **`.py`**: 22/22 opened in full (visualization/ package + 14 chart modules + `charts/__init__.py`).
- **`.md`**: 6/6 opened in full (3 reports × 2 output runs; `fixtures/platform/replays/README.md` also opened in full, bringing fixtures `.md` to 1/1).
- **`.yaml`**: 29/29 accounted for — 5 opened in full (`controls.yaml`, `settings.yaml`, `ma_crossover_debug.yaml`, `mean_reversion_debug.yaml`, `full_user_replay.yaml`) + 2 spot-check opens (`base_platform_replay.yaml` in full; others cross-checked against the README's documented config-index table rather than individually opened).
- **`.json`**: 71/71 opened/parsed (top-level schema + representative nested structure for every file via script; ~19 files also fully printed). Identical-schema groups (platform/backtests 33, backtesting 20-ish, diagnostics/governance duplicate pairs) summarized collectively per audit scope; all unique-schema singles read in full.
- **`.png`**: 26/26 listed by filename only, not opened/rendered, per audit scope.
- Root loose items: `trading_platform_screens.html` (line count only), `.task_progress.json`, `.runtime/replay-debug/latest.json`, `.tmp/settings-export.json`, `.tmp/settings-snapshot.json` — all opened/characterized.
