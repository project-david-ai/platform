import base64
import hashlib
import hmac
import io
import os
from datetime import datetime, timedelta
from typing import Tuple
from urllib.parse import urlencode

from fastapi import HTTPException
from projectdavid_common import UtilsInterface, ValidationInterface
from projectdavid_common.constants.mime_types import (SUPPORTED_MIME_TYPES,
                                                      get_mime_type)
from projectdavid_common.utilities.logging_service import LoggingUtility
from sqlalchemy.orm import Session

from src.api.entities_api.models.models import File, FileStorage, User
from src.api.entities_api.utils.samba_client import SambaClient

logging_utility = LoggingUtility()
validator = ValidationInterface()


class FileService:

    def __init__(self, db: Session):
        """
        Initialize FileService with a database session.
        """
        self.identifier_service = UtilsInterface.IdentifierService()
        self.db = db
        self.samba_client = SambaClient(
            os.getenv("SMBCLIENT_SERVER"),
            os.getenv("SMBCLIENT_SHARE"),
            os.getenv("SMBCLIENT_USERNAME"),
            os.getenv("SMBCLIENT_PASSWORD"),
        )

    def validate_file_type(self, filename: str, content_type: str = None) -> str:
        """
        Validates if the file type is supported based on extension and optional content_type.
        Returns the validated MIME type or raises an HTTPException if not supported.
        """
        mime_type = get_mime_type(filename)
        if not mime_type:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {os.path.splitext(filename)[1].lower()}. Supported types: {list(SUPPORTED_MIME_TYPES.keys())}",
            )
        if content_type and content_type != mime_type:
            raise HTTPException(
                status_code=400,
                detail=f"Content type mismatch: expected {mime_type}, got {content_type}",
            )
        return mime_type

    def validate_user(self, user_id: str) -> User:
        """
        Validates that a user exists in the database.
        """
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    def upload_file(self, file, request) -> File:
        """
        Handles file upload logic with unique filenames for Samba storage.
        """
        mime_type = self.validate_file_type(
            file.filename, getattr(file, "content_type", None)
        )
        try:
            temp_file_path = f"/tmp/{file.filename}"
            with open(temp_file_path, "wb") as f:
                file.file.seek(0)
                f.write(file.file.read())
            file_metadata = File(
                id=self.identifier_service.generate_file_id(),
                object="file",
                bytes=os.path.getsize(temp_file_path),
                created_at=datetime.now(),
                expires_at=None,
                filename=file.filename,
                purpose=request.purpose,
                user_id=request.user_id,
                mime_type=mime_type,
            )
            self.db.add(file_metadata)
            self.db.flush()
            unique_filename = f"{file_metadata.id}_{file.filename}"
            self.samba_client.upload_file(temp_file_path, unique_filename)
            file_storage = FileStorage(
                file_id=file_metadata.id,
                storage_system="samba",
                storage_path=unique_filename,
                is_primary=True,
                created_at=datetime.now(),
            )
            self.db.add(file_storage)
            self.db.commit()
            self.db.refresh(file_metadata)
            os.remove(temp_file_path)
            return file_metadata
        except Exception as e:
            self.db.rollback()
            logging_utility.error(f"Error uploading file: {str(e)}")
            raise HTTPException(
                status_code=500, detail=f"Failed to upload file: {str(e)}"
            )
        finally:
            file.file.close()

    def delete_file_by_id(self, file_id: str) -> bool:
        """
        Delete a file by ID and all its storage locations.
        """
        try:
            file_record = self.db.query(File).filter(File.id == file_id).first()
            if not file_record:
                logging_utility.warning(f"File with ID {file_id} not found in database")
                return False
            storage_locations = (
                self.db.query(FileStorage).filter(FileStorage.file_id == file_id).all()
            )
            for storage_location in storage_locations:
                if storage_location.storage_system == "samba":
                    try:
                        self.samba_client.delete_file(storage_location.storage_path)
                    except Exception as e:
                        logging_utility.error(
                            f"Failed to delete file from Samba: {str(e)}"
                        )
            self.db.delete(file_record)
            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logging_utility.error(f"Error deleting file with ID {file_id}: {str(e)}")
            raise HTTPException(
                status_code=500, detail=f"Failed to delete file: {str(e)}"
            )

    def get_file_by_id(self, file_id: str) -> dict:
        """
        Retrieve file metadata by ID.
        """
        try:
            file_record = self.db.query(File).filter(File.id == file_id).first()
            if not file_record:
                return None
            return {
                "id": file_record.id,
                "object": "file",
                "bytes": file_record.bytes,
                "created_at": int(file_record.created_at.timestamp()),
                "filename": file_record.filename,
                "purpose": file_record.purpose,
            }
        except Exception as e:
            logging_utility.error(f"Error retrieving file with ID {file_id}: {str(e)}")
            raise HTTPException(
                status_code=500, detail=f"Failed to retrieve file: {str(e)}"
            )

    def get_file_as_object(self, file_id: str) -> io.BytesIO:
        """
        Retrieve file content as a file-like object using storage_path from FileStorage.
        """
        file_storage = (
            self.db.query(FileStorage).filter(FileStorage.file_id == file_id).first()
        )
        if not file_storage:
            raise HTTPException(status_code=404, detail="File storage record not found")
        try:
            file_bytes = self.samba_client.download_file_to_bytes(
                file_storage.storage_path
            )
            return io.BytesIO(file_bytes)
        except Exception as e:
            logging_utility.error(
                f"Error retrieving file object for ID {file_id}: {str(e)}"
            )
            raise HTTPException(
                status_code=500, detail=f"Failed to retrieve file: {str(e)}"
            )

    def get_file_as_signed_url(
        self, file_id: str, expires_in: int = 3600, label: str = None
    ) -> str:
        """
        Generate a signed Markdown-safe URL for downloading or rendering the file.

        Args:
            file_id (str): ID of the file in the database
            expires_in (int): Time in seconds until the link expires (default 1 hour)
            label (str): Optional label for Markdown formatting (e.g., "plot.png")

        Returns:
            str: A Markdown-formatted link using [label](<signed_url>) style
        """
        file_record = self.db.query(File).filter(File.id == file_id).first()
        if not file_record:
            raise HTTPException(
                status_code=404, detail=f"File with ID {file_id} not found"
            )
        secret_key = os.getenv("SIGNED_URL_SECRET", "default_secret_key")
        expiration_time = datetime.utcnow() + timedelta(seconds=expires_in)
        expiration_timestamp = int(expiration_time.timestamp())
        data = f"{file_id}:{expiration_timestamp}"
        signature = hmac.new(
            key=secret_key.encode(), msg=data.encode(), digestmod=hashlib.sha256
        ).hexdigest()
        query_params = {
            "file_id": file_id,
            "expires": expiration_timestamp,
            "signature": signature,
        }

        base_url = os.getenv(
            "DOWNLOAD_BASE_URL", "http://localhost:9000/v1/files/download"
        )
        signed_url = f"{base_url}?{urlencode(query_params)}"

        if label:
            return f"[{label}](<{signed_url}>)"
        else:
            return f"<{signed_url}>"

    def get_file_as_base64(self, file_id: str) -> str:
        """
        Retrieve the file content and return it as a BASE64-encoded string.
        """
        file_storage = (
            self.db.query(FileStorage).filter(FileStorage.file_id == file_id).first()
        )
        if not file_storage:
            raise HTTPException(status_code=404, detail="File storage record not found")
        try:
            file_bytes = self.samba_client.download_file_to_bytes(
                file_storage.storage_path
            )
            return base64.b64encode(file_bytes).decode("utf-8")
        except Exception as e:
            logging_utility.error(
                f"Error retrieving BASE64 for file ID {file_id}: {str(e)}"
            )
            raise HTTPException(
                status_code=500, detail=f"Failed to retrieve file: {str(e)}"
            )

    def get_file_with_metadata(self, file_id: str) -> Tuple[io.BytesIO, str, str]:
        """
        Returns (file-like-object, filename, mime_type) tuple for download use.
        """
        file_obj = self.get_file_as_object(file_id)
        file_record = self.db.query(File).filter(File.id == file_id).first()
        if not file_record:
            raise HTTPException(status_code=404, detail="File metadata not found")
        filename = file_record.filename or f"{file_id}"
        mime_type = file_record.mime_type or "application/octet-stream"
        return (file_obj, filename, mime_type)
