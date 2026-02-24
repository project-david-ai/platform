import json
import time
from typing import Any, Dict, List

from fastapi import HTTPException
from projectdavid_common import UtilsInterface, ValidationInterface
from projectdavid_common.utilities.logging_service import LoggingUtility
from sqlalchemy.orm import Session

from entities_api.utils.cache_utils import get_sync_message_cache

# Import the SessionLocal factory from your central database file.
# NOTE: Ensure this import path is correct for your project structure.
from src.api.entities_api.db.database import SessionLocal
from src.api.entities_api.models.models import Message, Thread, User

logging_utility = LoggingUtility()
validator = ValidationInterface()


class ThreadService:
    """CRUD logic for Thread objects."""

    # ──────────────────────────────────────────────────────────
    # Constructor
    # ──────────────────────────────────────────────────────────
    # The constructor no longer accepts or stores a database session.
    def __init__(self):
        pass

    # ──────────────────────────────────────────────────────────
    # Public CRUD
    # ──────────────────────────────────────────────────────────
    def create_thread(
        self,
        thread: validator.ThreadCreate,
    ) -> validator.ThreadReadDetailed:
        """Create a new thread and attach participants."""
        # Each method now creates and manages its own session.
        with SessionLocal() as db:
            existing_users = (
                db.query(User).filter(User.id.in_(thread.participant_ids)).all()
            )
            if len(existing_users) != len(thread.participant_ids):
                raise HTTPException(status_code=400, detail="Invalid user IDs")

            db_thread = Thread(
                id=UtilsInterface.IdentifierService.generate_thread_id(),
                created_at=int(time.time()),
                meta_data=json.dumps({}),
                object="thread",
                tool_resources=json.dumps({}),
            )
            db.add(db_thread)
            for user in existing_users:
                db_thread.participants.append(user)
            db.commit()
            db.refresh(db_thread)
            return self._create_thread_read_detailed(db_thread)

    def get_thread(self, thread_id: str) -> validator.ThreadReadDetailed:
        with SessionLocal() as db:
            db_thread = self._get_thread_or_404(thread_id, db)
            return self._create_thread_read_detailed(db_thread)

    def delete_thread(self, thread_id: str) -> validator.ThreadDeleted:
        logging_utility.info("Deleting thread and clearing history: %s", thread_id)
        with SessionLocal() as db:
            try:
                db_thread = self._get_thread_or_404(thread_id, db)

                # 1. DB Cleanup
                db.query(Message).filter(Message.thread_id == thread_id).delete()
                db_thread.participants = []
                db.delete(db_thread)
                db.commit()

                # 2. Cache Invalidation using the Sync Helper
                try:
                    msg_cache = get_sync_message_cache()
                    msg_cache.delete_history_sync(thread_id)
                    logging_utility.info(
                        f"Invalidated message cache for thread: {thread_id}"
                    )
                except Exception as e:
                    logging_utility.error(f"Failed to invalidate message cache: {e}")

                return validator.ThreadDeleted(id=thread_id)

            except Exception as e:
                db.rollback()
                logging_utility.error("Error deleting thread: %s", str(e))
                raise HTTPException(status_code=500, detail="Delete failed")

    def list_threads_by_user(self, user_id: str) -> List[str]:
        with SessionLocal() as db:
            threads = (
                db.query(Thread)
                .join(Thread.participants)
                .filter(User.id == user_id)
                .all()
            )
            return [thread.id for thread in threads]

    def update_thread_metadata(
        self,
        thread_id: str,
        new_metadata: Dict[str, Any],
    ) -> validator.ThreadReadDetailed:
        with SessionLocal() as db:
            db_thread = self._get_thread_or_404(thread_id, db)
            db_thread.meta_data = json.dumps(new_metadata)
            db.commit()
            db.refresh(db_thread)
            return self._create_thread_read_detailed(db_thread)

    def update_thread(
        self,
        thread_id: str,
        thread_update: validator.ThreadUpdate,
    ) -> validator.ThreadReadDetailed:
        logging_utility.info(f"Attempting to update thread with id: {thread_id}")
        logging_utility.info(f"Update data: {thread_update.dict()}")

        with SessionLocal() as db:
            db_thread = self._get_thread_or_404(thread_id, db)
            update_data = thread_update.dict(exclude_unset=True)

            if "meta_data" in update_data and update_data["meta_data"] is not None:
                current_metadata = self._ensure_dict(db_thread.meta_data)
                current_metadata.update(update_data["meta_data"])
                db_thread.meta_data = current_metadata

            if (
                "tool_resources" in update_data
                and update_data["tool_resources"] is not None
            ):
                db_thread.tool_resources = update_data["tool_resources"]

            for key, value in update_data.items():
                if key not in ("meta_data", "tool_resources"):
                    setattr(db_thread, key, value)

            db.commit()
            db.refresh(db_thread)
            logging_utility.info(f"Successfully updated thread with id: {thread_id}")
            return self._create_thread_read_detailed(db_thread)

    # ──────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────
    # Helper methods that need a session must now accept it as a parameter.
    def _get_thread_or_404(self, thread_id: str, db: Session) -> Thread:
        db_thread = db.query(Thread).filter(Thread.id == thread_id).first()
        if not db_thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        return db_thread

    @staticmethod
    def _ensure_dict(value: Any) -> Dict[str, Any]:
        """Coerce JSON/text column to dict (handles legacy str rows)."""
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return {}
        return {}

    def _create_thread_read_detailed(
        self,
        db_thread: Thread,
    ) -> validator.ThreadReadDetailed:
        """Convert SQLAlchemy Thread → Pydantic ThreadReadDetailed."""
        # This method uses lazy loading which depends on the session from the
        # calling context. This is safe as it's only called from methods
        # that already have an active `with SessionLocal() as db:` block.
        participants = [
            ValidationInterface.UserBase.from_orm(user)
            for user in db_thread.participants
        ]
        return validator.ThreadReadDetailed(
            id=db_thread.id,
            created_at=db_thread.created_at,
            meta_data=self._ensure_dict(db_thread.meta_data),
            object=db_thread.object,
            tool_resources=self._ensure_dict(db_thread.tool_resources),
            participants=participants,
        )
