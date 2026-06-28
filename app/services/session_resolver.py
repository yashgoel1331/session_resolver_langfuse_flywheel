import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional

from langfuse import Langfuse

from app.models.session_resolver import (
    MatchedOn,
    ResolveSessionRequest,
    ResolveSessionResponse,
    SessionCandidate,
)

SearchField = Literal["input", "output"]
_WHITESPACE_RE = re.compile(r"\s+")
# Vision models sometimes substitute common Gujarati synonyms; normalize for matching only.
_MATCH_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("માહિતી", "વિગત"),
    ("માહિતિ", "વિગત"),
)


@dataclass
class _Match:
    session_id: str
    observation_id: str
    trace_id: Optional[str]
    timestamp: Optional[datetime]
    field: SearchField
    snippet: str


def _normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "").strip())


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return str(value)


def _compact_snippet(text: str, limit: int = 220) -> str:
    t = _normalize_text(text)
    if len(t) <= limit:
        return t
    return f"{t[: limit - 3]}..."


def _normalize_for_match(text: str) -> str:
    normalized = unicodedata.normalize("NFC", _normalize_text(text)).casefold()
    normalized = _normalize_indic_digits(normalized)
    for source, target in _MATCH_SYNONYMS:
        normalized = normalized.replace(source.casefold(), target.casefold())
    return normalized


def _normalize_indic_digits(text: str) -> str:
    chars: list[str] = []
    for ch in text:
        if "\u0ae6" <= ch <= "\u0aef":
            chars.append(chr(ord("0") + ord(ch) - 0x0AE6))
        elif "\u0966" <= ch <= "\u096f":
            chars.append(chr(ord("0") + ord(ch) - 0x0966))
        else:
            chars.append(ch)
    return "".join(chars)


def _build_prefix_variants(query: str, min_prefix_chars: int, max_variants: int = 8) -> list[str]:
    """Build longest-prefix variants: full query first, then shorter prefixes."""
    full = _normalize_text(query)
    if not full:
        return []

    variants = [full]
    words = full.split()
    if len(words) <= 1:
        return variants

    for cut_count in range(1, len(words)):
        prefix = " ".join(words[:-cut_count]).strip()
        if len(prefix) < min_prefix_chars:
            break
        variants.append(prefix)
        if len(variants) >= max_variants:
            break

    return variants


def _ts_value(value: Optional[datetime]) -> float:
    if value is None:
        return float("-inf")
    return value.timestamp()


def _collect_matches_from_traces(
    *,
    langfuse: Langfuse,
    request: ResolveSessionRequest,
) -> list[_Match]:
    """Primary search path: scan recent traces with exact substring matching."""
    matches: list[_Match] = []
    seen_trace_ids: set[str] = set()
    query_variants = [
        _normalize_for_match(v)
        for v in _build_prefix_variants(request.query or "", request.min_prefix_chars)
    ]
    query_variants = [v for v in query_variants if v]
    if not query_variants:
        return matches

    for page in range(1, request.trace_pages + 1):
        traces_page = langfuse.api.trace.list(
            page=page,
            limit=request.trace_page_size,
            from_timestamp=request.from_timestamp,
            to_timestamp=request.to_timestamp,
            order_by="timestamp.desc",
            fields="core,io",
        )
        traces = traces_page.data or []
        if not traces:
            break

        for trace in traces:
            if not trace.session_id or trace.id in seen_trace_ids:
                continue

            input_text = _to_text(trace.input)
            output_text = _to_text(trace.output)
            normalized_input = _normalize_for_match(input_text) if input_text else ""
            normalized_output = _normalize_for_match(output_text) if output_text else ""

            best_field: Optional[SearchField] = None
            best_text = ""

            # Longest-prefix matching: try the full query first, then shorter prefixes.
            for normalized_query in query_variants:
                if normalized_query in normalized_input:
                    best_field = "input"
                    best_text = input_text
                    break
                if normalized_query in normalized_output:
                    best_field = "output"
                    best_text = output_text
                    break

            if best_field is None:
                continue

            seen_trace_ids.add(trace.id)
            matches.append(
                _Match(
                    session_id=trace.session_id,
                    observation_id=trace.id,
                    trace_id=trace.id,
                    timestamp=trace.timestamp,
                    field=best_field,
                    snippet=_compact_snippet(best_text),
                )
            )

    return matches


def _candidate_from_group(session_id: str, group: list[_Match]) -> SessionCandidate:
    best = max(group, key=lambda m: _ts_value(m.timestamp))
    return SessionCandidate(
        session_id=session_id,
        trace_count=len(group),
        matched_on=MatchedOn(
            field=best.field,
            snippet=best.snippet,
            observation_id=best.observation_id,
            trace_id=best.trace_id,
            timestamp=best.timestamp,
        ),
    )


def resolve_session_from_text(
    *,
    langfuse: Langfuse,
    request: ResolveSessionRequest,
) -> ResolveSessionResponse:
    normalized_query = _normalize_text(request.query or "")
    backend: Literal["traces_v1"] = "traces_v1"
    matches = _collect_matches_from_traces(langfuse=langfuse, request=request)

    if not matches:
        return ResolveSessionResponse(
            status="not_found",
            confidence=0.0,
            extracted_query=normalized_query,
            search_backend=backend,
            message="No matching session found in recent traces for the provided text.",
        )

    grouped: dict[str, list[_Match]] = {}
    for match in matches:
        grouped.setdefault(match.session_id, []).append(match)

    ranked = sorted(
        (_candidate_from_group(session_id, group) for session_id, group in grouped.items()),
        key=lambda c: (
            _ts_value(c.matched_on.timestamp),
            c.trace_count,
        ),
        reverse=True,
    )
    top = ranked[0]
    confidence = 1.0 if len(ranked) == 1 else 0.5

    should_return_found = len(ranked) == 1
    if not should_return_found:
        return ResolveSessionResponse(
            status="ambiguous",
            confidence=confidence,
            extracted_query=normalized_query,
            candidates=ranked[: request.max_candidates],
            matched_on=top.matched_on,
            search_backend=backend,
            message="Multiple sessions matched the text. Please choose one of the candidates.",
        )

    session = langfuse.api.sessions.get(top.session_id)
    return ResolveSessionResponse(
        status="found",
        confidence=confidence,
        extracted_query=normalized_query,
        session_id=top.session_id,
        session_created_at=session.created_at,
        trace_count=len(session.traces or []),
        matched_on=top.matched_on,
        candidates=ranked[: request.max_candidates],
        search_backend=backend,
        message="Resolved a matching Langfuse session via trace-based search.",
    )
