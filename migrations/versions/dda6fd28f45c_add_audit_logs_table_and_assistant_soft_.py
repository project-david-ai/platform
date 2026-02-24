"""Add audit_logs table and assistant soft_delete

Revision ID: dda6fd28f45c
Revises: b697008df93a
Create Date: 2026-02-14 00:52:59.555724

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# Import the safe DDL helpers
from migrations.utils.safe_ddl import (add_column_if_missing,
                                       drop_column_if_exists, has_table,
                                       safe_alter_column)

# revision identifiers, used by Alembic.
revision: str = "dda6fd28f45c"
down_revision: Union[str, None] = "b697008df93a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema safely."""

    # 1. Create Audit Logs Table (Idempotent Check)
    if not has_table("audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=True),
            sa.Column(
                "action",
                sa.String(length=32),
                nullable=False,
                comment="e.g. CREATE, UPDATE, DELETE, HARD_DELETE",
            ),
            sa.Column(
                "entity_type",
                sa.String(length=64),
                nullable=False,
                comment="e.g. Assistant, User, Thread",
            ),
            sa.Column("entity_id", sa.String(length=64), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column(
                "details",
                sa.JSON(),
                nullable=True,
                comment="Stores before/after state or reasoning for action",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )

        # Create indexes only if table was just created
        op.create_index(
            op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False
        )
        op.create_index(
            op.f("ix_audit_logs_entity_id"), "audit_logs", ["entity_id"], unique=False
        )
        op.create_index(
            op.f("ix_audit_logs_entity_type"),
            "audit_logs",
            ["entity_type"],
            unique=False,
        )
        op.create_index(op.f("ix_audit_logs_id"), "audit_logs", ["id"], unique=False)
        op.create_index(
            op.f("ix_audit_logs_timestamp"), "audit_logs", ["timestamp"], unique=False
        )
        op.create_index(
            op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False
        )

        print("[Alembic-safeDDL] ✅ Created table: audit_logs")
    else:
        print("[Alembic-safeDDL] ⚠️ Skipped – table already exists: audit_logs")

    # 2. Add deleted_at to Assistants
    add_column_if_missing(
        "assistants",
        sa.Column(
            "deleted_at",
            sa.Integer(),
            nullable=True,
            comment="Unix timestamp of soft-deletion. If present, entity is in 'Recycle Bin'.",
        ),
    )

    # 3. Message Table Updates (Type hardening)
    # Ensure content is not null
    safe_alter_column("messages", "content", existing_type=mysql.TEXT(), nullable=False)

    # Ensure reasoning uses the correct Large Text type
    safe_alter_column(
        "messages",
        "reasoning",
        existing_type=mysql.LONGTEXT(),
        type_=sa.Text(length=4294967295),
        existing_comment="Stores the internal 'thinking' or reasoning tokens from the model.",
        existing_nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema safely."""

    # 1. Revert Message types
    safe_alter_column(
        "messages",
        "reasoning",
        existing_type=sa.Text(length=4294967295),
        type_=mysql.LONGTEXT(),
        existing_comment="Stores the internal 'thinking' or reasoning tokens from the model.",
        existing_nullable=True,
    )

    safe_alter_column("messages", "content", existing_type=mysql.TEXT(), nullable=True)

    # 2. Drop deleted_at column
    drop_column_if_exists("assistants", "deleted_at")

    # 3. Drop Audit Log table
    # Standard drop_table is usually safe enough in downgrade,
    # but we can check existence to prevent errors if manually dropped.
    if has_table("audit_logs"):
        # Drop indexes first (though drop_table usually handles this, explicit is safer in some dialects)
        op.drop_index(op.f("ix_audit_logs_user_id"), table_name="audit_logs")
        op.drop_index(op.f("ix_audit_logs_timestamp"), table_name="audit_logs")
        op.drop_index(op.f("ix_audit_logs_id"), table_name="audit_logs")
        op.drop_index(op.f("ix_audit_logs_entity_type"), table_name="audit_logs")
        op.drop_index(op.f("ix_audit_logs_entity_id"), table_name="audit_logs")
        op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")

        op.drop_table("audit_logs")
        print("[Alembic-safeDDL] ✅ Dropped table: audit_logs")
    else:
        print("[Alembic-safeDDL] ⚠️ Skipped – table does not exist: audit_logs")
