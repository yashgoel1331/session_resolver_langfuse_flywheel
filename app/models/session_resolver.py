from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ResolveSessionRequest(BaseModel):
    query: str = Field(..., min_length=2, description="Text to match against query/answer.")
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
    session_id: Optional[str] = None
    session_created_at: Optional[datetime] = None
    trace_count: Optional[int] = None
    matched_on: Optional[MatchedOn] = None
    candidates: list[SessionCandidate] = Field(default_factory=list)
    search_backend: Literal["traces_v1"]
    message: str
