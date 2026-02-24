from fastapi import APIRouter, Depends, HTTPException, Response, status
from projectdavid_common.schemas.api_key_schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyDetails,
    ApiKeyListResponse,
)
from sqlalchemy.orm import Session

from src.api.entities_api.dependencies import get_api_key, get_db
from src.api.entities_api.models.models import ApiKey as ApiKeyModel
from src.api.entities_api.services.api_key_service import ApiKeyService

router = APIRouter(
    prefix="/users/{user_id}/apikeys",
    tags=["API Keys"],
    responses={404: {"description": "User or Key not found"}},
)


def verify_user_access(requested_user_id: str, authenticated_key: ApiKeyModel):
    """Checks if the authenticated user matches the requested user ID."""
    if authenticated_key.user_id != requested_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to manage API keys for this user.",
        )


@router.post(
    "",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create API Key",
    description="Generates a new API key for the specified user. The plain key is returned only once.",
)
def create_api_key(
    user_id: str,
    request_data: ApiKeyCreateRequest,
    db: Session = Depends(get_db),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    """
    Creates an API key for the user specified in the path (`user_id`).
    - **Authorization**: Request must be authenticated with an API key belonging to the *same* `user_id`.
    - **Input**: Optional key name and expiration days.
    - **Output**: The generated plain API key and its details. **Store the key immediately.**
    """
    verify_user_access(requested_user_id=user_id, authenticated_key=auth_key)
    service = ApiKeyService(db=db)
    try:
        plain_key, created_key_record = service.create_key(
            user_id=user_id,
            key_name=request_data.key_name,
            expires_in_days=request_data.expires_in_days,
        )
        key_details = ApiKeyDetails.model_validate(created_key_record)
        return ApiKeyCreateResponse(plain_key=plain_key, details=key_details)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create API key: {str(e)}",
        )


@router.get(
    "",
    response_model=ApiKeyListResponse,
    summary="List API Keys",
    description="Retrieves a list of API keys for the specified user.",
)
def list_api_keys(
    user_id: str,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    """
    Lists API keys for the user specified in the path (`user_id`).
    - **Authorization**: Request must be authenticated with an API key belonging to the *same* `user_id`.
    - **Query Param**: `include_inactive` (default False) to show revoked keys.
    - **Output**: A list of API key details (prefix, name, dates, status). **Does not include the key itself.**
    """
    verify_user_access(requested_user_id=user_id, authenticated_key=auth_key)
    service = ApiKeyService(db=db)
    try:
        keys_orm = service.list_keys_for_user(
            user_id=user_id, include_inactive=include_inactive
        )
        keys_details = [ApiKeyDetails.model_validate(key) for key in keys_orm]
        return ApiKeyListResponse(keys=keys_details)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list API keys: {str(e)}",
        )


@router.get(
    "/{key_prefix}",
    response_model=ApiKeyDetails,
    summary="Get API Key Details",
    description="Retrieves details for a specific API key using its prefix.",
)
def get_api_key_details(
    user_id: str,
    key_prefix: str,
    db: Session = Depends(get_db),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    """
    Gets details for a specific API key identified by its `key_prefix` for the given `user_id`.
    - **Authorization**: Request must be authenticated with an API key belonging to the *same* `user_id`.
    - **Output**: API key details (prefix, name, dates, status). **Does not include the key itself.**
    """
    verify_user_access(requested_user_id=user_id, authenticated_key=auth_key)
    service = ApiKeyService(db=db)
    try:
        key_orm = service.get_key_details_by_prefix(
            user_id=user_id, key_prefix=key_prefix
        )
        if not key_orm:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"API Key with prefix '{key_prefix}' not found for this user.",
            )
        return ApiKeyDetails.model_validate(key_orm)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get API key details: {str(e)}",
        )


@router.delete(
    "/{key_prefix}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke API Key",
    description="Revokes (deactivates) a specific API key using its prefix.",
)
def revoke_api_key(
    user_id: str,
    key_prefix: str,
    db: Session = Depends(get_db),
    auth_key: ApiKeyModel = Depends(get_api_key),
):
    """
    Revokes (sets `is_active=False`) an API key identified by its `key_prefix` for the given `user_id`.
    - **Authorization**: Request must be authenticated with an API key belonging to the *same* `user_id`.
    - **Output**: `204 No Content` on success. Returns `404` if the key prefix is not found for the user.
    """
    verify_user_access(requested_user_id=user_id, authenticated_key=auth_key)
    service = ApiKeyService(db=db)
    try:
        revoked = service.revoke_key(user_id=user_id, key_prefix=key_prefix)
        if not revoked:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"API Key with prefix '{key_prefix}' not found for this user or could not be revoked.",
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to revoke API key: {str(e)}",
        )
