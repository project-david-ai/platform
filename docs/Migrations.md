# Alembic SafeDDL Utilities

We use the Alembic library to handle database migrations and extensions. The schema changes from time to time. 
If every developer had to apply each new models.py revision locally, we would get mismatched schemas and broken API services.
To prevent that, we push database changes automatically to every environment: dev, pre‑staging, and production.
Two scenarios cover nearly everything:

## A. Keep existing data

- `models.py` is newer than the running database schema.

- Endpoints, services, or SDK clients may already depend on the update.

- After pulling the latest code and rebuilding containers, the database image stays intact, so the schema is still out of sync.

- `init_and_run_api.sh` checks for pending Alembic migrations and applies them. You will find those migrations in
 migrations/versions.


## B. Start fresh

- The schema changed, and the developer rebuilds images and clears volumes.

- All data is wiped, so the tables are created from scratch with the current schema.

- Alembic does not need to merge anything; it just builds a clean database.


## SafeDDL helpers

This repo also includes fault‑tolerant helpers for writing idempotent, declarative Alembic migrations.
They stop repeated migrations from throwing errors and make auto‑generated diffs painless.


```bash
migrations/utils/safe_ddl.py
```

---

## ✨ Features

| Function                  | Behavior                                                    |
|--------------------------|-------------------------------------------------------------|
| `add_column_if_missing`  | Adds a column only if it doesn't exist                      |
| `drop_column_if_exists`  | Drops a column only if it exists                            |
| `safe_alter_column`      | Alters column only if it exists                             |
| `has_table`              | Checks if a table exists                                    |
| `has_column`             | Checks if a column exists in a table                        |

---

## 🧱 Example Usage in a Migration

```python
from migrations.utils.safe_ddl import add_column_if_missing, safe_alter_column

import sqlalchemy as sa
from sqlalchemy.dialects import mysql


def upgrade():
    add_column_if_missing(
        "users",
        sa.Column("profile_url", sa.String(255), nullable=True)
    )

    safe_alter_column(
        "users",
        "email",
        existing_type=mysql.VARCHAR(length=255),
        nullable=False,
        comment="User email address (required)"
    )


def downgrade():
    safe_alter_column(
        "users",
        "email",
        existing_type=mysql.VARCHAR(length=255),
        nullable=True,
    )
```

---

## 💬 Logging

Each safe operation logs a status message to stdout:

```text
[Alembic‑safeDDL] ✅ Added column: users.profile_url
[Alembic‑safeDDL] ⚠️ Skipped – column already exists: users.profile_url
```

---

## 🤝 Contribution Tips

- Prefer `safe_ddl` for all column-level operations in production migrations.
- Avoid raw SQL unless absolutely necessary.
- Always run `alembic upgrade head && alembic downgrade -1` to validate reversibility.

---

## 📁 File Layout Example

```
migrations/
├── env.py
├── script.py.mako
├── versions/
│   └── 2025_05_01_add_tool_columns.py
└── utils/
    └── safe_ddl.py
```
