import logging

from fastapi import APIRouter, Depends, Response, status
from pydantic.networks import EmailStr
from sqlalchemy import Engine, text

from app.api.deps import EngineDep, OptionalStorageDep, get_current_active_superuser
from app.core.storage import FileStorage, StorageError
from app.models import Message, Readiness, ReadinessChecks
from app.utils import generate_test_email, send_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/utils", tags=["utils"])


@router.post(
    "/test-email/",
    dependencies=[Depends(get_current_active_superuser)],
    status_code=201,
)
def test_email(email_to: EmailStr) -> Message:
    """
    Test emails.
    """
    email_data = generate_test_email(email_to=email_to)
    send_email(
        email_to=email_to,
        subject=email_data.subject,
        html_content=email_data.html_content,
    )
    return Message(message="Test email sent")


@router.get("/health-check/")
async def health_check() -> bool:
    return True


def _check_database(engine: Engine) -> str:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning("Readiness: database check failed", exc_info=True)
        return "error"
    return "ok"


def _check_storage(storage: FileStorage | None) -> str:
    if storage is None:
        return "not_configured"
    try:
        storage.check_bucket()
    except StorageError:
        logger.warning("Readiness: storage check failed", exc_info=True)
        return "error"
    return "ok"


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
)
def readiness(
    engine: EngineDep, storage: OptionalStorageDep, response: Response
) -> Readiness:
    """
    Readiness: PostgreSQL (SELECT 1) and the S3 bucket (HeadBucket).
    Redis is not checked: the cache is fail-open.
    """
    checks = ReadinessChecks(
        database=_check_database(engine), storage=_check_storage(storage)
    )
    ready = checks.database == "ok" and checks.storage == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ready" if ready else "not_ready", checks=checks)
