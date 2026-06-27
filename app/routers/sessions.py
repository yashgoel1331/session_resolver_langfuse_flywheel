from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.auth import verify_session_finder_api_key
from app.models.session_resolver import ResolveSessionRequest, ResolveSessionResponse
from app.services.langfuse_client import (
    build_langfuse_client,
    resolve_langfuse_credentials,
)
from app.services.session_resolver import resolve_session_from_text

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/resolve")
async def resolve_session(
    payload: ResolveSessionRequest,
    x_langfuse_environment: str | None = Header(
        default="dev", alias="X-Langfuse-Environment"
    ),
    _: None = Depends(verify_session_finder_api_key),
) -> ResolveSessionResponse:
    """Resolve a Langfuse session from pasted query or answer text."""
    credentials = resolve_langfuse_credentials(x_langfuse_environment)
    langfuse = build_langfuse_client(credentials)

    try:
        return resolve_session_from_text(langfuse=langfuse, request=payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Session resolution failed: {exc}",
        ) from exc
