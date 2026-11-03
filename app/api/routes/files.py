import logging
import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep, StorageDep
from app.core.config import settings
from app.core.storage import ObjectNotFound, StorageError
from app.models import File, FilePublic, FilesPublic, Message, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

DEFAULT_CONTENT_TYPE = "application/octet-stream"


def _get_owned_file(session: SessionDep, current_user: User, id: uuid.UUID) -> File:
    file = session.get(File, id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    if not current_user.is_superuser and (file.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return file


def _storage_unavailable() -> HTTPException:
    return HTTPException(status_code=502, detail="File storage is unavailable")


@router.get("/", response_model=FilesPublic)
def read_files(
    session: SessionDep, current_user: CurrentUser, skip: int = 0, limit: int = 100
) -> Any:
    """
    Retrieve files.
    """
    count_statement = select(func.count()).select_from(File)
    statement = select(File)
    if not current_user.is_superuser:
        count_statement = count_statement.where(File.owner_id == current_user.id)
        statement = statement.where(File.owner_id == current_user.id)
    count = session.exec(count_statement).one()
    files = session.exec(
        statement.order_by(col(File.created_at).desc()).offset(skip).limit(limit)
    ).all()
    return FilesPublic(
        data=[FilePublic.model_validate(file) for file in files], count=count
    )


@router.post("/", response_model=FilePublic)
def upload_file(
    session: SessionDep,
    current_user: CurrentUser,
    storage: StorageDep,
    file: UploadFile,
) -> Any:
    """
    Upload a file.
    """
    # Read one byte more than allowed to detect oversized uploads
    data = file.file.read(settings.MAX_UPLOAD_BYTES + 1)
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {settings.MAX_UPLOAD_BYTES} bytes)",
        )
    file_id = uuid.uuid4()
    key = storage.key_for(owner_id=str(current_user.id), file_id=str(file_id))
    content_type = file.content_type or DEFAULT_CONTENT_TYPE
    try:
        storage.put(key, data, content_type)
    except StorageError:
        logger.error("Upload to storage failed", exc_info=True)
        raise _storage_unavailable()

    db_file = File(
        id=file_id,
        filename=(file.filename or "file")[:255],
        content_type=content_type[:255],
        size=len(data),
        s3_key=key,
        owner_id=current_user.id,
    )
    try:
        session.add(db_file)
        session.commit()
    except Exception:
        # Do not leave an object without metadata
        session.rollback()
        storage.delete_many([key])
        raise
    session.refresh(db_file)
    return db_file


@router.get(
    "/{id}",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def download_file(
    session: SessionDep, current_user: CurrentUser, storage: StorageDep, id: uuid.UUID
) -> StreamingResponse:
    """
    Download a file.
    """
    file = _get_owned_file(session, current_user, id)
    try:
        stored = storage.get(file.s3_key)
    except ObjectNotFound:
        logger.error("Object missing for file %s", file.id)
        raise HTTPException(status_code=404, detail="File content not found")
    except StorageError:
        logger.error("Download from storage failed", exc_info=True)
        raise _storage_unavailable()
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}",
        "Content-Length": str(stored.content_length),
        # Never let browsers render user supplied content as another type
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        stored.chunks, media_type=file.content_type, headers=headers
    )


@router.delete("/{id}")
def delete_file(
    session: SessionDep, current_user: CurrentUser, storage: StorageDep, id: uuid.UUID
) -> Message:
    """
    Delete a file.
    """
    file = _get_owned_file(session, current_user, id)
    # Object first: if storage fails the metadata is kept and the client can retry
    try:
        storage.delete(file.s3_key)
    except StorageError:
        logger.error("Delete from storage failed", exc_info=True)
        raise _storage_unavailable()
    session.delete(file)
    session.commit()
    return Message(message="File deleted successfully")
