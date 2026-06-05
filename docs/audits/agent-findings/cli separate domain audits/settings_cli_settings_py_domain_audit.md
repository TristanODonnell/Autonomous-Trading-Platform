# Settings CLI Domain Audit: `settings.py`

Target CLI domain: `settings`

Target CLI file: `src/autonomous_trading_platform/cli/commands/settings.py`

Audit status: target file does not exist yet. This audit therefore records the empty current inventory and the desired settings-domain entrypoints needed for local operation, testing, and migration from legacy backtesting commands.

Domain definition: `settings` owns operator configuration and persisted platform settings. It should cover operator settings inspection, validation, preview, audited mutation, source-of-truth explanation, and settings artifacts. It should not own pause/resume/trading mode (`controls`), kill switch/live gate/emergency halt (`safety`), capital/risk enforcement (`risk`), runtime cycle orchestration (`runtime`), REST/frontend smoke checks (`api`), or full workflow validation (`platform`).

## 1. Current CLI Inventory

No commands are registered in `src/autonomous_trading_platform/cli/commands/settings.py` because the file does not exist. The `settings` domain is also not registered in `src/autonomous_trading_platform/cli/main.py`.

| Command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp settings` | none | none | no | no | no | PLACEHOLDER |

Settings-like commands currently live under `backtesting` and should be treated as migration candidates:

| Existing command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp backtesting seed-settings` | `--config` | `handle_seed_settings` | yes | no | no | LOCAL_DB_MUTATION |
| `atp backtesting read-settings` | none | `handle_read_settings` | conditional | no | mostly | READ_ONLY_SAFE |
| `atp backtesting verify-risk-parameter-effects` | `--controls`, `--settings`, `--symbols`, `--start`, `--end`, `--starting-cash`, `--random-seed`, `--reset-sim-state`, `--print-summary`, repeatable `--parameter` | `handle_verify_risk_parameter_effects` | yes/conditional | no | no | CROSS_DOMAIN_RUNTIME |
| `atp backtesting verify-notification-events` | `--controls`, `--settings` | `handle_verify_notification_events` | yes/conditional | no | no | CROSS_DOMAIN_RUNTIME |
| `atp backtesting verify-auto-promotion` | `--settings` | `handle_verify_auto_promotion` | yes/conditional | no | no | CROSS_DOMAIN_RUNTIME |
| `atp backtesting verify-auto-demotion` | `--settings` | `handle_verify_auto_demotion` | yes/conditional | no | no | CROSS_DOMAIN_RUNTIME |

Note: `read-settings` is actually read-only: `handle_read_settings` calls `OperatorSettingsRepository.get_current()` (which returns `None` if no row exists) and prints a "run seed-settings first" message. It does NOT call `get_or_create_default()`. The "conditional mutation" concern applies only to callers that use the create-on-read path, not to this handler.

## 2. Domain Responsibility Check

