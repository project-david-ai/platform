# src/api/entities_api/services/message_service.py
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from projectdavid_common import UtilsInterface, ValidationInterface

from src.api.entities_api.db.database import SessionLocal

# FIX: Removed Tool from imports.
from src.api.entities_api.models.models import Message, Thread
from src.api.entities_api.services.logging_service import LoggingUtility

validator = ValidationInterface()
logging_utility = LoggingUtility()


class MessageService:

    def __init__(self):
        self.message_chunks: Dict[str, List[str]] = {}
        logging_utility.info(f"Initialized MessageService. Source: {__file__}")

    def _prepare_for_read(self, db_msg: Message) -> Message:
        """
        Helper to ensure meta_data is a dict before Pydantic validation.
        Fixes the 'Input should be a valid dictionary' error.
        """
        if isinstance(db_msg.meta_data, str):
            try:
                db_msg.meta_data = json.loads(db_msg.meta_data)
            except (TypeError, ValueError, json.JSONDecodeError):
                db_msg.meta_data = {}
        elif db_msg.meta_data is None:
            db_msg.meta_data = {}
        return db_msg

    def create_message(self, message: validator.MessageCreate) -> validator.MessageRead:
        logging_utility.info(
            f"Creating message for thread_id={message.thread_id}, role={message.role}."
        )
        with SessionLocal() as db:
            db_thread = db.query(Thread).filter(Thread.id == message.thread_id).first()
            if not db_thread:
                raise HTTPException(status_code=404, detail="Thread not found")

            db_message = Message(
                id=UtilsInterface.IdentifierService.generate_message_id(),
                assistant_id=message.assistant_id,
                attachments=[],
                completed_at=None,
                content=message.content,
                created_at=int(time.time()),
                incomplete_at=None,
                incomplete_details=None,
                # FIX: Pass dict directly. SQLAlchemy JSON type handles serialization.
                meta_data=message.meta_data or {},
                object="message",
                role=message.role,
                run_id=None,
                status=None,
                thread_id=message.thread_id,
                sender_id=message.sender_id,
                tool_id=getattr(message, "tool_id", None),
                tool_call_id=getattr(message, "tool_call_id", None),
            )
            try:
                db.add(db_message)
                db.commit()
                db.refresh(db_message)
            except Exception as e:
                db.rollback()
                logging_utility.error(f"Error saving message: {e}")
                raise HTTPException(status_code=500, detail="Failed to create message")

            return validator.MessageRead.model_validate(
                self._prepare_for_read(db_message)
            )

    def retrieve_message(self, message_id: str) -> validator.MessageRead:
        with SessionLocal() as db:
            db_message = db.query(Message).filter(Message.id == message_id).first()
            if not db_message:
                raise HTTPException(status_code=404, detail="Message not found")

            return validator.MessageRead.model_validate(
                self._prepare_for_read(db_message)
            )

    def list_messages(
        self,
        thread_id: str,
        limit: int = 20,
        order: str = "asc",
    ) -> validator.MessagesList:
        with SessionLocal() as db:
            db_thread = db.query(Thread).filter(Thread.id == thread_id).first()
            if not db_thread:
                raise HTTPException(status_code=404, detail="Thread not found")

            query = db.query(Message).filter(Message.thread_id == thread_id)
            query = (
                query.order_by(Message.created_at.asc())
                if order == "asc"
                else query.order_by(Message.created_at.desc())
            )
            db_messages = query.limit(limit).all()

            messages = [
                validator.MessageRead.model_validate(self._prepare_for_read(m))
                for m in db_messages
            ]

            return validator.MessagesList(
                data=messages,
                first_id=messages[0].id if messages else None,
                last_id=messages[-1].id if messages else None,
                has_more=len(db_messages) >= limit,
            )

    def save_assistant_message_chunk(
        self,
        thread_id: str,
        content: str,
        role: str,
        assistant_id: str,
        sender_id: str,
        is_last_chunk: bool = False,
    ) -> Optional[validator.MessageRead]:
        if thread_id not in self.message_chunks:
            self.message_chunks[thread_id] = []
        self.message_chunks[thread_id].append(content)

        if not is_last_chunk:
            return None

        complete_message = "".join(self.message_chunks.pop(thread_id, []))

        with SessionLocal() as db:
            db_message = Message(
                id=UtilsInterface.IdentifierService.generate_message_id(),
                assistant_id=assistant_id,
                attachments=[],
                completed_at=int(time.time()),
                content=complete_message,
                created_at=int(time.time()),
                meta_data={},  # FIX: Raw dict
                object="message",
                role=role,
                thread_id=thread_id,
                sender_id=sender_id,
            )
            try:
                db.add(db_message)
                db.commit()
                db.refresh(db_message)
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=500, detail="Failed to save message")

            return validator.MessageRead.model_validate(
                self._prepare_for_read(db_message)
            )

    def get_formatted_messages(self, thread_id: str) -> List[Dict[str, Any]]:
        """
        Structures messages for LLM consumption without relying on the Tool table.
        """
        with SessionLocal() as db:
            db_thread = db.query(Thread).filter(Thread.id == thread_id).first()
            if not db_thread:
                raise HTTPException(status_code=404, detail="Thread not found")

            messages = (
                db.query(Message)
                .filter(Message.thread_id == thread_id)
                .order_by(Message.created_at.asc())
                .all()
            )

            formatted_messages: List[Dict[str, Any]] = []

            for db_message in messages:
                role = db_message.role

                if role == "tool":
                    formatted_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": db_message.tool_call_id,
                            "content": db_message.content,
                        }
                    )
                    continue

                if role == "assistant":
                    try:
                        parsed = json.loads(db_message.content)
                        is_tool_list = (
                            isinstance(parsed, list)
                            and len(parsed) > 0
                            and all(
                                isinstance(i, dict) and "function" in i for i in parsed
                            )
                        )

                        if is_tool_list:
                            formatted_messages.append(
                                {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": parsed,
                                }
                            )
                        else:
                            formatted_messages.append(
                                {"role": "assistant", "content": db_message.content}
                            )
                    except (json.JSONDecodeError, TypeError):
                        formatted_messages.append(
                            {"role": "assistant", "content": db_message.content}
                        )
                    continue

                formatted_messages.append(
                    {
                        "role": role,
                        "content": db_message.content,
                    }
                )

            return formatted_messages

    def submit_tool_output(
        self, message: validator.MessageCreate
    ) -> validator.MessageRead:
        with SessionLocal() as db:
            db_thread = db.query(Thread).filter(Thread.id == message.thread_id).first()
            if not db_thread:
                raise HTTPException(status_code=404, detail="Thread not found")

            db_message = Message(
                id=UtilsInterface.IdentifierService.generate_message_id(),
                assistant_id=message.assistant_id,
                content=message.content,
                created_at=int(time.time()),
                meta_data=message.meta_data or {},  # FIX: Raw dict
                object="message",
                role="tool",
                thread_id=message.thread_id,
                tool_id=message.tool_id,
                tool_call_id=message.tool_call_id,
            )
            try:
                db.add(db_message)
                db.commit()
                db.refresh(db_message)
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=500, detail="Failed to create message")

            return validator.MessageRead.model_validate(
                self._prepare_for_read(db_message)
            )

    def delete_message(self, message_id: str) -> validator.MessageDeleted:
        with SessionLocal() as db:
            db_msg = db.query(Message).filter(Message.id == message_id).first()
            if not db_msg:
                raise HTTPException(status_code=404, detail="Message not found")
            db.delete(db_msg)
            db.commit()
            return validator.MessageDeleted(id=message_id)
