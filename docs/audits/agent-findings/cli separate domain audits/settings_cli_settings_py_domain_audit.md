# Settings CLI Domain Audit: `settings.py`

Target file: `src/autonomous_trading_platform/cli/commands/settings.py`

Current state: this CLI command file does not exist yet. No `settings` domain is registered in `src/autonomous_trading_platform/cli/main.py` at the time of this audit.

Scope note: `settings` should own persisted operator configuration: risk appetite knobs, automation toggles, notification toggles, rebalance cadence, cost/slippage model selection, and settings metadata/source-of-truth explanations. It should not own pause/resume/trading mode (`controls`), kill switch/live gate (`safety`), capital/exposure policy execution (`risk`), or end-to-end product workflows (`platform`).

## 1. Current CLI Inventory

No commands are currently registered because `src/autonomous_trading_platform/cli/commands/settings.py` does not exist.

| Command path | Arguments/options | Handler | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp settings` | none | none | no | no | no | PLACEHOLDER |

Related legacy settings commands currently live under `backtesting`:

| Existing command path | Arguments/options | Handler | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp backtesting seed-settings` | `--config` | `handle_seed_settings` | yes | no | no | LOCAL_DB_MUTATION |
| `atp backtesting read-settings` | none | `handle_read_settings` | no | no | yes | READ_ONLY_SAFE |
| `atp backtesting verify-risk-parameter-effects` | `--controls`, `--settings`, `--symbols`, `--start`, `--end`, `--starting-cash`, `--random-seed`, `--reset-sim-state`, `--print-summary`, `--parameter` | `handle_verify_risk_parameter_effects` | yes | no | no | CROSS_DOMAIN_RUNTIME |
| `atp backtesting verify-notification-events` | `--controls`, `--settings` | `handle_verify_notification_events` | yes/conditional | no | no | CROSS_DOMAIN_RUNTIME |
| `atp backtesting verify-auto-promotion` | `--settings` | `handle_verify_auto_promotion` | yes/conditional | no | no | CROSS_DOMAIN_RUNTIME |
| `atp backtesting verify-auto-demotion` | `--settings` | `handle_verify_auto_demotion` | yes/conditional | no | no | CROSS_DOMAIN_RUNTIME |

## 2. Domain Responsibility Check