| Command | Classification | Correct domain | Notes |
|---|---|---|---|
| `atp settings` | correctly placed, missing | settings | The domain belongs in the final CLI taxonomy but has not been implemented. |
| `atp backtesting seed-settings` | should move to another domain | settings | Persisting operator configuration is not a backtesting responsibility. |
| `atp backtesting read-settings` | should move to another domain | settings | Reading persisted operator settings is a core settings-domain operation. |
| `atp backtesting verify-risk-parameter-effects` | should be duplicated/wrapped elsewhere | settings plus runtime/risk/platform | Settings owns the input patch and snapshot. Runtime/risk/platform own behavioral proof that settings affect execution or replay. |
| `atp backtesting verify-notification-events` | should be duplicated/wrapped elsewhere | settings plus operations/platform | Settings owns `notify_*` flags. Event triggering and notification verification are operational/platform concerns. |
| `atp backtesting verify-auto-promotion` | should move to another domain | governance, with settings preflight | `auto_promote_enabled` is a setting, but promotion eligibility and state transition behavior belong to governance. |
| `atp backtesting verify-auto-demotion` | should move to another domain | governance/risk, with settings preflight | `auto_demote_on_breach` is a setting, but demotion behavior belongs to governance/risk. |

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why it belongs in this domain | Class | Implementation target service/function | Priority |
|---|---|---|---|---|---:|
| `atp settings show` | Print current persisted operator settings. | Basic operator configuration inspection. | read-only, but may create default row unless repository behavior changes | `OperatorSettingsService.get_settings` | P0 |
| `atp settings show --json` | Emit scriptable JSON settings output. | CI, audits, and local scripts need stable machine-readable output. | read-only, but may create default row unless repository behavior changes | `OperatorSettingsService.get_settings` plus DTO serialization | P0 |
| `atp settings sources` | Explain active source of truth and deprecated/persisted-only fields. | Prevents confusion around settings that are persisted but ignored for active governance/allocation decisions. | read-only | Metadata equivalent to `settings_routes._settings_response` | P0 |
| `atp settings validate --config fixtures/settings.yaml` | Validate allowed keys, types, ranges, enums, and deprecated-field warnings before any write. | Settings mutation needs a safe preflight. | read-only | `OperatorSettingsUpdateRequest` or a shared CLI schema aligned with REST | P0 |
| `atp settings diff --config fixtures/settings.yaml` | Compare current DB values to a proposed settings file. | Operators need to see current -> proposed changes before applying. | read-only, but may create default row unless repository behavior changes | `OperatorSettingsService.get_settings` plus typed patch validation | P0 |
| `atp settings seed --config fixtures/settings.yaml --dry-run` | Preview a YAML settings patch without writing. | Replaces legacy `backtesting seed-settings` with safe behavior. | read-only | YAML loader plus `OperatorSettingsUpdateRequest` validation | P0 |
| `atp settings seed --config fixtures/settings.yaml --updated-by local-operator --reason "seed local fixture"` | Persist a YAML settings patch and record audit evidence. | This is the primary local settings mutation entrypoint. | local-mutating | `OperatorSettingsService.update_settings` with `AuditLogRepository` | P0 |
| `atp settings set --key risk_tolerance --value high --updated-by local-operator --reason "raise risk tolerance" --dry-run` | Preview a single setting update. | Ergonomic targeted operator patch. | read-only | Shared key/value coercion and `OperatorSettingsUpdateRequest` validation | P1 |
| `atp settings set --key risk_tolerance --value high --updated-by local-operator --reason "raise risk tolerance"` | Persist a single audited setting update. | Operators often need one-field changes without editing YAML. | local-mutating | `OperatorSettingsService.update_settings` | P1 |
| `atp settings risk-profile` | Print the plain-language risk profile. | REST already exposes settings-derived operator context. | read-only | `OperatorSettingsService.get_risk_profile` | P1 |
| `atp settings advanced` | Show advanced settings context: drawdown overrides, asset caps, and cost model metadata. | Mirrors `/settings/advanced` and clarifies what is settings vs policy/override. | read-only | Factor logic from `settings_routes.get_advanced_settings` into a service | P1 |
| `atp settings export --output artifacts/settings/current.json` | Write current settings and metadata to an artifact. | Settings are reproducibility inputs for runs and audits. | read-only artifact output | `OperatorSettingsService.get_settings` plus metadata builder | P1 |
| `atp settings snapshot --output artifacts/settings/snapshot.json` | Capture operator settings, environment-derived settings, controls references, allocation override context, and a hash. | The runtime needs reproducible settings inputs; the settings CLI should emit them as artifacts. | read-only artifact output | Existing runtime replay/settings snapshot helpers if available, otherwise a new settings snapshot service | P1 |
| `atp settings verify-persisted --expect risk_tolerance=high --expect rebalance_frequency=weekly` | Assert current DB values match expected values; exit nonzero on mismatch. | Useful after seed/update and in CI. | read-only | `OperatorSettingsService.get_settings` or repository read | P1 |
| `atp settings audit-log --limit 20` | Show recent `OPERATOR_SETTINGS_UPDATED` audit entries. | Settings mutations need traceability. | read-only | `AuditLogRepository` filtered by `component="settings"` and event type | P1 |
| `atp settings verify-runtime-effect --config fixtures/settings.yaml --parameter risk_tolerance --symbols AAPL,MSFT --start ... --end ...` | Prove a settings change affects deterministic replay/runtime output. | Settings owns the patch, but this should be clearly marked cross-domain. | cross-domain/runtime | Split or wrap `handle_verify_risk_parameter_effects` | P2 |
| `atp settings verify-notifications --config fixtures/settings.yaml` | Prove notification toggles gate expected events. | Settings owns `notify_*`; behavior verification crosses operations/platform. | cross-domain/runtime or platform-level | Split or wrap `handle_verify_notification_events` | P2 |
| `atp settings reset-defaults --updated-by local-operator --reason "reset local fixture" --dry-run` | Preview resetting settings to defaults. | Useful for local and CI cleanup, but risky. | read-only dry run | Defaults from `OperatorSettingsRepository.get_or_create_default` or shared defaults object | P2 |
| `atp settings reset-defaults --updated-by local-operator --reason "reset local fixture"` | Persist explicit defaults and audit the change. | Useful for local fixtures when guarded and audited. | local-mutating | `OperatorSettingsService.update_settings` using explicit default payload | P3 |

