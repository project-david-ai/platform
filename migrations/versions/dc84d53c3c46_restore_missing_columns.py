"""restore missing columns

Revision ID: dc84d53c3c46
Revises: ffbf9622eece
Create Date: 2025-08-20 11:46:00.251355
"""

from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

# --- Try to import your DDL safety helpers; fall back to local shims ---
try:
    from migrations.utils.safe_ddl import (
        add_column_if_missing,
        column_exists,
        drop_column_if_exists,
        rename_column_if_exists,
        safe_alter_column,
    )
except Exception:

    def _insp():
        bind = op.get_bind()
        return sa.inspect(bind)

    def column_exists(table: str, col: str) -> bool:
        insp = _insp()
        return col in {c["name"] for c in insp.get_columns(table)}

    def add_column_if_missing(table: str, column: sa.Column) -> None:
        if not column_exists(table, column.name):
            op.add_column(table, column)

    def drop_column_if_exists(table: str, col_name: str) -> None:
        if column_exists(table, col_name):
            op.drop_column(table, col_name)

    def safe_alter_column(table: str, col_name: str, **kw) -> None:
        op.alter_column(table, col_name, **kw)

    def rename_column_if_exists(
        table: str,
        old_name: str,
        new_name: str,
        existing_type: Optional[sa.types.TypeEngine] = None,
        existing_nullable: Optional[bool] = None,
    ) -> None:
        if column_exists(table, old_name) and not column_exists(table, new_name):
            op.alter_column(
                table,
                old_name,
                new_column_name=new_name,
                existing_type=existing_type,
                existing_nullable=existing_nullable,
            )


# Alembic identifiers
revision: str = "dc84d53c3c46"
down_revision: Union[str, None] = "ffbf9622eece"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bigint_to_int_swap(table: str, base_col: str) -> None:
    """
    Safely convert BIGINT column to INT using temp column + backfill + swap.
    NOTE: This can truncate values > INT range; ensure data is within bounds.
    """
    temp = f"{base_col}_i"

    # 1) Add temp INT column if missing
    add_column_if_missing(table, sa.Column(temp, sa.Integer(), nullable=True))

    # 2) Backfill 1:1 (NULL-safe)
    conn = op.get_bind()
    conn.exec_driver_sql(f"UPDATE {table} SET {temp} = {base_col}")

    # 3) Drop old BIGINT column
    drop_column_if_exists(table, base_col)

    # 4) Rename temp → original name
    rename_column_if_exists(
        table,
        old_name=temp,
        new_name=base_col,
        existing_type=sa.Integer(),
        existing_nullable=True,
    )


def _int_to_bigint_swap(table: str, base_col: str) -> None:
    """
    Downgrade path: convert INT back to BIGINT via temp column + backfill + swap.
    """
    temp = f"{base_col}_bi"

    # 1) Add temp BIGINT column if missing
    add_column_if_missing(table, sa.Column(temp, sa.BigInteger(), nullable=True))

    # 2) Backfill 1:1 (NULL-safe)
    conn = op.get_bind()
    conn.exec_driver_sql(f"UPDATE {table} SET {temp} = {base_col}")

    # 3) Drop old INT column
    drop_column_if_exists(table, base_col)

    # 4) Rename temp → original name
    rename_column_if_exists(
        table,
        old_name=temp,
        new_name=base_col,
        existing_type=sa.BigInteger(),
        existing_nullable=True,
    )


def upgrade() -> None:
    """Upgrade schema."""
    # Keep messages.function_call NOT NULL
    safe_alter_column(
        "messages", "function_call", existing_type=sa.Text(), nullable=False
    )

    # Convert runs.* from BIGINT → INT (epoch seconds stay numeric; just width change)
    for col in ("cancelled_at", "completed_at", "failed_at", "started_at"):
        if column_exists("runs", col):
            _bigint_to_int_swap("runs", col)


def downgrade() -> None:
    """Downgrade schema."""
    # Revert messages.function_call to nullable to match prior state
    safe_alter_column(
        "messages", "function_call", existing_type=sa.Text(), nullable=True
    )

    # Convert runs.* from INT → BIGINT
    for col in ("cancelled_at", "completed_at", "failed_at", "started_at"):
        if column_exists("runs", col):
            _int_to_bigint_swap("runs", col)
