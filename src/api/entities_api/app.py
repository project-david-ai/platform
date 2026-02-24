from fastapi import FastAPI
from projectdavid_common import UtilsInterface
from sqlalchemy import text

# DB
from src.api.entities_api.db.database import engine, wait_for_databases
from src.api.entities_api.models.models import Base
# 🔭 Observability
from src.api.entities_api.observability.tracing import setup_tracing
from src.api.entities_api.routers import api_router

logging_utility = UtilsInterface.LoggingUtility()

# ─────────────────────────────────────────────────────────────
# Application startup
# ─────────────────────────────────────────────────────────────

# 1. Wait for the database(s) to be ready before proceeding.
wait_for_databases()


# 2. Define the app creation factory
def create_app(init_db: bool = True) -> FastAPI:
    logging_utility.info("Creating FastAPI app")

    app = FastAPI(
        title="Entities",
        description="API for AI inference",
        version="1.0.0",
        docs_url="/mydocs",
        redoc_url="/altredoc",
        openapi_url="/openapi.json",
    )

    # 🧠 --- OpenTelemetry MUST be initialised before router binding
    setup_tracing(app)

    # Routes
    app.include_router(api_router, prefix="/v1")

    @app.get("/")
    def read_root():
        logging_utility.info("Root endpoint accessed")
        return {"message": "Welcome to the API!"}

    # 3. Schema init
    if init_db:
        logging_utility.info("Initializing database schema...")
        Base.metadata.create_all(bind=engine)
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE messages MODIFY COLUMN content TEXT"))
        except Exception:
            pass

    return app


# 4. Create the final app instance
app = create_app()
