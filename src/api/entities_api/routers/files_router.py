import hashlib
import hmac
import os
from datetime import datetime
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from projectdavid_common.utilities.logging_service import LoggingUtility
from projectdavid_common.validation import FileDeleteResponse, FileResponse
from sqlalchemy.orm import Session

from src.api.entities_api.dependencies import get_api_key, get_db
from src.api.entities_api.models.models import ApiKey as ApiKeyModel
from src.api.entities_api.models.models import File as FileModel
from src.api.entities_api.services.file_service import FileService

load_dotenv()
router = APIRouter()
logging_utility = LoggingUtility()


# ======================================
# Needs to remain at the top to prevent
# /files/{file_id} from overriding.
#
# Used to download files with a signed url
# =========================================
@router.get(
    "/files/download",
    include_in_schema=False,
    response_class=StreamingResponse,
    summary="Download file via signed URL (no API key required)",
)
def download_file(
    file_id: str = Query(...),
    expires: int = Query(...),
    signature: str = Query(...),
    use_real_filename: bool = Query(False),
    db: Session = Depends(get_db),
):
    log_msg = f"!!!!!! Entered /files/download route for file_id: {file_id} !!!!!!"
    print(log_msg, flush=True)
    logging_utility.error(log_msg)

    if datetime.utcnow().timestamp() > expires:
        logging_utility.warning(f"Download URL expired for file_id: {file_id}")

        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Signed URL expired")
    if not _verify_sig(file_id, expires, signature, use_real_filename):
        logging_utility.warning(f"Invalid signature for file_id: {file_id}")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid signature")
    logging_utility.info(f"Signature OK, proceeding to stream file_id: {file_id}")
    stream, orig_name, mime = FileService(db).get_file_with_metadata(file_id)

    fname = orig_name if use_real_filename else file_id
    disp = (
        "inline"
        if mime and mime.startswith(("image/", "text/", "application/pdf"))
        else "attachment"
    )
    return StreamingResponse(
        stream,
        media_type=mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'{disp}; filename="{fname}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/uploads",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file",
)
def upload_file_endpoint(
    purpose: str = Form(..., description="Purpose (e.g. assistants)"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth_key: ApiKeyModel = Depends(get_api_key),
) -> FileResponse:
    user_id = auth_key.user_id
    logging_utility.info("User %s uploading %s (%s)", user_id, file.filename, purpose)
    service = FileService(db)
    created = service.upload_file(
        file=file, request=type("Req", (), {"purpose": purpose, "user_id": user_id})
    )
    return FileResponse.model_validate(created)


@router.get(
    "/files/{file_id}", response_model=FileResponse, summary="Retrieve file metadata"
)
def retrieve_file_metadata(
    file_id: str,
    db: Session = Depends(get_db),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    user_id = auth_key.user_id
    logging_utility.info("User %s reading metadata for %s", user_id, file_id)
    data = FileService(db).get_file_by_id(file_id)
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return FileResponse.model_validate(data)


@router.delete(
    "/files/{file_id}", response_model=FileDeleteResponse, summary="Delete a file"
)
def delete_file_endpoint(
    file_id: str,
    db: Session = Depends(get_db),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    user_id = auth_key.user_id
    logging_utility.info("User %s deleting %s", user_id, file_id)
    if not FileService(db).delete_file_by_id(file_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return FileDeleteResponse(id=file_id, object="file", deleted=True)


def _verify_sig(file_id: str, exp: int, sig: str, real_name: bool) -> bool:
    secret = os.getenv("SIGNED_URL_SECRET", "")
    if not secret:
        logging_utility.critical("SIGNED_URL_SECRET is not configured!")
        return False
    payload = f"{file_id}:{exp}:{str(real_name).lower()}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


@router.get(
    "/files/{file_id}/signed-url",
    response_model=dict,
    summary="Generate a temporary signed URL (no API key required)",
)
def generate_signed_url(
    file_id: str,
    expires_in: int = Query(600, description="Seconds until link expires"),
    use_real_filename: bool = Query(False),
    db: Session = Depends(get_db),
):
    # 1. Validate File Exists
    if not db.query(FileModel).filter(FileModel.id == file_id).first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    # 2. Get Config
    secret = os.getenv("SIGNED_URL_SECRET", "")
    base_url = os.getenv("DOWNLOAD_BASE_URL", "").rstrip("/")

    if not secret or not base_url:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "SIGNED_URL_SECRET or DOWNLOAD_BASE_URL not configured",
        )

    # 3. Calculate Signature
    exp = int(datetime.utcnow().timestamp() + expires_in)
    payload = f"{file_id}:{exp}:{str(use_real_filename).lower()}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    # 4. Construct Query Params
    query = urlencode(
        {
            "file_id": file_id,
            "expires": exp,
            "signature": sig,
            "use_real_filename": use_real_filename,
        }
    )

    target_path = "/v1/files/download"

    # If the .env var ALREADY ends with the path, we do not add it again.
    if base_url.endswith(target_path):
        url = f"{base_url}?{query}"
    else:
        url = f"{base_url}{target_path}?{query}"

    logging_utility.info("Generated signed URL for %s", file_id)
    return {"signed_url": url}


@router.get(
    "/files/{file_id}/base64", response_model=dict, summary="Get file as Base64"
)
def get_file_as_base64(
    file_id: str,
    db: Session = Depends(get_db),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    user_id = auth_key.user_id
    logging_utility.info("User %s requesting base64 for %s", user_id, file_id)
    b64 = FileService(db).get_file_as_base64(file_id)
    return {"file_id": file_id, "base64": b64}
