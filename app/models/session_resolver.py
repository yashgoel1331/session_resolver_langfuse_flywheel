from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ResolveSessionRequest(BaseModel):
    query: Optional[str] = Field(
        default=None,
        min_length=2,
        description="Text to match against query/answer.",
    )
    screenshot_base64: Optional[str] = Field(
        default=None,
        min_length=50,
        description="Base64-encoded screenshot content (without data URL prefix).",
    )
    screenshot_mime_type: str = Field(
        default="image/png",
        description="MIME type for screenshot_base64 (e.g. image/png, image/jpeg).",
    )
    from_timestamp: Optional[datetime] = Field(
        default=None, description="Optional lower bound for observation start time."
    )
    to_timestamp: Optional[datetime] = Field(
        default=None, description="Optional upper bound for observation start time."
    )
    max_candidates: int = Field(
        default=3, ge=1, le=10, description="How many top session candidates to return."
    )
    trace_page_size: int = Field(
        default=100,
        ge=20,
        le=200,
        description="Page size for trace-based scan.",
    )
    trace_pages: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Number of trace pages to scan.",
    )
    min_prefix_chars: int = Field(
        default=40,
        ge=10,
        le=500,
        description=(
            "Minimum prefix length used for longest-prefix fallback. "
            "Full query is always tried first."
        ),
    )

    @model_validator(mode="after")
    def validate_input_sources(self) -> "ResolveSessionRequest":
        has_query = bool((self.query or "").strip())
        has_image = bool((self.screenshot_base64 or "").strip())
        if not has_query and not has_image:
            raise ValueError("Provide either `query` or `screenshot_base64`.")
        return self


class MatchedOn(BaseModel):
    field: Literal["input", "output"]
    snippet: str
    observation_id: str
    trace_id: Optional[str] = None
    timestamp: Optional[datetime] = None


class SessionCandidate(BaseModel):
    session_id: str
    trace_count: int
    matched_on: MatchedOn


class ResolveSessionResponse(BaseModel):
    status: Literal["found", "ambiguous", "not_found"]
    confidence: float = 0.0
    extracted_query: str
    extracted_user_query: Optional[str] = None
    extracted_agent_response: Optional[str] = None
    session_id: Optional[str] = None
    session_created_at: Optional[datetime] = None
    trace_count: Optional[int] = None
    matched_on: Optional[MatchedOn] = None
    candidates: list[SessionCandidate] = Field(default_factory=list)
    search_backend: Literal["traces_v1"]
    message: str