## 4. Testing Plan

Phase 0: help commands

```powershell
atp settings --help
atp settings show --help
atp settings sources --help
atp settings validate --help
atp settings diff --help
atp settings seed --help
atp settings set --help
atp settings risk-profile --help
atp settings advanced --help
atp settings export --help
atp settings snapshot --help
atp settings verify-persisted --help
atp settings audit-log --help
atp settings verify-runtime-effect --help
atp settings verify-notifications --help
```

Phase 1: safe read-only commands

```powershell
atp settings show --json
atp settings sources
atp settings risk-profile
atp settings advanced --json
atp settings validate --config fixtures/settings.yaml
atp settings diff --config fixtures/settings.yaml
atp settings seed --config fixtures/settings.yaml --dry-run
atp settings set --key risk_tolerance --value high --updated-by local-operator --reason "preview local update" --dry-run
atp settings export --output artifacts/settings/current.json
atp settings snapshot --output artifacts/settings/settings-snapshot.json
atp settings verify-persisted --expect risk_tolerance=medium --expect rebalance_frequency=weekly
atp settings audit-log --limit 20
```

Phase 2: local DB mutation commands

```powershell
atp settings seed --config fixtures/settings.yaml --updated-by local-operator --reason "seed local settings fixture"
atp settings verify-persisted --expect risk_tolerance=medium --expect rebalance_frequency=weekly
atp settings set --key risk_tolerance --value high --updated-by local-operator --reason "test risk tolerance update"
atp settings verify-persisted --expect risk_tolerance=high
atp settings audit-log --limit 5
```

Phase 3: cross-domain/runtime commands

```powershell
atp settings verify-runtime-effect --config fixtures/settings.yaml --parameter risk_tolerance --symbols AAPL,MSFT --start 2026-05-04T00:00:00Z --end 2026-05-08T00:00:00Z --starting-cash 100000 --random-seed 42 --print-summary
atp settings verify-runtime-effect --config fixtures/settings.yaml --parameter target_portfolio_volatility --symbols AAPL,MSFT --start 2026-05-04T00:00:00Z --end 2026-05-08T00:00:00Z --starting-cash 100000 --random-seed 42 --output artifacts/settings/runtime-effect.json
atp settings verify-notifications --config fixtures/settings.yaml
```

Phase 4: broker/external commands

No settings-domain command should directly call a broker or external market data API. If a future workflow needs live REST/frontend smoke validation, put it under `api` or `platform`. If a future workflow needs live trading safety validation, put it under `safety`. The settings domain may emit artifacts consumed by those domains.

## 5. Risks / Suspicious Wiring