| Command | Placement | Notes |
|---|---|---|
| `settings` domain | should be added | This domain is missing but belongs in the final CLI taxonomy. |
| `backtesting seed-settings` | should move to `settings` | Seeding persisted operator settings is not a backtesting concern. |
| `backtesting read-settings` | should move to `settings` | Reading persisted operator settings is core settings-domain behavior. |
| `backtesting verify-risk-parameter-effects` | should be duplicated/wrapped elsewhere | The settings-input half belongs in `settings`, but runtime effect verification crosses `runtime`, `risk`, and possibly `platform`. |
| `backtesting verify-notification-events` | should be duplicated/wrapped elsewhere | Settings toggles belong here; event triggering/notification behavior may fit `operations` or `platform`. |
| `backtesting verify-auto-promotion` | should move/wrap under `governance` with `settings` preflight support | `auto_promote_enabled` is a settings toggle, but promotion behavior is governance. |
| `backtesting verify-auto-demotion` | should move/wrap under `governance`/`risk` with `settings` preflight support | Settings own the toggle/threshold row; demotion behavior is governance/risk. |

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why it belongs in this domain | Class | Implementation target service/function | Priority |
|---|---|---|---|---|---:|
| `atp settings show` | Print current persisted operator settings with metadata/source-of-truth notes. | Core read path for operator configuration. | read-only | `OperatorSettingsService.get_settings`, `_settings_response`-equivalent metadata | P0 |
| `atp settings show --json` | Stable JSON output for scripts and audits. | Settings need machine-readable inspection. | read-only | `OperatorSettingsService.get_settings` | P0 |
| `atp settings seed --config settings.yaml --dry-run` | Validate and preview a YAML settings patch before writing. | Existing `backtesting seed-settings` should move here and gain dry-run. | read-only | Pydantic `OperatorSettingsUpdateRequest` validation plus YAML loader | P0 |
| `atp settings seed --config settings.yaml --updated-by operator --reason "..."` | Persist settings from YAML and record audit evidence. | This is the main local mutating settings entrypoint. | local-mutating | `OperatorSettingsService.update_settings` | P0 |
| `atp settings set --key risk_tolerance --value high --updated-by operator --reason "..." --dry-run` | Small targeted update without a YAML file. | Ergonomic operator setting patch. | read-only/local-mutating | `OperatorSettingsService.update_settings` with type coercion/validation | P1 |
| `atp settings validate --config settings.yaml` | Validate allowed keys, types, ranges, deprecated fields, and source-of-truth warnings. | Prevents bad persisted config before mutation. | read-only | `OperatorSettingsUpdateRequest`, allowed-key set from schema/model | P0 |
| `atp settings diff --config settings.yaml` | Compare proposed YAML against current DB values and show changed fields. | Operators need to understand X -> Y before applying. | read-only | `OperatorSettingsService.get_settings` plus typed patch validation | P0 |
| `atp settings export --output artifacts/settings/current.json` | Export current settings and metadata for reproducibility/audit. | Persisted settings are run inputs and should be artifactable. | read-only artifact output | `OperatorSettingsService.get_settings` | P1 |
| `atp settings risk-profile` | Print plain-language current risk profile. | REST already exposes this settings-derived view. | read-only | `OperatorSettingsService.get_risk_profile` | P1 |
| `atp settings advanced` | Show advanced read-only state: drawdown overrides, position caps, cost model metadata. | Mirrors `/settings/advanced`; helps explain what is settings vs policy/override. | read-only | `settings_routes.get_advanced_settings` logic factored into service | P1 |
| `atp settings sources` | Explain active source-of-truth for each setting and mark deprecated/persisted-only knobs. | Avoids confusion around fields like `min_sharpe_for_promotion` and `per_strategy_cap`. | read-only | Metadata from `_settings_response` and governance audit helpers | P0 |
| `atp settings snapshot --output artifacts/settings/snapshot.json` | Capture environment settings, operator settings, runtime control, strategy controls, allocation overrides, and snapshot hash. | Runtime already depends on a settings snapshot; settings CLI should expose it. | read-only artifact output | `runtime.replay_debug.load_settings_snapshot`, `snapshot_hash` | P1 |
| `atp settings verify-persisted --expect risk_tolerance=high` | Assert current DB settings match expected values; return non-zero on mismatch. | Useful after seed/update and in CI/local scripts. | read-only | `OperatorSettingsRepository.get_current` or service DTO | P1 |
| `atp settings verify-runtime-effect --config settings.yaml --parameter risk_tolerance --symbols AAPL,MSFT --start ... --end ...` | Prove a setting changes runtime outputs by baseline vs mutated replay. | This matches the desired X -> Y "does it actually affect results?" workflow, while clearly marked cross-domain. | cross-domain/runtime | migrate/split `handle_verify_risk_parameter_effects`, `RuntimeReplayDebugRunner` | P1 |
| `atp settings verify-notifications --config settings.yaml` | Prove `notify_*` flags gate expected notification/audit behavior. | Settings own flags, but behavior crosses runtime/operations. | cross-domain/runtime | migrate/split `handle_verify_notification_events` | P2 |
| `atp settings audit-log --limit 20` | Show recent `OPERATOR_SETTINGS_UPDATED` audit entries. | Settings changes need traceability. | read-only | `AuditLogRepository.list_events` filtered by `component="settings"` | P1 |
| `atp settings reset-defaults --updated-by operator --reason "..." --dry-run` | Restore default settings explicitly. | Useful for test environments; risky enough to need dry-run/confirmation. | local-mutating | `OperatorSettingsRepository.get_or_create_default` plus explicit update defaults | P2 |

## 4. Testing Plan

Phase 0: help commands

```powershell
atp settings --help
atp settings show --help
atp settings seed --help
atp settings validate --help
atp settings diff --help
atp settings set --help
atp settings risk-profile --help
atp settings advanced --help
atp settings sources --help
atp settings snapshot --help
atp settings verify-persisted --help
atp settings verify-runtime-effect --help
atp settings audit-log --help
```

Phase 1: safe read-only commands

```powershell
atp settings show --json
atp settings risk-profile
atp settings sources
atp settings advanced --json
atp settings validate --config fixtures/settings.yaml
atp settings diff --config fixtures/settings.yaml
atp settings snapshot --output artifacts/settings/settings-snapshot.json
atp settings verify-persisted --expect risk_tolerance=medium --expect rebalance_frequency=weekly
atp settings audit-log --limit 20
```

Phase 2: local DB mutation commands

```powershell
atp settings seed --config fixtures/settings.yaml --dry-run
atp settings seed --config fixtures/settings.yaml --updated-by local-operator --reason "seed local settings fixture"
atp settings set --key risk_tolerance --value high --updated-by local-operator --reason "test risk tolerance update" --dry-run
atp settings set --key risk_tolerance --value high --updated-by local-operator --reason "test risk tolerance update"
atp settings verify-persisted --expect risk_tolerance=high
```

