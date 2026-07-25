# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Autonomous quantitative trading platform. FastAPI backend + React/TypeScript frontend. The frontend is wired to real backend endpoints (not mock data) but is uneven in places — some pages have drifted from recent backend changes, and frontend test coverage is thin. Verify current behavior against the backend before assuming a page reflects the latest API shape.

## Commands

### Backend

```bash
# Install (from repo root, with venv active)
pip install -r requirements.txt
pip install -e .

# Run all tests
python -m pytest

# Run a single test file
python -m pytest tests/path/to/test_file.py

# Run only integration tests (require Postgres)
python -m pytest -m integration

# Skip slow/external tests
python -m pytest -m "not integration and not external and not alpaca"

# Lint + format
ruff check src/ tests/
ruff format src/ tests/

# Type check
mypy src/

# Run pre-commit (ruff + mypy) on staged files
pre-commit run

# DB migrations (from repo root)
alembic -c infra/db/alembic.ini upgrade head
alembic -c infra/db/alembic.ini revision --autogenerate -m "description"

# Dev JWT for manual API testing
python scripts/generate_dev_jwt.py

# Start infrastructure (Postgres, Airflow, Grafana, OTel)
docker compose up -d
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # Vite dev server
npm run build      # Production build
npm run lint       # ESLint
```

## Backend Architecture

The backend follows a strict layered architecture. **Always flow inward: interfaces → application → domain → storage → contracts.**

```
contracts/     ← Pydantic models (immutable data shapes; no business logic)
storage/       ← Persistence: PostgreSQL (SQLAlchemy) + Parquet datasets
application/   ← Service layer: orchestrates domain logic, no I/O
interfaces/    ← REST API (FastAPI), CLI
```

**Key directories:**

| Path | Purpose |
|------|---------|
| `src/autonomous_trading_platform/contracts/` | Pydantic data contracts for every domain (market, execution, governance, runtime) |
| `src/autonomous_trading_platform/storage/sor/` | System-of-Record: SQLAlchemy ORM models + UnitOfWork repositories |
| `src/autonomous_trading_platform/storage/parquet/` | Versioned Parquet datasets for market bars and features |
| `src/autonomous_trading_platform/application/services/` | 30+ service classes: portfolio analytics, strategy management, system health, controls |
| `src/autonomous_trading_platform/interfaces/rest/` | FastAPI app factory, middleware stack, route modules |
| `src/autonomous_trading_platform/interfaces/rest/routes/` | One file per API domain: portfolio, strategies, controls, audit_log, system, settings |
| `src/autonomous_trading_platform/strategy/` | BaseStrategy + concrete implementations (Momentum, MeanReversion, MACD, etc.) |
| `src/autonomous_trading_platform/execution/` | Order lifecycle: broker client, fill accounting, position/cash ledgers, reconciliation |
| `src/autonomous_trading_platform/safety/` | Multi-layer trading gates, kill switch, environment isolation (paper vs live) |
| `src/autonomous_trading_platform/universe/` | Universe snapshot versioning, symbol lifecycle, survivorship bias elimination |
| `src/autonomous_trading_platform/research/` | Experiment pipeline: simulation, metrics computation, artifact persistence |
| `src/autonomous_trading_platform/ingestion/` | Alpaca market data + corporate actions ingestion pipeline |
| `src/autonomous_trading_platform/scheduler/airflow/` | Airflow DAGs for ingestion, backtesting, universe governance |
| `src/autonomous_trading_platform/observability/` | OpenTelemetry instrumentation |

**FastAPI middleware order** (defined in `interfaces/rest/app.py`):
`RequestID → Logging → JWT → Deprecation`

**Database:** PostgreSQL 16 on port 5433 (Docker). Test overrides use SQLite in-memory. Alembic migrations live in `infra/db/alembic/versions/` (65+ versions).

**Key patterns:**
- All domain data shapes are Pydantic contracts in `contracts/` — never define data shapes in services or routes
- Repositories follow UnitOfWork pattern; never call ORM directly from services
- Parquet datasets are versioned (`storage/parquet/versioning.py`) — do not write raw files
- `NO_LIVE_TRADING=true` must be set in all non-live environments; the safety layer enforces this at runtime

## Test Setup

- Framework: pytest + pytest-asyncio, SQLite in-memory for unit/service tests
- Markers: `integration` (requires Postgres), `external` (network), `alpaca`, `smoke`, `paper_runtime`
- Shared fixtures in `tests/conftest.py` and `tests/utilities/` (cycle fixtures for ingestion, paper trading, research pipelines)
- Test env vars auto-set in conftest: `APP_ENV=test`, `DATABASE_URL=sqlite:///:memory:`, `NO_LIVE_TRADING=true`

## Frontend

### Stack
React 19 + Vite + TypeScript (strict), TanStack Router + Query, Zustand, Tailwind CSS, shadcn/ui, Recharts, TanStack Table, Framer Motion.

### Key Rules
- The frontend calls real backend endpoints — `frontend/src/mock/data.ts` is a leftover from an earlier mock-only phase and is no longer imported anywhere; don't add new dependencies on it
- Do not edit `frontend/src/components/ui/` (shadcn primitives)
- Colors: use CSS variables (`bg-[var(--surface)]`), not hardcoded hex
- `docs/design-reference/trading_platform_screens.html` is the original visual reference — useful for design intent, but the live frontend may have since drifted from it

### Design Tokens (defined in `frontend/src/index.css`)
```
--bg: #070B0F       --surface: #0D1117    --surface2: #111820
--border: #1C2532   --text: #C9D1D9       --text2: #8B949E
--accent: #00E5A0   --red: #FF4D6D        --yellow: #E8A838
--blue: #3B9EFF     --purple: #9B72FF
```

### Pages
| Route | Page | Description |
|-------|------|-------------|
| `/` | Dashboard | Portfolio value, equity curve, active strategies, system health, activity feed |
| `/portfolio` | Portfolio | Holdings table, drawdown chart, allocation bars, risk metrics |
| `/strategy` | Strategy Lab | Strategy cards grid, filter bar, comparison table |
| `/controls` | Controls | Kill switch, strategy toggles, allocation overrides, audit log |
| `/settings` | Settings | Risk sliders, governance config, notification toggles |

## Infrastructure

Docker Compose services (all local dev):
- **postgres** port 5433 — system-of-record DB
- **airflow-webserver** port 8080 — DAG orchestration UI
- **lgtm** port 3000 — Grafana observability (metrics + traces)
- **otel-collector** ports 4317/4318 — OpenTelemetry ingestion

## Docs

Canonical architecture docs live in `docs/`. Key references:
- `docs/architecture/layering.md` — layer boundaries and domain ownership
- `docs/architecture/safety-doctrine.md` — safety invariants (read before touching safety/ or execution/)
- `docs/architecture/invariants.md` — hard invariants that must not be violated
- `docs/backend/storage-lineage/` — Parquet dataset versioning, Postgres SoR design
- `docs/archived-docs/architecture/v1-boundaries.md` — historical v1-only system boundaries (superseded by multi-strategy/portfolio-governance work; kept for context only)
