# WeTrade — Claude Code Context

## Project Overview
Autonomous quantitative trading platform. FastAPI backend, React frontend.
Frontend is a **static mockup first** — no API calls yet, all mock data.
Wire up APIs later once all pages and components exist.

## Tech Stack

### Frontend
- React + Vite + TypeScript
- TanStack Router (routing)
- TanStack Query (data fetching — not wired yet)
- Zustand (global state)
- Tailwind CSS
- shadcn/ui (component primitives)
- Recharts (charts)
- TanStack Table (tables)
- Axios + Zod (API layer — not wired yet)
- Framer Motion (animations)

### Backend (separate — do not touch unless asked)
- FastAPI + Uvicorn
- SQLAlchemy + Alembic
- Pydantic + mypy

## Frontend Folder Structure
```
src/
  components/
    ui/              ← shadcn primitives (Button, Badge, Card, etc.)
    shared/          ← our reusable components (MetricCard, StatusBadge, etc.)
  pages/
    Dashboard.tsx
    Portfolio.tsx
    StrategyLab.tsx
    Controls.tsx
    Settings.tsx
  layouts/
    AppShell.tsx     ← persistent wrapper (nav + topbar)
    TopNav.tsx
  store/
    useAppStore.ts   ← Zustand store
  services/          ← empty for now, API services go here later
  types/
    index.ts         ← shared TypeScript types
  lib/
    utils.ts         ← cn() helper and misc utils
  mock/
    data.ts          ← ALL mock data lives here
```

## Design System — Theme Tokens (defined in index.css)
```
--bg: #070B0F
--surface: #0D1117
--surface2: #111820
--border: #1C2532
--border2: #243040
--text: #C9D1D9
--text2: #8B949E
--text3: #4A5568
--accent: #00E5A0        ← primary green
--accent2: #00B37D
--red: #FF4D6D
--yellow: #E8A838
--blue: #3B9EFF
--purple: #9B72FF
--font-mono: 'IBM Plex Mono', monospace
--font-sans: 'IBM Plex Sans', sans-serif
```

## UI Reference
The file `trading_platform_screens.html` at the project root is the
full visual reference. Match it exactly for layout, colors, spacing,
and component structure. Do not invent new layouts — use that file as
the source of truth.

## Key Patterns

### Mock data
All mock data lives in `src/mock/data.ts`. Pages import from there.
Never hardcode data inside a component or page file.

### Component naming
- Shared reusable components: PascalCase in `src/components/shared/`
- Page files: PascalCase in `src/pages/`
- shadcn primitives: live in `src/components/ui/` — do not edit these

### Tailwind + CSS variables
Use Tailwind for spacing and layout. Use CSS variables (via inline style
or arbitrary Tailwind values like `bg-[var(--surface)]`) for colors so
the theme stays in one place.

### TypeScript
Strict mode. All props typed. No `any`. Types defined in `src/types/index.ts`.

### Zustand store
`useAppStore` holds:
- `activeEnv: 'simulation' | 'paper' | 'live'`
- `killSwitchActive: boolean`
- `viewMode: 'basic' | 'pro'` (reserved for later)

### TanStack Router
Each page is a route. Route definitions live in `src/main.tsx` or a
dedicated `src/router.ts`. Use `<Link>` for nav, not `<a>`.

## Pages Summary

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/` | Portfolio value, equity curve, active strategies, system health, activity feed |
| Portfolio | `/portfolio` | Holdings table, drawdown chart, allocation bars, risk metrics, sector exposure |
| Strategy Lab | `/strategy` | Strategy cards grid, filter bar, comparison table at bottom |
| Controls | `/controls` | Kill switch, strategy toggles, allocation overrides, alert thresholds, audit log |
| Settings | `/settings` | Risk sliders, governance config, data version info, notification toggles |

## What NOT to do
- Do not wire up real API calls — use mock data only until told otherwise
- Do not install libraries not listed in the stack above without asking
- Do not edit files in `src/components/ui/` (shadcn) unless explicitly asked
- Do not create new pages or routes not listed above
- Do not change the color tokens — match the reference HTML exactly