Phase 3: cross-domain/runtime commands

```powershell
atp settings verify-runtime-effect --config fixtures/settings.yaml --parameter risk_tolerance --symbols AAPL,MSFT --start 2026-05-04T00:00:00Z --end 2026-05-08T00:00:00Z --max-ticks 3 --print-summary
atp settings verify-runtime-effect --config fixtures/settings.yaml --parameter target_portfolio_volatility --symbols AAPL,MSFT --start 2026-05-04T00:00:00Z --end 2026-05-08T00:00:00Z --max-ticks 3 --output-json artifacts/settings/runtime-effect.json
atp settings verify-notifications --config fixtures/settings.yaml
```

Phase 4: broker/external commands

No settings CLI command should call broker/external APIs directly. If a future command validates API/frontend behavior, put it under `api` or `platform` and have it consume settings CLI artifacts rather than live broker state.

## 5. Risks / Suspicious Wiring

- `settings.py` does not exist and is not registered in `cli/main.py`.
- Current settings seed/read behavior lives under `backtesting`, which makes operator configuration look like a simulation-only concern.
- Legacy `backtesting seed-settings` validates against a local `_VALID_SETTINGS_KEYS` set that does not include newer `OperatorSettingsRow` fields such as `max_total_strategy_allocation_pct`, `max_portfolio_symbol_exposure_usd`, `max_portfolio_symbol_pct`, `min_rebalance_interval_hours`, `min_allocation_change_pct`, or `turnover_penalty_weight`.
- `backtesting seed-settings` writes through `OperatorSettingsRepository.update_current` directly, not `OperatorSettingsService.update_settings`; it therefore bypasses `OPERATOR_SETTINGS_UPDATED` audit logging.
- `backtesting seed-settings` has no `--dry-run`.
- `OperatorSettingsRepository.update_current` sets arbitrary attributes from `values`; a CLI should validate keys/types/ranges with the REST schema or equivalent before calling it.
- `OperatorSettingsRepository.get_or_create_default` commits during a read path, so a nominal read command can create/persist a default settings row.
- Some settings are persisted but not the active source of truth for behavior. REST metadata already marks `min_sharpe_for_promotion`, `min_paper_trading_period_days`, and `per_strategy_cap` as deprecated/persisted-only for specific decisions; the CLI should make that obvious.
- Runtime effect verification is real cross-domain work. It should not be a plain `settings seed` side effect; it should be an explicit `verify-runtime-effect` style command with artifacts.
- Settings can alter runtime behavior materially. Mutating commands need `--reason`, `--updated-by`, audit logging, and JSON/artifact output.

## 6. Recommended Refactor / Extension

- Add a new `settings.py` CLI file and register `settings` in `cli/main.py`.
- Move `backtesting read-settings` to `settings show`.
- Move `backtesting seed-settings` to `settings seed`, add `--dry-run`, `--updated-by`, `--reason`, typed validation, and audit logging through `OperatorSettingsService`.
- Add `validate`, `diff`, `sources`, `snapshot`, and `verify-persisted` before adding broader runtime verification.
- Add `verify-runtime-effect` as an explicit cross-domain harness for proving setting X -> Y changes runtime outputs.
- Keep governance-specific behavior verification in `governance` and risk-limit behavior verification in `risk`, with `settings` providing the persisted settings patch/snapshot.
- Add consistent JSON and artifact output for settings snapshots, diffs, updates, and verification reports.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `atp settings` | Missing | yes | Medium | Add domain file and register it. |
| `atp settings show` | Missing | yes | Low | Implement from `OperatorSettingsService.get_settings`. |
| `atp settings seed` | Missing; currently `backtesting seed-settings` | yes | Medium | Move, add dry-run/validation/audit. |
| `atp settings validate` | Missing | yes | Low | Implement before mutating seed/set commands. |
| `atp settings diff` | Missing | yes | Low | Add preview of current -> proposed changes. |
| `atp settings set` | Missing | yes | Medium | Add targeted audited updates. |
| `atp settings sources` | Missing | yes | Low | Surface source-of-truth/deprecated metadata. |
| `atp settings snapshot` | Missing | yes | Low | Expose runtime settings snapshot and hash. |
| `atp settings verify-persisted` | Missing | yes | Low | Add scriptable assertions after seed/update. |
| `atp settings verify-runtime-effect` | Missing; partly in backtesting | partial | Medium | Add explicit cross-domain harness or wrap runtime/risk implementations. |
| `atp settings audit-log` | Missing | yes | Low | Add read-only settings change history. |