- `src/autonomous_trading_platform/cli/commands/settings.py` does not exist.
- `src/autonomous_trading_platform/cli/main.py` does not import or register a `settings` domain.
- Operator configuration commands currently live under `backtesting`, which makes persisted platform settings look simulation-specific.
- Legacy `backtesting seed-settings` has no `--dry-run`.
- Legacy `backtesting seed-settings` has no explicit `--updated-by` or `--reason`; it uses a fixture actor.
- Legacy `backtesting seed-settings` writes through `OperatorSettingsRepository.update_current` instead of `OperatorSettingsService.update_settings`, so it bypasses the `OPERATOR_SETTINGS_UPDATED` audit log path used by REST.
- `OperatorSettingsRepository.update_current` applies arbitrary keys with `setattr`; CLI mutation must validate keys, types, and ranges before calling it.
- The legacy `_VALID_SETTINGS_KEYS` allowlist in `backtesting.py` omits several `OperatorSettingsRow` columns, including `max_total_strategy_allocation_pct`, `max_portfolio_symbol_exposure_usd`, `max_portfolio_symbol_pct`, `min_rebalance_interval_hours`, `min_allocation_change_pct`, `turnover_penalty_weight`, and portfolio drawdown governance fields.
- `OperatorSettingsService.OperatorSettingsDTO` omits some persisted model fields that may still matter to allocation/rebalance behavior.
- REST update schema omits some persisted model fields. Decide whether the CLI should align with REST strictly or expose a broader admin-only settings surface.
- `OperatorSettingsRepository.get_or_create_default()` commits during default creation, so read-like commands that call `OperatorSettingsService.get_settings()` may mutate local DB state.
- Some persisted settings are deprecated or ignored for specific active decisions: `min_sharpe_for_promotion`, `min_paper_trading_period_days`, and `per_strategy_cap` are not the active source of truth for promotion thresholds or allocation targets.
- Settings can materially affect runtime behavior; mutating commands need audit logging, actor, reason, JSON output, and ideally artifact output.
- Cross-domain verification commands should not be hidden behind simple seed/update commands. They need explicit names, summaries, and output artifacts.
- Settings commands should not call broker/live/external systems. No broker-facing settings command is recommended.
- `auto_promote_enabled` IS wired: `AutoPromotionService.run()` checks this flag at line 172 and returns `skipped_reason="auto_promote_disabled"` when the flag is false. The `_GOVERNANCE_SETTING_WIRING` table in `backtesting.py` that classifies it as `FLAG_NOT_WIRED` is stale. The `backtesting verify-governance-allocation` artifact will still report it as `FLAG_NOT_WIRED` until that handler is updated.
- `auto_demote_on_breach` IS wired: `AutoDemotionService.run()` checks this flag at line 226 and returns `skipped_reason="auto_demote_disabled"` when the flag is false. Same stale classification issue in `backtesting.py`.

## 6. Recommended Refactor / Extension

- Add `src/autonomous_trading_platform/cli/commands/settings.py` and register it in `src/autonomous_trading_platform/cli/main.py`.
- Add P0 commands first: `show`, `sources`, `validate`, `diff`, and `seed`.
- Move or wrap `backtesting read-settings` as `settings show`.
- Move or wrap `backtesting seed-settings` as `settings seed`.
- Add `--dry-run`, `--updated-by`, `--reason`, typed validation, and audit logging to all mutating settings commands.
- Prefer `OperatorSettingsService.update_settings` over direct repository mutation for writes.
- Add JSON output and artifact output for read, diff, seed, export, snapshot, and verification commands.
- Add an explicit source-of-truth command before expanding behavioral verification.
- Keep governance-specific promotion/demotion verification in `governance`, and risk-limit behavioral verification in `risk` or `runtime`, with settings artifacts as inputs.
- Add a small shared settings metadata builder so REST and CLI do not drift.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `atp settings` | Missing | yes | Medium | Create `settings.py` and register the domain. |
| `atp settings show` | Missing | yes | Low | Implement from `OperatorSettingsService.get_settings`; document default-row side effect or remove it. |
| `atp settings sources` | Missing | yes | Low | Reuse REST metadata/source-of-truth logic via shared helper. |
| `atp settings validate` | Missing | yes | Low | Validate YAML against REST-aligned schema before writes. |
| `atp settings diff` | Missing | yes | Low | Show current -> proposed settings changes. |
| `atp settings seed` | Missing; legacy exists as `backtesting seed-settings` | yes | Medium | Move/wrap, add dry-run, actor, reason, validation, audit logging. |
| `atp settings set` | Missing | yes | Medium | Add targeted audited update with dry-run. |
| `atp settings risk-profile` | Missing | yes | Low | Implement from `OperatorSettingsService.get_risk_profile`. |
| `atp settings advanced` | Missing | yes | Low | Factor REST route logic into a service before exposing it. |
| `atp settings export` | Missing | yes | Low | Emit settings artifact for reproducibility. |
| `atp settings snapshot` | Missing | yes | Low | Emit hashed settings snapshot artifact. |
| `atp settings verify-persisted` | Missing | yes | Low | Add scriptable assertions after seed/update. |
| `atp settings audit-log` | Missing | yes | Low | Show `OPERATOR_SETTINGS_UPDATED` history. |
| `atp settings verify-runtime-effect` | Missing; partly legacy under backtesting | partial | Medium | Add explicit cross-domain wrapper or move to runtime/risk with settings inputs. |
| `atp settings verify-notifications` | Missing; partly legacy under backtesting | partial | Medium | Add explicit cross-domain wrapper or move to operations/platform. |
| `atp settings reset-defaults` | Missing | yes | Medium | Add only after dry-run, actor, reason, and audit logging are in place. |
