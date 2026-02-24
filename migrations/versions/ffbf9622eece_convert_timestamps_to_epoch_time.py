"""convert timestamps to epoch time

Revision ID: ffbf9622eece
Revises: c8adf7bf8fa8
Create Date: 2025-08-18 16:30:56.469086
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# ✅ import the shared safe DDL helpers (no local shadowing!)
from migrations.utils.safe_ddl import (add_column_if_missing,
                                       drop_column_if_exists, has_column,
                                       has_table, safe_alter_column)

# revision identifiers, used by Alembic.
revision: str = "ffbf9622eece"
down_revision: Union[str, None] = "c8adf7bf8fa8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (inspection + conditional type change)
# ─────────────────────────────────────────────────────────────────────────────


def _column_is_integer(table: str, col: str) -> bool:
    """Return True if the DB column is already an integer-like type."""
    bind = op.get_bind()
    insp = inspect(bind)
    if not insp.has_table(table):
        return False
    for c in insp.get_columns(table):
        if c["name"] == col:
            # Compare by SQLAlchemy type class name to avoid dialect quirks
            tname = type(c["type"]).__name__.lower()
            # Matches Integer, BIGINT, INTEGER, etc.
            return "int" in tname and "interval" not in tname
    return False


def _change_to_integer_if_needed(table: str, col: str, nullable: bool = True) -> None:
    """
    Convert a column to Integer only if it exists and is not already integer-like.
    Uses safe_alter_column underneath.
    """
    if not has_table(table) or not has_column(table, col):
        return
    if _column_is_integer(table, col):
        print(f"[alembic.safe_ddl] ⏭️  Skip alter: {table}.{col} already integer")
        return
    # We don't pass existing_type to avoid mismatches across environments.
    # MySQL impl can infer current type; if you prefer, you can inspect and pass it.
    safe_alter_column(table, col, type_=sa.Integer(), nullable=nullable)


# ─────────────────────────────────────────────────────────────────────────────
# Upgrade / Downgrade
# ─────────────────────────────────────────────────────────────────────────────


def upgrade() -> None:
    """Upgrade schema."""
    # messages.function_call → NOT NULL (only if messages table exists)
    safe_alter_column(
        "messages", "function_call", existing_type=sa.Text(), nullable=False
    )

    # runs.* timestamps → Integer epoch seconds (conditionally)
    _change_to_integer_if_needed("runs", "cancelled_at", nullable=True)
    _change_to_integer_if_needed("runs", "completed_at", nullable=True)
    _change_to_integer_if_needed("runs", "failed_at", nullable=True)
    _change_to_integer_if_needed("runs", "started_at", nullable=True)

    # These were dropped in this revision originally. Keep the safe pattern:
    # (If they don't exist, drop_column_if_exists no-ops.)
    drop_column_if_exists("runs", "temperature")
    drop_column_if_exists("runs", "top_p")


def downgrade() -> None:
    """Downgrade schema."""
    # Re-add dropped columns if missing
    add_column_if_missing("runs", sa.Column("temperature", sa.Integer(), nullable=True))
    add_column_if_missing("runs", sa.Column("top_p", sa.Integer(), nullable=True))

    # Convert back to DATETIME only if the columns exist and aren't already datetime.
    # For simplicity, we always attempt the alter when column exists; most MySQL servers
    # will no-op if already DATETIME, otherwise they'll convert int→datetime (may zero).
    if has_table("runs"):
        if has_column("runs", "started_at"):
            safe_alter_column("runs", "started_at", type_=sa.DateTime(), nullable=True)
        if has_column("runs", "failed_at"):
            safe_alter_column("runs", "failed_at", type_=sa.DateTime(), nullable=True)
        if has_column("runs", "completed_at"):
            safe_alter_column(
                "runs", "completed_at", type_=sa.DateTime(), nullable=True
            )
        if has_column("runs", "cancelled_at"):
            safe_alter_column(
                "runs", "cancelled_at", type_=sa.DateTime(), nullable=True
            )

    # messages.function_call → back to nullable if table exists
    safe_alter_column(
        "messages", "function_call", existing_type=sa.Text(), nullable=True
    )
