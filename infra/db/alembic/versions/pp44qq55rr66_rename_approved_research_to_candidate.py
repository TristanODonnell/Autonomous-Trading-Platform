"""rename approved_research governance state to candidate

Research survivors now land in the ``candidate`` state (the bench) instead of
``approved_research``. Renames the stored string values and the
``governance_state_enum`` label used by run_manifests.

Audit/history tables (governance audit, audit logs) are intentionally left as
written — they record what the state was called at the time.

Revision ID: pp44qq55rr66
Revises: oo33pp44qq55
Create Date: 2026-09-26

"""

from __future__ import annotations

from alembic import op

revision = "pp44qq55rr66"
down_revision = "oo33pp44qq55"
branch_labels = None
depends_on = None

_OLD = "approved_research"
_NEW = "candidate"

# (table, column) pairs that store the governance state as a plain string.
_STRING_COLUMNS = (
    ("strategy_governance", "current_state"),
    ("promotion_rules", "from_status"),
    ("promotion_rules", "to_status"),
    ("capital_allocation_policies", "approval_status"),
)


def _rename_strings(old: str, new: str) -> None:
    for table, column in _STRING_COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = '{new}' WHERE {column} = '{old}'")


def _rename_enum_label(old: str, new: str) -> None:
    # Guarded so fresh databases (or ones already migrated by hand) don't fail.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                WHERE t.typname = 'governance_state_enum' AND e.enumlabel = '{old}'
            ) THEN
                ALTER TYPE governance_state_enum RENAME VALUE '{old}' TO '{new}';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    _rename_strings(_OLD, _NEW)
    _rename_enum_label("APPROVED_RESEARCH", "CANDIDATE")


def downgrade() -> None:
    _rename_enum_label("CANDIDATE", "APPROVED_RESEARCH")
    _rename_strings(_NEW, _OLD)
