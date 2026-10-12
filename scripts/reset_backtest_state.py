"""Reset all backtest state tables for a clean run.

Thin wrapper over ``WorkerStateResetService`` (the worker's job runner uses the same list).
Pulled dataset registrations are kept; pass ``--all`` to drop those too.
"""

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.worker_state_reset_service import (
    WorkerStateResetService,
)

url = os.environ.get("DATABASE_URL", "postgresql+psycopg://ratp:ratp_password@localhost:5433/ratp")
engine = create_engine(url)

with engine.connect() as conn:
    result = conn.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    )
    existing = {row[0] for row in result}

print("Tables in DB:")
for t in sorted(existing):
    print(f"  {t}")

print()
print("Clearing tables:")
with Session(engine) as session:
    outcome = WorkerStateResetService(session).reset(keep_pulled_datasets="--all" not in sys.argv)
    for table, count in outcome.deleted.items():
        print(f"  {table}: {count} rows deleted")
    if outcome.kept_pulled_dataset_versions:
        print(f"  dataset_versions: kept {outcome.kept_pulled_dataset_versions} pulled version(s)")
    session.commit()

print()
print("Done.")
