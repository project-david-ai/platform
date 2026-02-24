import os
import sys
from logging.config import fileConfig
from typing import Any, Literal, Union

from alembic import context
from alembic.autogenerate import renderers
from alembic.autogenerate.api import AutogenContext
# --- NEW IMPORTS FOR CUSTOM RENDERING ---
from alembic.operations import ops
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# --- PATH FIX (Keep this) ---
project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)


# --- MODELS IMPORT (Keep this) ---
from src.api.entities_api.models.models import Base

load_dotenv()

# This is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config


# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the target metadata for 'autogenerate' support
target_metadata = Base.metadata

# Fetch the URL securely from the environment or .env file
DB_URL = os.getenv("DATABASE_URL")

if not DB_URL:
    raise ValueError(
        "FATAL: DATABASE_URL environment variable is not set or empty. Please check your .env file."
    )
if not DB_URL:
    raise ValueError("FATAL: DATABASE_URL environment variable is not set or empty.")

# Manually set the 'sqlalchemy.url' in the config object that Alembic uses.
# This will override anything in alembic.ini.
config.set_main_option("sqlalchemy.url", DB_URL)


# --- CUSTOM RENDERER LOGIC ---
def render_item(
    type_: str, obj: Any, autogen_context: AutogenContext
) -> Union[str, Literal[False]]:
    """
    Intercepts Alembic operations and renders custom SafeDDL code.
    """

    # 1. Handle Add Column -> safe_ddl.add_column_if_missing
    if type_ == "ops.AddColumnOp":
        column_code = renderers.render_column(obj.column, autogen_context)
        return "safe_ddl.add_column_if_missing('%s', %s)" % (
            obj.table_name,
            column_code,
        )

    # 2. Handle Drop Column -> safe_ddl.drop_column_if_exists
    if type_ == "ops.DropColumnOp":
        return "safe_ddl.drop_column_if_exists('%s', '%s')" % (
            obj.table_name,
            obj.column_name,
        )

    # 3. Handle Alter Column -> safe_ddl.safe_alter_column
    if type_ == "ops.AlterColumnOp":
        args = [f"'{obj.table_name}'", f"'{obj.column_name}'"]

        # --- Handle Existing State ---
        if obj.existing_type is not None:
            existing_type_code = renderers.render_type(
                obj.existing_type, autogen_context
            )
            args.append(f"existing_type={existing_type_code}")

        if obj.existing_nullable is not None:
            args.append(f"existing_nullable={obj.existing_nullable}")

        if obj.existing_comment is not None:
            safe_comment = obj.existing_comment.replace("'", "\\'")
            args.append(f"existing_comment='{safe_comment}'")

        if obj.existing_server_default is not None:
            default_val = renderers.render_server_default(
                obj.existing_server_default, autogen_context
            )
            args.append(f"existing_server_default={default_val}")

        # --- Handle Changes ---
        if obj.modify_type is not None:
            type_code = renderers.render_type(obj.modify_type, autogen_context)
            args.append(f"type_={type_code}")

        if obj.modify_nullable is not None:
            args.append(f"nullable={obj.modify_nullable}")

        if obj.modify_server_default is not None:
            default_val = renderers.render_server_default(
                obj.modify_server_default, autogen_context
            )
            args.append(f"server_default={default_val}")

        if obj.modify_comment is not None:
            safe_comment = obj.modify_comment.replace("'", "\\'")
            args.append(f"comment='{safe_comment}'")

        if obj.modify_name is not None:
            args.append(f"new_column_name='{obj.modify_name}'")

        return f"safe_ddl.safe_alter_column({', '.join(args)})"

    # Default: Return False (Literally False) to let Alembic handle other ops
    return False


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # render_item is strictly for autogeneration (online mode typically),
        # but can be added here if needed, though usually offline just generates SQL.
    )
    with context.begin_transaction():
        context.run_migrations()
