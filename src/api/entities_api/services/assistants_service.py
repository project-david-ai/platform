# src/api/entities_api/services/assistant_service.py

import time
from typing import Any, List

from fastapi import HTTPException
from projectdavid import Entity
from projectdavid_common import UtilsInterface, ValidationInterface

from src.api.entities_api.db.database import SessionLocal
from src.api.entities_api.models.models import Assistant, User, VectorStore
from src.api.entities_api.services.logging_service import LoggingUtility
from src.api.entities_api.utils.cache_utils import get_sync_invalidator

logging_utility = LoggingUtility()
validator = ValidationInterface()


class AssistantService:
    """
    CRUD + relationship utilities for `Assistant`.
    """

    RELATIONSHIP_FIELDS = {"users", "vector_stores"}

    @staticmethod
    def _extract_ids(raw_list: list[Any]) -> list[str]:
        ids: list[str] = []
        for item in raw_list or []:
            if isinstance(item, str):
                ids.append(item)
            elif isinstance(item, dict) and "id" in item:
                ids.append(item["id"])
        return ids

    def __init__(self):
        self.client = Entity()

    # ────────────────────────────────────────────────
    # CRUD
    # ────────────────────────────────────────────────
    def create_assistant(
        self, assistant: validator.AssistantCreate
    ) -> validator.AssistantRead:
        with SessionLocal() as db:
            assistant_id = (
                assistant.id or UtilsInterface.IdentifierService.generate_assistant_id()
            )
            # Check if exists (and not soft deleted)
            existing = db.query(Assistant).filter(Assistant.id == assistant_id).first()
            if existing:
                # If it was soft deleted, we could technically "revive" it,
                # but standard practice creates a collision error or requires a purge first.
                raise HTTPException(
                    status_code=400,
                    detail=f"Assistant with ID '{assistant_id}' already exists",
                )

            db_assistant = Assistant(
                id=assistant_id,
                object="assistant",
                created_at=int(time.time()),
                name=assistant.name,
                description=assistant.description,
                model=assistant.model,
                instructions=assistant.instructions,
                tool_configs=assistant.tools,
                tool_resources=assistant.tool_resources,
                meta_data=assistant.meta_data,
                top_p=assistant.top_p,
                temperature=assistant.temperature,
                response_format=assistant.response_format,
                max_turns=assistant.max_turns,
                agent_mode=assistant.agent_mode,
                web_access=assistant.web_access,
                deep_research=assistant.deep_research,
                engineer=assistant.engineer,  # <--- NEW
                decision_telemetry=assistant.decision_telemetry,
                deleted_at=None,  # Explicitly active
            )

            db.add(db_assistant)
            db.commit()
            db.refresh(db_assistant)
            return self.map_to_read_model(db_assistant)

    def retrieve_assistant(self, assistant_id: str) -> validator.AssistantRead:
        with SessionLocal() as db:
            # UPDATE: Filter out soft-deleted items
            db_asst = (
                db.query(Assistant)
                .filter(Assistant.id == assistant_id, Assistant.deleted_at.is_(None))
                .first()
            )

            if not db_asst:
                raise HTTPException(status_code=404, detail="Assistant not found")

            return self.map_to_read_model(db_asst)

    def update_assistant(
        self,
        assistant_id: str,
        assistant_update: validator.AssistantUpdate,
    ) -> validator.AssistantRead:
        # Cache invalidation logic
        try:
            cache = get_sync_invalidator()
            cache.invalidate_sync(assistant_id)
            logging_utility.info(f"Invalidated cache for assistant {assistant_id}")
        except Exception as e:
            logging_utility.error(f"Failed to invalidate cache: {e}")

        with SessionLocal() as db:
            # UPDATE: Ensure we don't update a soft-deleted record
            db_asst = (
                db.query(Assistant)
                .filter(Assistant.id == assistant_id, Assistant.deleted_at.is_(None))
                .first()
            )

            if not db_asst:
                raise HTTPException(404, "Assistant not found")

            data = assistant_update.model_dump(exclude_unset=True)

            for key, val in data.items():
                if key not in self.RELATIONSHIP_FIELDS and key != "tools":
                    setattr(db_asst, key, val)

            if "tools" in data:
                db_asst.tool_configs = data["tools"]

            if "users" in data:
                db_asst.users = (
                    db.query(User)
                    .filter(User.id.in_(self._extract_ids(data["users"])))
                    .all()
                )

            if "vector_stores" in data:
                db_asst.vector_stores = (
                    db.query(VectorStore)
                    .filter(
                        VectorStore.id.in_(self._extract_ids(data["vector_stores"]))
                    )
                    .all()
                )

            db.commit()
            db.refresh(db_asst)
            return self.map_to_read_model(db_asst)

    def list_assistants_by_user(self, user_id: str) -> List[validator.AssistantRead]:
        with SessionLocal() as db:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise HTTPException(404, "User not found")

            # Python-side filtering for relationships is safer if the relationship
            # query isn't dynamic, or we join explicitly.
            # Ideally, change relationship to lazy='dynamic' or filter list comp:
            active_assistants = [a for a in user.assistants if a.deleted_at is None]

            return [self.map_to_read_model(a) for a in active_assistants]

    # ────────────────────────────────────────────────
    # DELETE (GDPR Compliant)
    # ────────────────────────────────────────────────
    def delete_assistant(self, assistant_id: str, permanent: bool = False) -> None:
        """
        Deletes an assistant.

        :param assistant_id: The ID of the assistant.
        :param permanent:
            If False (Default): Soft delete (sets deleted_at). Recoverable by admin.
            If True: Hard delete (GDPR 'Right to Erasure'). Irreversible.
        """

        # 1. Invalidate Cache immediately
        try:
            cache = get_sync_invalidator()
            cache.invalidate_sync(assistant_id)
        except Exception as e:
            logging_utility.error(f"Failed to invalidate cache during delete: {e}")

        with SessionLocal() as db:
            # Find the assistant (even if already soft-deleted, so we can hard delete if requested)
            db_asst = db.query(Assistant).filter(Assistant.id == assistant_id).first()

            if not db_asst:
                # Determine if we should 404.
                # Security best practice: If it doesn't exist, say 404.
                # If it exists but is soft-deleted and this is a soft-delete request, say 404 (already gone).
                raise HTTPException(404, "Assistant not found")

            if permanent:
                # HARD DELETE
                logging_utility.warning(
                    f"PERMANENTLY deleting assistant {assistant_id}"
                )

                # Clear relationships explicitly if not handled by CASCADE
                db_asst.users = []
                db_asst.vector_stores = []

                db.delete(db_asst)
                db.commit()
            else:
                # SOFT DELETE
                if db_asst.deleted_at is not None:
                    # Already deleted
                    raise HTTPException(404, "Assistant not found")

                logging_utility.info(f"Soft deleting assistant {assistant_id}")
                db_asst.deleted_at = int(time.time())

                # Optional: You might want to strip PII from 'name' or 'instructions' here
                # if your policy is strict, but usually Soft Delete retains data for x days.

                db.commit()

    # ────────────────────────────────────────────────
    # ASSOCIATIONS
    # ────────────────────────────────────────────────
    def associate_assistant_with_user(self, user_id: str, assistant_id: str) -> None:
        with SessionLocal() as db:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            db_asst = (
                db.query(Assistant)
                .filter(Assistant.id == assistant_id, Assistant.deleted_at.is_(None))
                .first()
            )
            if not db_asst:
                raise HTTPException(status_code=404, detail="Assistant not found")

            if db_asst not in user.assistants:
                user.assistants.append(db_asst)
                db.commit()
                logging_utility.info(
                    f"Associated assistant {assistant_id} with user {user_id}"
                )

    def disassociate_assistant_from_user(self, user_id: str, assistant_id: str) -> None:
        with SessionLocal() as db:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            db_asst = (
                db.query(Assistant)
                .filter(Assistant.id == assistant_id, Assistant.deleted_at.is_(None))
                .first()
            )
            if not db_asst:
                raise HTTPException(status_code=404, detail="Assistant not found")

            if db_asst in user.assistants:
                user.assistants.remove(db_asst)
                db.commit()
                logging_utility.info(
                    f"Disassociated assistant {assistant_id} from user {user_id}"
                )

    # ────────────────────────────────────────────────
    # Mapper
    # ────────────────────────────────────────────────
    def map_to_read_model(self, db_asst: Assistant) -> validator.AssistantRead:
        data = db_asst.__dict__.copy()
        data.pop("_sa_instance_state", None)
        data["tools"] = db_asst.tool_configs or []
        return validator.AssistantRead.model_validate(data)
