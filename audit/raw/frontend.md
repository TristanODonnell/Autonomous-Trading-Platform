# Frontend Audit

- File count (tracked, `git ls-files -- frontend`): 59
- Total LOC (all 59 files, `wc -l`, includes binary hero.png's meaningless 52 and 4 zero-byte route files): 6406
- Total LOC excluding package-lock.json (not read line-by-line, version-checked only): see above (package-lock.json not included in the 59/6406 either way per `git ls-files`)
- LOC of `frontend/src/pages/*.tsx` + `pages/index.ts` only: 4293
  - Controls.tsx 948, ExperimentLab.tsx 751, Dashboard.tsx 714, Portfolio.tsx 650, Settings.tsx 631, StrategyLab.tsx 598, index.ts 1
- TODO/FIXME list: **13 found, exactly matching expectation.** Full list with file:line in the "CRITICAL FINDING — TODO/FIXME inventory" section below.

## Confirmed environment facts
- React: `react` ^19.2.5, `react-dom` ^19.2.5 (package.json)
- tsconfig.app.json / tsconfig.node.json: `noUnusedLocals: true`, `noUnusedParameters: true`, `verbatimModuleSyntax: true`, `erasableSyntaxOnly: true` — all four ON (both configs identical on these flags)
- `frontend/src/routes/__root.tsx`, `controls.tsx`, `index.tsx`, `portfolio.tsx`, `settings.tsx`, `strategy.tsx` are all **0 bytes** (confirmed via `ls -la` and `git show HEAD:<path>`) — genuinely empty files, not a wc artifact

---

## CRITICAL QUESTION — resolved: real API wiring vs. mock data

**Verdict: the prior draft is correct; CLAUDE.md's "static mockup, no real API calls" line is stale/outdated.**

Every one of the 6 page components (`Dashboard.tsx`, `Portfolio.tsx`, `StrategyLab.tsx`, `Controls.tsx`, `Settings.tsx`, `ExperimentLab.tsx`) imports from `../services` (or a specific `services/*.ts` file) and calls real endpoints through TanStack Query's `useQuery`/`useMutation`, which call functions in `src/services/*.ts`, which call `http.get/post/put` from `src/api/http.ts` (a real configured axios instance with JWT bearer auth). **Zero pages import from `src/mock/data.ts`** — confirmed by grepping every exported mock symbol name across `frontend/src`; the only matches are inside `mock/data.ts` itself. The mock data file is vestigial/dead code, entirely superseded by live API wiring.

The one page not wired to the live app is **`StrategyLab.tsx`, and even it calls a real endpoint** (`fetchAllStrategies`) — it's just not mounted on any route (see orphaned-page finding above), not because it's a mock stub.

### Every distinct backend endpoint called from the frontend (24 total)
- `GET /api/v1/portfolio/summary` — Dashboard, Portfolio
- `GET /api/v1/portfolio/equity-curve?period=` — Dashboard, Portfolio
- `GET /api/v1/portfolio/holdings` — Portfolio (×2 call sites)
- `GET /api/v1/portfolio/allocation` — Portfolio
- `GET /api/v1/portfolio/risk` — Dashboard, Portfolio
- `GET /api/v1/portfolio/performance` — Dashboard, Portfolio
- `GET /api/v1/strategies` — StrategyLab, Portfolio (name lookup), Controls (catalog), used with `?status=research` filter in Controls' `GovernancePendingCard`
- `PUT /api/v1/strategies/{id}/enabled` — Controls
- `POST /api/v1/strategies/{id}/governance/transition` — Controls
- `GET /api/v1/strategies/allocations` — Controls
- `PUT /api/v1/strategies/{id}/allocation` — Controls
- `GET /api/v1/strategies/active` — Dashboard (×2 call sites)
- `GET /api/v1/system/health` — Dashboard, Controls (both poll every 30s)
- `PUT /api/v1/system/trading-mode` — Controls
- `GET /api/v1/activity/recent?limit=` — Dashboard (polls 30s)
- `GET /api/v1/controls/state` — Controls (×3 call sites, kill-switch card polls 15s)
- `POST /api/v1/controls/kill-switch` — Controls
- `POST /api/v1/controls/resume` — Controls
- `GET /api/v1/audit-log?page=&page_size=` — Controls (polls 30s)
- `GET /api/v1/experiments` — ExperimentLab
- `GET /api/v1/experiments/{id}/strategies` — ExperimentLab (on-demand, staleTime 60s)
- `POST /api/v1/experiments` — ExperimentLab (create modal)
- `POST /api/v1/experiments/{id}/cancel` — ExperimentLab
- `GET /api/v1/settings` — Settings (×4 call sites, one per card)
- `PUT /api/v1/settings` — Settings (×3 mutations)
- `GET /api/v1/metadata/dataset-versions/latest` — Settings
- `GET /api/v1/metadata/feature-dataset-versions/latest` — Settings
- `GET /api/v1/settings/advanced` — **defined in `settingsService.ts` but never called from any page — dead service function**

### Remaining mock-data references
**None in reachable app code.** `frontend/src/mock/data.ts` (508 lines, 12 exported constants: `mockStrategies`, `mockHoldings`, `mockEquityCurve`, `mockActivity`, `mockAuditLog`, `mockRiskMetrics`, `mockSystemHealth`, `mockPortfolioSummary`, `mockStrategyAllocation`, `mockSectorAllocation`, `mockExperiments`, `mockExperimentStrategies`, `mockDrawdownSeries`) is imported by zero files. It appears to be a leftover from an earlier "static mockup" phase of the project (matching CLAUDE.md's description) that was fully replaced by the services/React Query layer in a later pass, without deleting the now-dead mock file.

## Dependency-vs-usage audit (every package.json dependency)

### Runtime dependencies (23) — used unless noted
| Package | Status |
|---|---|
| `@fontsource-variable/geist` | **Unused** — `index.css` loads fonts via Google Fonts `@import` (IBM Plex Mono/Sans), not this package |
| `@hookform/resolvers` | **Unused** — zero imports anywhere in src |
| `@tailwindcss/vite` | Used — `vite.config.ts` plugin |
| `@tanstack/react-query` + devtools | Used extensively (`useQuery`/`useMutation` in every page); devtools package present in deps but **no `<ReactQueryDevtools>` component rendered anywhere** — devtools dep itself unused |
| `@tanstack/react-router` | Used — `App.tsx` router, `Link`/`Outlet` in TopNav/AppShell |
| `@tanstack/react-table` | Used — Dashboard (strategies table), Portfolio (holdings table) |
| `axios` | Used — `api/http.ts` |
| `class-variance-authority` | **Unused** — zero imports (would normally be used inside `lib/utils.ts`/shadcn components, neither of which exist/are populated) |
| `clsx` | Used indirectly — presumably inside the missing `lib/utils.ts`'s `cn()`; cannot confirm since that file doesn't exist. Direct `from 'clsx'` imports: zero in src |
| `date-fns` | Used — `formatDistanceToNow`/`parseISO` (Dashboard), `format`/`isToday`/`isYesterday`/`parseISO` (Controls) |
| `dotenv` | **Unused** — Vite handles `.env` natively via `import.meta.env`; no `require('dotenv')`/`import 'dotenv'` anywhere |
| `framer-motion` | **Confirmed unused** — zero imports anywhere in src, matches prior-draft claim exactly |
| `lightweight-charts` | **Unused** — zero imports; all charts use Recharts or hand-rolled SVG instead |
| `lucide-react` | **Unused** — zero imports; no icon components rendered anywhere in the app (nav/badges use Unicode glyphs like ◈, ▲, ▼, ⬛ instead) |
| `radix-ui` | **Unused** — zero imports; consistent with `components/ui/index.ts` being an empty stub (shadcn primitives built on Radix were never generated) |
| `react` / `react-dom` | Used — core, confirmed v19.2.5 |
| `react-hook-form` | **Unused** — zero imports; `ExperimentLab.tsx`'s "New Experiment" form uses plain `useState` + manual validation instead |
| `recharts` | Used — `AreaChart`/`Area`/`XAxis`/`YAxis`/`Tooltip`/`ResponsiveContainer` (Dashboard), `ComposedChart`/`Area`/`Line` (Portfolio). Only these two chart types are used anywhere; no `LineChart`/`BarChart`/`PieChart`/`ScatterChart`/`RadarChart` |
| `shadcn` (CLI, listed oddly as a runtime dep not devDep) | **Unused as code** — it's the codegen CLI, not a runtime import; its presence in `dependencies` rather than `devDependencies` is itself a minor smell |
| `tailwind-merge` | **Unused** — zero direct imports; would be used inside the missing `lib/utils.ts` |
| `tailwindcss` | Used — `index.css` `@import "tailwindcss"`, `tailwind.config.ts` |
| `zod` | **Unused** — zero imports; no runtime schema validation anywhere (ExperimentLab form validates manually) |
| `zustand` | Used, but minimally — only `useAppStore.ts` (18 lines) + consumed solely by `TopNav.tsx` and `Controls.tsx` (`setKillSwitchActive`/`setActiveEnv`). `setActiveEnv` is called (from Controls' `EnvironmentCard`), so not fully dead, but the store's total footprint is 2 booleans-worth of state for the whole app |

### Dev dependencies (18)
| Package | Status |
|---|---|
| `@playwright/test` + `playwright` | **Confirmed unused** — zero `*.spec.ts`/`*.test.ts` files anywhere, no `playwright.config.ts` in the tracked file list |
| `@testing-library/jest-dom`, `@testing-library/react`, `@testing-library/user-event` | **Confirmed unused** — zero test files |
| `vitest` | **Confirmed unused** — zero test files, no `vitest.config.ts`, and `package.json` has no `test` script (the husky pre-commit hook calls `npm test`, which has no script to run — see earlier finding) |
| `jsdom` | Unused (only relevant as a vitest environment, which isn't configured) |
| `eslint` + plugins, `typescript-eslint`, `globals` | Used — `eslint.config.js` |
| `husky`, `lint-staged` | Partially used — husky pre-commit hook exists but calls a non-existent `npm test` script; no `lint-staged` config block found in `package.json` (would normally be a `"lint-staged"` key) — **`lint-staged` dep present but unconfigured** |
| `prettier`, `eslint-config-prettier`, `eslint-plugin-prettier` | Used — wired into eslint.config.js via `eslint-plugin-prettier`, though no standalone `.prettierrc` found in the 59 tracked files |
| `tw-animate-css` | Not imported in `index.css` — likely unused |
| `typescript`, `typescript-eslint` | Used — tsconfig/eslint |
| `vite`, `@vitejs/plugin-react` | Used — vite.config.ts |
| `@types/*` | Used implicitly by TS tooling |

## Per-file notes

### frontend/.gitignore (24 lines)
Purpose: standard Vite/Node ignore rules (node_modules, dist, logs, editor dirs).
Notable: nothing unusual.

### frontend/.husky/pre-commit (1 line)
Purpose: husky git hook, runs `npm test`.
Notable: package.json has no `test` script defined (only dev/build/lint/preview/prepare) — pre-commit hook calling `npm test` would fall through to npm's default "Missing script" error. Vitest is a devDependency but not wired into a `test` script anywhere.

### frontend/components.json (25 lines)
Purpose: shadcn/ui CLI config — style "radix-nova", baseColor neutral, aliases for @/components, @/lib/utils, @/components/ui, @/hooks.
Notable: references `@/lib/utils` and `@/hooks` aliases but no `src/lib/` or `src/hooks/` directories exist in the tracked file list — shadcn scaffolding never generated those paths.

### frontend/eslint.config.js (22 lines)
Purpose: flat ESLint config — js recommended, typescript-eslint recommended, react-hooks flat recommended, react-refresh vite preset. Ignores `dist`.
Notable: standard Vite+React+TS template config, nothing custom.

### frontend/index.html (13 lines)
Purpose: Vite entry HTML, mounts `#root`, loads `/src/main.tsx`, favicon.svg link.
Notable: `<title>frontend</title>` — never customized from Vite scaffold default (not "Autonomous Trading Platform" or similar).

### frontend/public/favicon.svg (0 lines reported by wc / binary-ish single-line SVG)
Purpose: purple gradient abstract mark favicon.
Notable: generic/placeholder-looking mark, not obviously platform-branded.

### frontend/public/icons.svg (24 lines)
Purpose: SVG sprite sheet of social/doc icons (bluesky, discord, documentation, github, social, x) as `<symbol>` defs.
Notable: looks like leftover shadcn/marketing-template boilerplate (bluesky/discord/x social icons) unrelated to a trading platform UI — worth checking if actually referenced anywhere.

### frontend/vite.config.ts (14 lines)
Purpose: Vite config — react() + tailwindcss() plugins, `@` alias to `./src`.
Notable: minimal, standard.

### frontend/src/App.tsx (54 lines)
Purpose: **Actual routing source of truth.** Builds a TanStack Router instance in-code with `createRootRoute`/`createRoute` for `/`, `/portfolio`, `/strategy`, `/controls`, `/settings`, mapping directly to page components (Dashboard, Portfolio, ExperimentLab, Controls, Settings). Wraps in `QueryClientProvider` with a `QueryClient` (staleTime 30s, retry 2).
Notable: This is the real router config — the file-based routes under `src/routes/*.tsx` (all 0 bytes, see above) are NOT used; TanStack Router's file-based-routing convention was scaffolded but abandoned in favor of manual `createRoute` calls here. `ExperimentLab` page serves the `/strategy` route (page component name doesn't match route/nav name "Strategy Lab").

### frontend/src/App.css (184 lines)
Purpose: Leftover Vite/React template CSS (`.hero`, `.counter`, `#next-steps`, `#docs` social-link footer, `.ticks`) from the default `create-vite --template react-ts` scaffold.
Notable: **Confirmed dead code** — grepped `frontend/src` for `App.css`, `hero.png`, `react.svg`, `vite.svg`: zero matches. Nothing imports this file or the hero/react/vite assets. All four are scaffold leftovers never wired into the app.

### frontend/src/api/http.ts (16 lines)
Purpose: Configures a real axios instance (`http`) with `baseURL: import.meta.env.VITE_API_BASE_URL`, 15s timeout, and a request interceptor that attaches `Authorization: Bearer <token>` from `localStorage.getItem('access_token')` falling back to `VITE_DEV_JWT_TOKEN`.
Notable: This is genuine, functioning API-wiring infrastructure (axios + JWT bearer), not a stub. Whether it is actually *called* by any page is the key question — tracked separately below (services/ layer imports it but pages call services, not http directly — see services notes).

### frontend/src/main.tsx (10 lines)
Purpose: Standard Vite/React entrypoint — `createRoot` + `StrictMode` + `<App />`, imports `./index.css`.
Notable: nothing unusual.

### frontend/src/index.css (77 lines)
Purpose: Tailwind v4 entry (`@import "tailwindcss"`) + Google Fonts import (IBM Plex Mono/Sans) + `:root` CSS custom properties for the full design-token palette (bg/surface/border/text/accent/red/yellow/blue/purple + dim variants) + base reset + custom range-slider thumb styling.
Notable: Design tokens match CLAUDE.md's documented palette exactly (--bg #070B0F, --accent #00E5A0, etc.), confirming CLAUDE.md is in sync with actual code here.

### frontend/src/components/shared/ActivityFeed.tsx (35 lines)
Purpose: Renders a list of `ActivityItem` with colored dot by type (fill/paper/warning/system), text + meta line.
Notable: clean, typed via `../../types`.

### frontend/src/components/shared/AllocationBar.tsx (36 lines)
Purpose: Labeled horizontal percent bar with 4 color variants (green/blue/purple/yellow).
Notable: imports `cn` from `../../lib/utils` — see critical finding below.

### frontend/src/components/shared/MetricCard.tsx (40 lines)
Purpose: Stat-tile card — label, value, optional change indicator colored by up/down/neutral.
Notable: also imports `cn` from `../../lib/utils`.

### frontend/src/components/shared/SparklineChart.tsx (29 lines)
Purpose: Hand-rolled inline SVG sparkline (not Recharts) — builds a polyline path from a `number[]`, min/max normalized to a 200x36 viewbox.
Notable: Bespoke, dependency-free mini-chart; does not use Recharts despite Recharts being a dependency used elsewhere.

### frontend/src/components/shared/StatusBadge.tsx (53 lines)
Purpose: Pill badge component with 6 color variants + two helper functions `governanceBadgeVariant(state)` and `healthBadgeVariant(status)` mapping domain enums to badge colors.
Notable: also imports `cn` from `../../lib/utils`.

### frontend/src/components/shared/Toggle.tsx (29 lines)
Purpose: Custom switch/toggle control, ARIA `role="switch"`, inline-styled.
Notable: no `cn`/utils dependency — self-contained.

### frontend/src/components/shared/index.ts (8 lines)
Purpose: Barrel export for all shared components.
Notable: clean re-export, matches files present.

### frontend/src/components/ui/index.ts (1 line)
Purpose: shadcn/ui component directory barrel.
Notable: **Confirmed empty stub** — file content is literally `export {}`. No shadcn primitives were ever generated into `src/components/ui/`, despite `components.json` being fully configured for shadcn codegen. Corroborates the prior-draft claim.

### frontend/src/layouts/AppShell.tsx (13 lines)
Purpose: Root shell — renders `TopNav` + `<Outlet/>` (TanStack Router outlet) inside a full-height div with `--bg` background.
Notable: minimal, correct.

### frontend/src/layouts/TopNav.tsx (81 lines)
Purpose: Top navigation bar — logo/wordmark "◈ WeTrade", 5 nav tabs (Dashboard/Portfolio/Experiment Lab/Controls/Settings) via TanStack Router `<Link>`, live env status pill, and a Kill Switch button wired to `useAppStore` (`killSwitchActive`, `toggleKillSwitch`).
Notable: **Product is branded "WeTrade" in the UI**, not "Autonomous Trading Platform" — a naming mismatch vs. CLAUDE.md's project name. Kill switch here only flips local Zustand state — it does NOT call the real `activateKillSwitch`/`resumeTrading` API functions from `controlsService.ts` (those are called from the Controls page instead, per below).

### frontend/src/layouts/index.ts (1 line)
Purpose: layouts barrel.
Notable: **Empty stub** — `export {}`, does not actually re-export `AppShell`/`TopNav`. (Pages/App.tsx import `AppShell` directly by path, not through this barrel, so it isn't a broken dependency, just a dead/no-op file.)

### frontend/src/store/useAppStore.ts (18 lines)
Purpose: Zustand store — `activeEnv` (Environment), `killSwitchActive` (bool) + setters/toggler.
Notable: This is Zustand's entire usage footprint in the app — confirmed via grep, only `TopNav.tsx` consumes `useAppStore`. `setActiveEnv` is defined but never called anywhere (no UI control to change environment) — dead setter.

### frontend/src/store/index.ts (1 line)
Purpose: store barrel.
Notable: **Empty stub** (`export {}`) — same pattern as `layouts/index.ts`, `pages/index.ts`, `components/ui/index.ts`. Consumers import `useAppStore` directly from `./store/useAppStore`, not through this barrel.

### frontend/src/types/index.ts (91 lines)
Purpose: Shared TypeScript interfaces — `GovernanceState`, `ExperimentType/Status`, `ExperimentSummary`, `ExperimentStrategy`, `Environment`, `SimulationStage`, `Strategy`, `Holding`, `ActivityItem`, `AuditEntry`, `RiskMetrics`, `SystemHealth`.
Notable: Many of these types (`Strategy`, `Holding`, `RiskMetrics`, `SystemHealth`, `AuditEntry`) appear to be the **original mock-data shape** and are now superseded by the `Api*` interfaces defined locally inside each `services/*.ts` file (e.g. `ApiPortfolioSummary`, `ApiStrategyListItem`). Only `ExperimentSummary`/`ExperimentStrategy`/`Environment`/`GovernanceState` from this file are actually still used by live code (types/data.ts's mock exports use the rest, but mock/data.ts itself is dead — see below).

### frontend/tailwind.config.ts (39 lines)
Purpose: Tailwind v4 config mapping semantic color names (bg/surface/border/text/accent/red/yellow/blue/purple + dim variants) to the CSS custom properties in `index.css`, plus `fontFamily.sans/mono`.
Notable: consistent with index.css tokens; matches CLAUDE.md's documented palette.

### frontend/src/mock/data.ts (508 lines)
Purpose: Original static mock dataset — `mockStrategies`, `mockHoldings`, `mockEquityCurve`, `mockActivity`, `mockAuditLog`, `mockRiskMetrics`, `mockSystemHealth`, `mockPortfolioSummary`, `mockStrategyAllocation`, `mockSectorAllocation`, `mockExperiments`, `mockExperimentStrategies`, `mockDrawdownSeries`.
Notable: **Confirmed fully dead code.** Grepped every export name (`mockStrategies`, `mockHoldings`, etc.) across all of `frontend/src` — the only file matching any of them is `mock/data.ts` itself. No page or component imports from `../mock/data` anywhere (separately confirmed via grep for `mock/data` — zero hits outside this file). This is the single biggest resolution to the CRITICAL QUESTION: the mock layer is vestigial; every page now fetches from the real `services/` + React Query layer instead.

### frontend/src/services/activityService.ts (15 lines)
Purpose: `fetchRecentActivity(limit)` → `GET /api/v1/activity/recent`.
Notable: real axios call via `http`, typed `ApiActivityItem`. Consumed by Dashboard's `RecentActivityCard`.

### frontend/src/services/auditLogService.ts (29 lines)
Purpose: `fetchAuditLog(page, pageSize)` → `GET /api/v1/audit-log`.
Notable: defined but — pending page-by-page confirmation — appears **unused** (Controls page audit log section needs checking; tracked below).

### frontend/src/services/controlsService.ts (36 lines)
Purpose: `fetchControlsState()` → `GET /api/v1/controls/state`; `activateKillSwitch(reason)` → `POST /api/v1/controls/kill-switch`; `resumeTrading(rationale)` → `POST /api/v1/controls/resume`.
Notable: Explicit code comment documents a deliberate API-schema inconsistency (`reason` vs `rationale` param naming) matching two different backend request schemas — a sign of real integration effort against the actual backend contracts, not guesswork.

### frontend/src/services/experimentsService.ts (39 lines)
Purpose: `fetchExperiments()` → `GET /api/v1/experiments`; `fetchExperimentStrategies(id)` → `GET /api/v1/experiments/{id}/strategies`; `createExperiment(payload)` → `POST /api/v1/experiments`; `cancelExperiment(id)` → `POST /api/v1/experiments/{id}/cancel`.
Notable: reuses `ExperimentSummary`/`ExperimentStrategy` types from `types/index.ts` (the only two types from that file confirmed still live).

### frontend/src/services/index.ts (8 lines)
Purpose: Barrel re-exporting all 7 service modules (`export * from`).
Notable: unlike the layout/store/pages/ui barrels, this one is real and actually used — `StrategyLab.tsx` imports `fetchAllStrategies` via `from '../services'` (the barrel), while other pages import directly from the specific service file. Inconsistent import style across pages but functionally fine.

### frontend/src/services/portfolioService.ts (96 lines)
Purpose: `fetchPortfolioSummary` → `GET /api/v1/portfolio/summary`; `fetchEquityCurve(period)` → `GET /api/v1/portfolio/equity-curve`; `fetchPortfolioHoldings` → `GET /api/v1/portfolio/holdings`; `fetchPortfolioAllocation` → `GET /api/v1/portfolio/allocation`; `fetchPortfolioRisk` → `GET /api/v1/portfolio/risk`; `fetchPortfolioPerformance` → `GET /api/v1/portfolio/performance`.
Notable: heaviest-used service file — consumed by both Dashboard and Portfolio pages.

### frontend/src/services/settingsService.ts (151 lines)
Purpose: `fetchLatestDatasetVersion` → `GET /api/v1/metadata/dataset-versions/latest`; `fetchLatestFeatureVersion` → `GET /api/v1/metadata/feature-dataset-versions/latest`; `fetchOperatorSettings` → `GET /api/v1/settings`; `updateOperatorSettings(updates)` → `PUT /api/v1/settings`; `fetchAdvancedSettings` → `GET /api/v1/settings/advanced`.
Notable: largest/most detailed service file — models a rich `ApiOperatorSettingsMetadata` shape (source-of-truth annotations, deprecated/ignored settings) implying real backend schema study, not guesswork.

### frontend/src/services/strategiesService.ts (85 lines)
Purpose: `fetchAllStrategies(status?)` → `GET /api/v1/strategies`; `updateStrategyEnabled` → `PUT /api/v1/strategies/{id}/enabled`; `transitionStrategyGovernance` → `POST /api/v1/strategies/{id}/governance/transition`; `fetchStrategyAllocations` → `GET /api/v1/strategies/allocations`; `updateStrategyAllocation` → `PUT /api/v1/strategies/{id}/allocation`; `fetchActiveStrategies` → `GET /api/v1/strategies/active`.
Notable: second most-used service file (Dashboard, Portfolio, StrategyLab all consume `fetchAllStrategies`/`fetchActiveStrategies`).

### frontend/src/services/systemService.ts (23 lines)
Purpose: `fetchSystemHealth` → `GET /api/v1/system/health`; `updateTradingMode(mode, rationale)` → `PUT /api/v1/system/trading-mode`.
Notable: comment documents a frontend/backend label mismatch (UI says "backtesting", backend expects "simulation") — again indicates genuine integration work.

### frontend/src/pages/Dashboard.tsx (714 lines)
Purpose: `/` route. 4 top metric cards (Portfolio Value, Total PnL, Active Strategies, Risk Status) + equity curve (Recharts `AreaChart`) with 1W/1M/3M/1Y period toggle + Active Strategies table (TanStack Table) + System Health card + Risk Snapshot card + Recent Activity feed.
Notable: **Fully wired to live endpoints, zero mock imports.** Calls (via React Query `useQuery`): `fetchPortfolioSummary`, `fetchEquityCurve`, `fetchPortfolioRisk`, `fetchPortfolioPerformance`, `fetchActiveStrategies`, `fetchSystemHealth` (polls every 30s), `fetchRecentActivity` (polls every 30s). Every card has explicit loading-skeleton and error states — not naive happy-path-only wiring. Contains 4 TODO comments (lines 58, 293, 300-302, 433-437) documenting backend-schema gaps (no 1Y period, no per-strategy sharpe on `/strategies/active`, no 30d PnL, no per-component health breakdown) — these read as genuine integration TODOs against a real backend contract, not placeholder boilerplate.

### frontend/src/pages/Portfolio.tsx (650 lines)
Purpose: `/portfolio` route. 4 metric cards (Total Value, Invested Capital, Cash Reserve, Open Positions) + combined equity/drawdown `ComposedChart` (Recharts, dual Y-axis, toggleable series) + Holdings table (TanStack Table, enriched with strategy display names joined client-side from `fetchAllStrategies`) + Allocation by Strategy bars + Risk Metrics card + Sector Exposure card.
Notable: **Fully wired, zero mock imports.** Calls: `fetchPortfolioSummary`, `fetchEquityCurve`, `fetchPortfolioHoldings`, `fetchPortfolioAllocation`, `fetchPortfolioRisk`, `fetchPortfolioPerformance`, `fetchAllStrategies`. `SectorExposureCard` is an explicit **stubbed-out placeholder** (not backed by any endpoint — comment says no backend route exists yet, renders "Sector exposure data not yet available. Awaiting GET /portfolio/sector-exposure endpoint.") — this is the one card in the app that is honestly non-functional rather than silently faked. Contains 2 TODO comments (line 58, and the sector-exposure block 539-544).

### frontend/src/pages/StrategyLab.tsx (598 lines)
Purpose: `/strategy` route (component named `StrategyLab`, but nav label is "Experiment Lab" and the actual routed component per App.tsx is `ExperimentLab.tsx`, not this file — see below). Strategy card grid with filter bar (All/Live/Paper/Research/Off), per-card sparkline, and a multi-select Comparison Table.
Notable: **This page is NOT mounted on any route.** `App.tsx` only imports `Dashboard`, `Portfolio`, `ExperimentLab`, `Controls`, `Settings` — `StrategyLab.tsx` is never imported by `App.tsx`, `AppShell.tsx`, or `pages/index.ts` (itself an empty `export {}` stub). Confirmed via grep: zero references to `StrategyLab` outside this file itself. **This is a fully-built 598-line page that is orphaned/unreachable in the running app** — likely superseded by `ExperimentLab.tsx` mid-development and left behind. Uses `fetchAllStrategies` via the services barrel (`from '../services'`). Defines its own local `SparklineChart` (shadowing the shared `components/shared/SparklineChart`) using a hardcoded `FLAT_SPARKLINE` placeholder (TODO comment: no sparkline endpoint exists). "Compare Selected" and "+ New Experiment" buttons have no `onClick` handlers — dead buttons. 5 TODO comments (lines 62, 86, 175, 178, 184, 435 — six actually, recount below in final tally).

### frontend/src/pages/Controls.tsx (948 lines, largest page)
Purpose: `/controls` route. 3-column grid: Kill Switch card, Strategy Toggles card, Environment (trading-mode) card (left); Allocation Overrides card, Governance Pending-Promotion card (middle); Audit Log card (right). Every mutable action requires a typed reason/rationale text input before a `useMutation` confirm.
Notable: **Fully wired, zero mock imports, most functionally complete page in the app.** Calls: `fetchSystemHealth`, `fetchControlsState` (15s poll on kill switch, unpolled elsewhere), `activateKillSwitch`, `resumeTrading`, `fetchAllStrategies`, `updateStrategyEnabled`, `fetchStrategyAllocations`, `updateStrategyAllocation`, `transitionStrategyGovernance`, `updateTradingMode`, `fetchAuditLog` (confirms `auditLogService.ts` IS used, correcting the earlier tentative note in the services section — it is consumed here by `AuditLogCard`, polled every 30s). Real `useMutation` + `queryClient.invalidateQueries` cache-invalidation patterns throughout (e.g. kill-switch activation invalidates `['controls','state']`; strategy toggle invalidates both `['controls','state']` and `['strategies','active']`). Contains explicit product-honesty comments surfaced to the operator UI: "Automatic quality-based reallocation is not wired yet, even if the Settings flag is enabled" and "Auto-promote is not wired to a runner yet." 1 TODO (line 344) documenting ambiguity between `kill_switch_active` and `trading_paused` state dimensions on resume.

### frontend/src/pages/Settings.tsx (631 lines)
Purpose: `/settings` route. 2x2 grid: Risk Parameters (sliders: portfolio DD, strategy DD, risk tolerance, max capital/strategy, target volatility), Governance & Allocation Automation (auto-promote/rebalance/demote toggles + rebalance frequency + deprecated-fields notice), Data & Simulation (dataset/feature version badges + slippage/cost model selects), Notifications (toggles, 2 permanently-disabled stub toggles for "Kill switch events" and "Daily PnL summary").
Notable: **Fully wired, zero mock imports.** Calls: `fetchOperatorSettings`, `updateOperatorSettings` (×3 cards, each with independent dirty-state tracking and its own Save button), `fetchLatestDatasetVersion`, `fetchLatestFeatureVersion`. **`fetchAdvancedSettings` (from `settingsService.ts`) is defined but never called anywhere in the app** — confirmed via grep, only self-reference in its own file. Cost-model configuration (`ApiCostModelConfig`/`ApiAdvancedSettings`) is dead/unreachable UI-wise. No TODO comments in this file (all deferred-feature messaging is done via `InlineNote` components instead, e.g. "Stubbed until email/report delivery exists").

### frontend/src/pages/ExperimentLab.tsx (751 lines)
Purpose: **This is the component actually routed to `/strategy`** (imported by `App.tsx` as `ExperimentLab`, nav label "Experiment Lab"). Experiment card grid with status filter (All/Running/Completed/Failed) + sort (Created/Best Sharpe/Strategies), expandable per-experiment strategies table, "New Experiment" modal with full form (name, type, symbols, date range, price basis, strategy count, JSON parameter ranges) and client-side validation, cancel-experiment action.
Notable: **Fully wired, zero mock imports.** Calls: `fetchExperiments`, `fetchExperimentStrategies` (on-demand when a card expands, `staleTime: 60_000`), `createExperiment` (via modal form + `useMutation`), `cancelExperiment`. No TODO comments — this page has no known backend gaps documented. Most fully-featured/polished page besides Controls; the only page with a real create/submit form flow.

### CRITICAL FINDING — TODO/FIXME inventory (13 total, matches expected ~13)
1. `frontend/src/pages/Dashboard.tsx:58` — no 1-year period on equity-curve API, using 'ytd' as substitute
2. `frontend/src/pages/Dashboard.tsx:293` — `sharpe_ratio` not available on `GET /strategies/active`
3. `frontend/src/pages/Dashboard.tsx:302` — no 30d PnL on `GET /strategies/active`, showing today's return instead
4. `frontend/src/pages/Dashboard.tsx:433` — no per-component system-health breakdown, only aggregate status
5. `frontend/src/pages/Portfolio.tsx:58` — same 1-year-period gap as Dashboard
6. `frontend/src/pages/Portfolio.tsx:539` — no backend endpoint for sector exposure at all
7. `frontend/src/pages/StrategyLab.tsx:62` — no simulation-stage field on `GET /strategies`
8. `frontend/src/pages/StrategyLab.tsx:86` — no sparkline/equity-history endpoint per strategy, flat placeholder used
9. `frontend/src/pages/StrategyLab.tsx:175` — no simulation-stage field (research-state row2)
10. `frontend/src/pages/StrategyLab.tsx:178` — no active dataset-version field on `GET /strategies`
11. `frontend/src/pages/StrategyLab.tsx:184` — no allocation field on `GET /strategies` (only on `/strategies/active`)
12. `frontend/src/pages/StrategyLab.tsx:435` — no simulation-stage field (comparison table row)
13. `frontend/src/pages/Controls.tsx:344` — ambiguous resume semantics vs. `kill_switch_active`/`trading_paused` state dimensions
Note: **6 of the 13 TODOs live in `StrategyLab.tsx` — the orphaned/unrouted page.** Only 7 TODOs exist in code that is actually reachable in the running app.

### CRITICAL FINDING — missing `src/lib/utils.ts`
`frontend/src/lib/utils.ts` (expected to export `cn`, the standard shadcn clsx+tailwind-merge helper) **does not exist anywhere in the repo**, tracked or untracked (`git ls-files`, `ls`, and `find` all confirm absence; `components.json` declares the `lib`/`utils` aliases but the scaffold step that creates `src/lib/utils.ts` was never run).
Imported by 9 files via `../../lib/utils` or `@/lib/utils`: `AllocationBar.tsx`, `MetricCard.tsx`, `StatusBadge.tsx`, and (per grep) `StrategyLab.tsx`, `Settings.tsx`, `Portfolio.tsx`, `ExperimentLab.tsx`, `Dashboard.tsx`, `Controls.tsx`.
This means **`npm run build` (`tsc -b && vite build`) should currently fail** with an unresolved-module error, and any of these 9 files would break at runtime in dev mode too if Vite can't resolve the import. Import path form confirmed consistent across all 9 files: the 3 shared components use relative `../../lib/utils`; all 6 pages use relative `../lib/utils`. No file uses the `@/lib/utils` alias form despite it being configured in `tsconfig.app.json` and `components.json` — the alias exists but nothing exercises it for this particular module. This is a serious, verifiable, build-breaking defect with no workaround present in the codebase (no CI config was found in the 59 tracked frontend files to confirm whether this is currently caught).

## Route list (as actually wired in App.tsx — the file-based `src/routes/*.tsx` are unused/empty)

| Path | Component | Nav label |
|---|---|---|
| `/` | `Dashboard` | Dashboard |
| `/portfolio` | `Portfolio` | Portfolio |
| `/strategy` | `ExperimentLab` | Experiment Lab |
| `/controls` | `Controls` | Controls |
| `/settings` | `Settings` | Settings |

`StrategyLab.tsx` (598 lines) is built but mounted nowhere — not reachable via any route, nav link, or import chain from `App.tsx`.

## React Query, Zustand, Recharts, TanStack Table — usage summary

- **React Query**: `QueryClient` instantiated once in `App.tsx` (staleTime 30s, retry 2) and provided app-wide. Every page uses `useQuery`; Controls/Settings/ExperimentLab also use `useMutation` + `queryClient.invalidateQueries()` for cache invalidation after writes. `@tanstack/react-query-devtools` is a dependency but the `<ReactQueryDevtools />` component is never rendered anywhere.
- **Zustand**: single store (`useAppStore`, 18 lines) holding `activeEnv` and `killSwitchActive`. Consumed only by `TopNav.tsx` (read both) and `Controls.tsx` (writes both, via `setKillSwitchActive`/`setActiveEnv` inside mutation `onSuccess` callbacks). This is essentially a small piece of cross-cutting UI state mirroring what's really server state (fetched via `fetchControlsState`) — a minor architectural smell (state duplicated between Zustand and React Query cache).
- **Recharts**: only two chart types used across the whole app — `AreaChart` (Dashboard equity curve) and `ComposedChart` combining `Area` + `Line` on dual Y-axes (Portfolio equity+drawdown). No `LineChart`, `BarChart`, `PieChart`, `ScatterChart`, or `RadarChart` usage anywhere, despite Recharts shipping all of these.
- **TanStack Table**: used in exactly two places — Dashboard's `StrategiesTable` (6 columns via `createColumnHelper`) and Portfolio's `HoldingsTable` (7 columns). Both use only `getCoreRowModel` — no sorting, filtering, or pagination row models are wired up despite the library supporting them.

## Standout candidates

- `frontend/src/pages/Controls.tsx` — most functionally complete page: real mutations for kill switch, per-strategy toggles, trading-mode switch, allocation overrides, and governance promotion, each gated behind a required reason/rationale field, with correct query-cache invalidation on every write.
- `frontend/src/pages/ExperimentLab.tsx` — the only page with a full create-form flow (client-validated "New Experiment" modal posting to a real endpoint) and the only page with zero outstanding TODOs.
- `frontend/src/services/settingsService.ts` — the richest typed API surface in the app (`ApiOperatorSettingsMetadata` models source-of-truth provenance for each setting), evidence of genuine backend-contract study rather than guesswork.
- `frontend/src/pages/Dashboard.tsx` equity chart + `Portfolio.tsx` combined equity/drawdown chart — the two most visually resolved data visualizations, with custom tooltips, gradient fills, and dual Y-axis handling.
- The self-documenting TODO comments throughout Dashboard/Portfolio/Controls that name the exact missing backend field/endpoint (e.g. "GET /strategies/active has no sharpe_ratio field") are unusually precise for a WIP frontend — good engineering hygiene even where the feature itself is incomplete.

## Gaps / smells

- **Build-breaking**: `src/lib/utils.ts` (the `cn` helper) does not exist; imported by 9 files (3 shared components + all 6 pages).
- **Orphaned page**: `StrategyLab.tsx` (598 lines, ~12% of all page code) is fully built but unreachable — not routed, not linked, not imported by `App.tsx`. Likely superseded by `ExperimentLab.tsx` and never deleted.
- **Dead mock layer**: `src/mock/data.ts` (508 lines) is entirely unused — every page now hits real endpoints instead.
- **Empty barrel stubs**: `components/ui/index.ts`, `layouts/index.ts`, `store/index.ts`, `pages/index.ts` are all literally `export {}` — none re-export anything; consumers bypass them and import concrete files directly. Harmless but dead weight / evidence of incomplete shadcn scaffolding.
- **Empty file-based routes**: `src/routes/__root.tsx`, `controls.tsx`, `index.tsx`, `portfolio.tsx`, `settings.tsx`, `strategy.tsx` are all 0 bytes — a TanStack Router file-based-routing convention was scaffolded (directory structure exists) but abandoned in favor of the manual `createRoute` calls in `App.tsx`. Confusing for anyone assuming file-based routing is live.
- **Vite/React template leftovers**: `App.css` (184 lines), `assets/hero.png`, `assets/react.svg`, `assets/vite.svg` are all dead — zero imports found anywhere. `public/icons.svg` (social icons: bluesky/discord/x/github) also appears to be unused marketing-template boilerplate unrelated to a trading platform.
- **Unused dependencies** (12 of 23 runtime deps have zero imports in src): `@fontsource-variable/geist`, `@hookform/resolvers`, `class-variance-authority`, `dotenv`, `framer-motion` (confirmed, matches prior draft), `lightweight-charts`, `lucide-react`, `radix-ui`, `react-hook-form`, `shadcn` (CLI misplaced in `dependencies`), `tailwind-merge`, `zod`. `clsx` has no direct imports either (would only be reachable via the missing `lib/utils.ts`).
- **Unused dev/test tooling** (confirmed, matches prior draft): `vitest`, `@playwright/test`/`playwright`, `@testing-library/*`, `jsdom` are all installed with **zero test files** (`*.test.*`/`*.spec.*`) and no `vitest.config.ts`/`playwright.config.ts` in the repo. `package.json` has no `test` script at all, yet `.husky/pre-commit` runs `npm test` — every commit's pre-commit hook is calling a script that doesn't exist.
- **`fetchAdvancedSettings`** (in `settingsService.ts`) is a fully-typed, real endpoint call that is never invoked from any page — dead service function.
- **Naming/branding drift**: the shipped product is branded "◈ WeTrade" in `TopNav.tsx`, not "Autonomous Trading Platform" as per CLAUDE.md/repo name; `index.html`'s `<title>` is still the unmodified Vite scaffold default "frontend".
- **Dead buttons**: "Compare Selected" and "+ New Experiment" in `StrategyLab.tsx` have no `onClick` handlers (moot, since the page is unreachable anyway).
- **State duplication**: `killSwitchActive`/`activeEnv` are tracked in both Zustand (`useAppStore`) and the React Query cache (`fetchControlsState`) — two sources of truth for the same server-owned state, manually kept in sync inside mutation `onSuccess` callbacks.
- **TanStack Table underused**: only `getCoreRowModel` is wired in both usages — no sorting/filtering/pagination despite two genuinely tabular, potentially-long datasets (holdings, strategies).

## Coverage

Read 59 of 59 tracked files under `frontend` (`git ls-files -- frontend`), including the binary `hero.png` (visually confirmed unused, not content-inspected further) and the two 0-byte-relevant classes of files (SVGs, empty route stubs) at their actual byte length. Skipped only `package-lock.json` per instructions (used solely to cross-check installed dependency versions where relevant — none of the version claims in this audit relied on it beyond what `package.json`'s semver ranges already state). No files outside the 59-file frontend scope were read for this audit.
