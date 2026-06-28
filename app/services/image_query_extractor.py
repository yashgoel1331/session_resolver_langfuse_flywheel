import json
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import settings

_WHITESPACE_RE = re.compile(r"\s+")
_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")
_DATE_RE = re.compile(r"\d{2}-\d{2}-\d{4}")
_DECIMAL_RE = re.compile(r"\d+\.\d{2}")
_ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
_OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

_EXTRACTION_SYSTEM_MESSAGE = (
    "You are a verbatim OCR engine for Gujarati chat screenshots. "
    "Transcribe visible text character-for-character. "
    "Never paraphrase, translate, correct grammar, or guess words."
)

_EXTRACTION_PROMPT = """\
You are performing OCR on a screenshot of the Amul AI chat app (Gujarati UI).

## Task
Transcribe visible text into JSON only. This is OCR/transcription — NOT summarization, translation, or question answering.

Output exactly:
{ "user_query": string|null, "agent_response": string|null }

No markdown fences. No extra keys. No commentary.

## Amul AI layout
- USER message: compact bubble, often top-right, pink/salmon background, may show avatar "US".
- AGENT message: larger white bubble below, labeled "Amul AI" with logo.
- Ignore avatars, logos, labels ("US", "Amul AI"), timestamps, buttons, and all other UI chrome.

## agent_response rules
- Verbatim prose from the agent white bubble only.
- If the agent reply is primarily a data table or grid → null (do not flatten tables).
- Otherwise transcribe agent prose character-for-character (long answers included).
- When agent prose is visible, always populate this field.
- Preserve numerals exactly as shown in the image (Arabic 70 vs Gujarati ૭૦ — do not convert).

## Extraction vs search (do not confuse)
- Extract BOTH fields accurately whenever visible.
- After extraction, search order is: user_query first (short bubble, fewer OCR errors),
  then opening snippet(s) of agent prose (not the full long reply).
- Table replies → agent_response is null; only user_query is searched.

## user_query rules (user bubble only)
- ONLY the text inside the USER bubble (pink/salmon, top-right).
- Copy character-for-character: exact Unicode, matras, anusvara, punctuation.
- Do NOT paraphrase or substitute synonyms (keep "વિગત", never "માહિતી").
- Do NOT fix grammar or change verb form (keep "દૂર કરવું", never "દૂર કરો").
- Do NOT convert Gujarati numerals to Arabic (keep "૭", never "7").
- Do NOT guess unknown words — read the pixels; use visually closest Gujarati characters.
- English/medical terms written in Gujarati script are transliterations — copy exactly:
  keep "પ્લેસેન્ટા", never "પ્રેસેંટા", "પેશન્ટ", or invented words.
- Do NOT include agent tables, table headers, table rows, or agent narrative in user_query.
- Do NOT flatten tables into user_query.

## Examples (user_query)

Example A — milk collection screenshot:
  CORRECT:   "છેલ્લા ૭ દિવસની મારી દૂધ ભરવાની વિગત આપો."
  WRONG:     "છેલ્લા 7 દિવસની મારી દૂધ ભરવાની માહિતી આપો."  (digit + synonym swap)
  WRONG:     "દૂધ એકત્રીકરણ તારીખ શિફ્ટ..."  (table data, not user bubble)

Example B — placenta / veterinary screenshot:
  CORRECT:   "જન્મ પછી મારી ગાયમાં રહેલ પ્લેસેન્ટા કેવી રીતે દૂર કરવું"
  WRONG:     "જમ્યા પછી મારી ગાયમાં રહેલ પ્રેસેંટા કેવી રીતે દૂર કરવું"  (જન્મ→જમ્યા, spelling)
  WRONG:     "જમણ પહેલા મારી ગાયમાં રહેલ પેશન્ટની ૩૦મી રીતી દૂર કરો."  (hallucinated)

Example C — general rules:
  CORRECT:   copy exactly what is visible in the user bubble, even if incomplete.
  WRONG:     inferring meaning, rephrasing, or merging user + agent text.

Now OCR the attached screenshot.\
"""

_TEXT_WITHOUT_BUBBLE_PROMPT = """\

## No-bubble fallback (caller hint)
The caller indicated that text visible WITHOUT a chat bubble should be treated as: {role_label}.
- If neither a USER bubble (pink/salmon, top-right) nor an AGENT bubble (white, "Amul AI") is visible, put all transcribed text in {target_field} and set the other JSON field to null.
- If chat bubbles ARE visible, follow the normal bubble rules above and ignore this hint.
"""

def _build_extraction_prompt(
    text_without_bubble_as: Literal["user", "agent"] | None = None,
) -> str:
    prompt = _EXTRACTION_PROMPT
    if text_without_bubble_as is None:
        return prompt

    if text_without_bubble_as == "user":
        role_label = "user query"
        target_field = "user_query"
    else:
        role_label = "agent response"
        target_field = "agent_response"

    return prompt + _TEXT_WITHOUT_BUBBLE_PROMPT.format(
        role_label=role_label,
        target_field=target_field,
    )


@dataclass(frozen=True)
class ScreenshotExtraction:
    user_query: str
    agent_response: str

    def search_candidates(self) -> list[str]:
        """User bubble first; agent opening snippet(s) as fallback. Skip table agent text."""
        candidates: list[str] = []
        user = self.user_query
        agent = self.agent_response

        if user:
            candidates.append(user)

        if _looks_like_table_data(agent):
            return candidates

        for snippet in _agent_search_snippets(agent):
            if snippet not in candidates:
                candidates.append(snippet)

        return candidates


