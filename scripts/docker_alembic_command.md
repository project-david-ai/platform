docker compose exec api alembic revision --autogenerate -m "add engineer column to assistants"


docker compose exec api alembic revision --autogenerate -m "add BatfishSnapshot to models"