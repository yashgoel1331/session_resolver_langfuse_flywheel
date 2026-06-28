from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.auth import verify_session_finder_api_key
from app.models.session_resolver import ResolveSessionRequest, ResolveSessionResponse
from app.services.langfuse_client import (
    build_langfuse_client,
    resolve_langfuse_credentials,
)
from app.services.image_query_extractor import (
    ImageQueryExtractionError,
    ScreenshotExtraction,
    extract_texts_from_screenshot,
)
from app.services.session_resolver import resolve_session_from_text

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _with_extraction_fields(
    result: ResolveSessionResponse,
    extraction: ScreenshotExtraction | None,
) -> ResolveSessionResponse:
    if extraction is None:
        return result
    return result.model_copy(
        update={
            "extracted_user_query": extraction.user_query or None,
            "extracted_agent_response": extraction.agent_response or None,
        }
    )


@router.post("/resolve")
async def resolve_session(
    payload: ResolveSessionRequest,
    x_langfuse_environment: str | None = Header(
        default="dev", alias="X-Langfuse-Environment"
    ),
    _: None = Depends(verify_session_finder_api_key),
) -> ResolveSessionResponse:
    """Resolve a Langfuse session from query text or screenshot-extracted text."""
    credentials = resolve_langfuse_credentials(x_langfuse_environment)
    langfuse = build_langfuse_client(credentials)

    try:
        normalized_query = (payload.query or "").strip()
        search_texts = [normalized_query] if normalized_query else []
        extraction: ScreenshotExtraction | None = None

        if not search_texts:
            extraction = extract_texts_from_screenshot(
                screenshot_base64=payload.screenshot_base64 or "",
                mime_type=payload.screenshot_mime_type,
            )
            search_texts = extraction.search_candidates()

        last_result: ResolveSessionResponse | None = None
        for search_text in search_texts:
            result = resolve_session_from_text(
                langfuse=langfuse,
                request=payload.model_copy(update={"query": search_text}),
            )
            last_result = result
            if result.status != "not_found":
                return _with_extraction_fields(result, extraction)

        if last_result is None:
            raise ImageQueryExtractionError(
                "No searchable text was available for session resolution.",
                status_code=502,
            )

        primary_text = search_texts[0]
        tried_count = len(search_texts)
        return _with_extraction_fields(
            last_result.model_copy(
                update={
                    "extracted_query": primary_text,
                    "message": (
                        f"No matching session found after trying {tried_count} "
                        f"extracted text candidate(s). First searched: {primary_text[:160]}"
                        f"{'...' if len(primary_text) > 160 else ''}"
                    ),
                }
            ),
            extraction,
        )
    except ImageQueryExtractionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Session resolution failed: {exc}",
        ) from exc
