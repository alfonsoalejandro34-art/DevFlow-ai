"""Reconcile patches left in 'retesting' after their retest run finished.

Before 0.1.0 shipped, a patch's verdict was only recorded when the patch was read, so
metrics could see stale 'retesting' rows. Verdicts are now written when the retest run
finishes; this migration fixes rows created earlier.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RECONCILE = (
    "UPDATE patch_proposals SET status = 'retest_passed' WHERE status = 'retesting' "
    "AND retest_run_id IN (SELECT id FROM test_runs WHERE status = 'passed')",
    "UPDATE patch_proposals SET status = 'retest_failed' WHERE status = 'retesting' "
    "AND retest_run_id IN (SELECT id FROM test_runs WHERE status IN ('failed', 'timeout'))",
    "UPDATE patch_proposals SET status = 'approved' WHERE status = 'retesting' "
    "AND retest_run_id IN (SELECT id FROM test_runs WHERE status IN ('error', 'cancelled'))",
)


def upgrade() -> None:
    for statement in _RECONCILE:
        op.execute(statement)


def downgrade() -> None:
    """Data-only migration; nothing to undo."""