def _apply_text_without_bubble_role(
    extraction: ScreenshotExtraction,
    text_without_bubble_as: Literal["user", "agent"] | None,
) -> ScreenshotExtraction:
    """Reassign lone extracted text when bubbles were not visible."""
    if text_without_bubble_as is None:
        return extraction

    user = extraction.user_query
    agent = extraction.agent_response

    if user and agent:
        return extraction

    lone_text = user or agent
    if not lone_text:
        return extraction

    if text_without_bubble_as == "user":
        if user:
            return extraction
        return ScreenshotExtraction(user_query=lone_text, agent_response="")

    if agent:
        return extraction
    return ScreenshotExtraction(user_query="", agent_response=lone_text)


class ImageQueryExtractionError(Exception):
    """Raised when screenshot text extraction fails."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _vision_model() -> str:
    return (settings.openai_vision_model or "gpt-4o").strip() or "gpt-4o"


def _normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "").strip())


def _clean_model_output(text: str) -> str:
    cleaned = _CODE_FENCE_RE.sub("", (text or "").strip()).strip()
    return _normalize_text(cleaned)


def _looks_like_table_data(text: str) -> bool:
    if not text or len(text) < 40:
        return False
    if len(_DATE_RE.findall(text)) >= 2:
        return True
    return len(_DECIMAL_RE.findall(text)) >= 3


def _agent_search_snippets(agent: str, *, max_lead_in: int = 180) -> list[str]:
    """Prefer a short opening snippet; full agent OCR rarely matches trace output."""
    agent = _normalize_text(agent)
    if not agent:
        return []

    snippets: list[str] = []
    for sep in (". ", ":", "\n"):
        idx = agent.find(sep)
        if idx >= 40:
            lead_in = agent[: idx + len(sep)].strip()
            if len(lead_in) >= 40:
                snippets.append(lead_in)
                break

    if not snippets and len(agent) > max_lead_in:
        snippets.append(agent[:max_lead_in].strip())

    if len(agent) <= max_lead_in and agent not in snippets:
        snippets.append(agent)
    elif len(agent) > max_lead_in and agent not in snippets:
        # Last resort: full text (usually too noisy to match).
        snippets.append(agent)

    return list(dict.fromkeys(snippets))


def _extract_text_from_openai_response(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""

    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return _clean_model_output(content)
    if isinstance(content, list):
        text_parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
        ]
        return _clean_model_output(" ".join(text_parts))
    return ""


def _parse_extraction_payload(raw_text: str) -> dict[str, Any]:
    cleaned = _clean_model_output(raw_text)
    if not cleaned:
        return {}

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ImageQueryExtractionError(
            "OpenAI returned invalid JSON for screenshot extraction.",
            status_code=502,
        ) from exc

    if not isinstance(parsed, dict):
        raise ImageQueryExtractionError(
            "OpenAI returned unexpected extraction payload.",
            status_code=502,
        )
    return parsed


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _normalize_text(value)
    return _normalize_text(str(value))


def _normalize_mime_type(mime_type: str) -> str:
    normalized = (mime_type or "image/png").strip().lower()
    if normalized == "image/jpg":
        return "image/jpeg"
    return normalized


def _build_extraction(raw: dict[str, Any]) -> ScreenshotExtraction:
    user_query = _coerce_text(raw.get("user_query"))
    agent_response = _coerce_text(raw.get("agent_response"))

    if _looks_like_table_data(user_query):
        user_query = ""

    if _looks_like_table_data(agent_response) and not user_query:
        agent_response = ""

    return ScreenshotExtraction(user_query=user_query, agent_response=agent_response)


def extract_texts_from_screenshot(
    *,
    screenshot_base64: str,
    mime_type: str,
    text_without_bubble_as: Literal["user", "agent"] | None = None,
) -> ScreenshotExtraction:
    api_key = (settings.openai_api_key or "").strip()
    if not api_key:
        raise ImageQueryExtractionError(
            "OPENAI_API_KEY is not configured on the server.",
            status_code=500,
        )

    normalized_mime = _normalize_mime_type(mime_type)
    if normalized_mime not in _ALLOWED_MIME_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_MIME_TYPES))
        raise ImageQueryExtractionError(
            f"Unsupported screenshot_mime_type '{mime_type}'. Allowed values: {allowed}."
        )

    image_data = (screenshot_base64 or "").strip()
    if not image_data:
        raise ImageQueryExtractionError("screenshot_base64 is empty.")

    extraction_prompt = _build_extraction_prompt(text_without_bubble_as)

    request_body = {
        "model": _vision_model(),
        "messages": [
            {"role": "system", "content": _EXTRACTION_SYSTEM_MESSAGE},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": extraction_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{normalized_mime};base64,{image_data}",
                            "detail": "high",
                        },
                    },
                ],
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 1024,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                _OPENAI_CHAT_COMPLETIONS_URL,
                headers=headers,
                json=request_body,
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:400] if exc.response is not None else str(exc)
        raise ImageQueryExtractionError(
            f"OpenAI request failed: {detail}",
            status_code=502,
        ) from exc
    except httpx.HTTPError as exc:
        raise ImageQueryExtractionError(f"OpenAI request failed: {exc}", status_code=502) from exc

    raw_output = _extract_text_from_openai_response(response.json())
    extraction = _build_extraction(_parse_extraction_payload(raw_output))
    extraction = _apply_text_without_bubble_role(extraction, text_without_bubble_as)
    if not extraction.search_candidates():
        raise ImageQueryExtractionError(
            "OpenAI could not extract a usable user query or agent response from the screenshot.",
            status_code=502,
        )

    return extraction


def extract_search_text_from_screenshot(*, screenshot_base64: str, mime_type: str) -> str:
    extraction = extract_texts_from_screenshot(
        screenshot_base64=screenshot_base64,
        mime_type=mime_type,
    )
    return extraction.search_candidates()[0]


extract_query_from_screenshot = extract_search_text_from_screenshot
