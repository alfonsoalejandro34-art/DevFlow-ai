"""Agent mode and self-correcting attempts.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_requests") as batch:
        batch.add_column(sa.Column("mode", sa.String(10), nullable=False, server_default="text"))
        batch.add_column(sa.Column("attempts", sa.JSON(), nullable=False, server_default="[]"))
    with op.batch_alter_table("patch_proposals") as batch:
        batch.add_column(sa.Column("verification_run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
        batch.create_foreign_key(
            "fk_patch_proposals_verification_run_id",
            "test_runs",
            ["verification_run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("patch_proposals") as batch:
        batch.drop_constraint("fk_patch_proposals_verification_run_id", type_="foreignkey")
        batch.drop_column("attempt")
        batch.drop_column("verification_run_id")
    with op.batch_alter_table("analysis_requests") as batch:
        batch.drop_column("attempts")
        batch.drop_column("mode")
