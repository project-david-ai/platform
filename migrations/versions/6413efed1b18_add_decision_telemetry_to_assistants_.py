"""add_decision_telemetry_to_assistants_table_convert_agent_mode_to_bool

Revision ID: 6413efed1b18
Revises: 741d86dd5ac8
Create Date: 2026-02-05 00:50:19.823642

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# Import the safe DDL helpers
from migrations.utils.safe_ddl import (
    add_column_if_missing,
    drop_column_if_exists,
    safe_alter_column,
)

# revision identifiers, used by Alembic.
revision: str = "6413efed1b18"
down_revision: Union[str, None] = "741d86dd5ac8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema safely."""

    # --- Table: assistants ---

    # 1. Add the decision_telemetry column
    add_column_if_missing(
        "assistants",
        sa.Column(
            "decision_telemetry",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),  # Defaults to False for existing rows
            comment="Flag to enable/disable detailed decision telemetry logging.",
        ),
    )

    # 2. DATA SANITIZATION (Critical for Type Conversion)
    # We must convert strings like 'standard' to '0' so MySQL can cast to Boolean/TINYINT.
    LOG_MSG = (
        "Mapping 'standard' to 0 and all other strings to 1 for Boolean conversion..."
    )
    op.execute("UPDATE assistants SET agent_mode = '0' WHERE agent_mode = 'standard'")
    # Catch-all for any other strings to ensure they are truthy (1)
    op.execute("UPDATE assistants SET agent_mode = '1' WHERE agent_mode != '0'")

    # 3. Convert agent_mode from String/Enum to Boolean
    safe_alter_column(
        "assistants",
        "agent_mode",
        existing_type=mysql.VARCHAR(length=255),
        type_=sa.Boolean(),
        comment="Boolean flag indicating the mode of the agent.",
        nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema safely."""

    # --- Table: assistants ---

    # 1. Revert agent_mode back to String
    safe_alter_column(
        "assistants",
        "agent_mode",
        existing_type=sa.Boolean(),
        type_=mysql.VARCHAR(length=255),
        nullable=True,
    )

    # 2. DATA REVERSION (Optional: Restore the 'standard' string)
    op.execute("UPDATE assistants SET agent_mode = 'standard' WHERE agent_mode = '0'")

    # 3. Drop the decision_telemetry column
    drop_column_if_exists("assistants", "decision_telemetry")
