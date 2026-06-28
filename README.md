# Session Resolver

Internal FastAPI service that helps engineers find a **Langfuse session** by pasting text (or a screenshot) copied from a chat UI, trace view, or support ticket.

Typical use case: someone shares a user question, assistant answer snippet, or screenshot, and you need the corresponding Langfuse session link/id without manually searching the UI.

---

## What this service does

```
Pasted text OR screenshot
        ↓
(Optional) OpenAI vision extraction for screenshots
        ↓
Scan recent Langfuse traces
        ↓
Exact substring match on trace input/output
        ↓
Group matches by sessionId
        ↓
Return found / ambiguous / not_found
```

This is **not** a chat agent. It is a lookup tool over Langfuse observability data.

---

## Current status

### Implemented and ready to test

| Feature | Status |
|---|---|
| FastAPI app + health endpoints | Ready |
| Internal API-key auth (`X-API-Key`) | Ready |
| Dev/prod Langfuse env switching (`X-Langfuse-Environment`) | Ready |
| Text-based session resolution (`POST /api/sessions/resolve`) | Ready |
| Trace-based search (`traces_v1`) for self-hosted Langfuse | Ready |
| Exact substring matching (case-insensitive) | Ready |
| Longest-prefix fallback with minimum length | Ready |
| Match on both trace `input` and `output` | Ready |
| Rank candidates by recency | Ready |
| Response statuses: `found`, `ambiguous`, `not_found` | Ready |

### Planned / not implemented yet

| Feature | Status |
|---|---|
| Observations v2 server-side text search | Not available on our self-hosted Langfuse |
| Langfuse deep-link URL generation | Not yet returned in API response |

---

## Architecture (high level)

```
Client
  └── POST /api/sessions/resolve
        ├── Auth: X-API-Key
        ├── Langfuse env: X-Langfuse-Environment (dev|prod)
        └── Resolver service
              ├── OpenAI vision (optional)       # screenshot -> extracted text
              ├── langfuse.api.trace.list(...)   # fetch recent traces with IO
              ├── substring match in Python
              ├── group by sessionId
              └── langfuse.api.sessions.get(...) # when unambiguous
```

Project layout:

```
session_resolver/
├── main.py                         # FastAPI entrypoint
├── app/
│   ├── auth.py                     # internal API key check
│   ├── config.py                   # env settings
│   ├── models/session_resolver.py  # request/response schemas
│   ├── routers/
│   │   ├── health.py
│   │   └── sessions.py             # /api/sessions/resolve
│   └── services/
│       ├── image_query_extractor.py # screenshot text extraction via OpenAI
│       ├── langfuse_client.py       # dev/prod credential selection
│       └── session_resolver.py      # search + ranking logic
├── requirements.txt
└── example.env
```

---

## Langfuse compatibility notes

Validated against self-hosted Langfuse (`3.150.0`):

| Endpoint | Status on our instance |
|---|---|
| `/api/public/v2/observations` | `501` (Cloud-only) |
| `/api/public/observations` (v1) | `200` |
| `/api/public/traces` | `200` |
| `/api/public/sessions` | `200` |

Because v2 observations is unavailable, this service uses **trace-based client-side matching** instead of Langfuse indexed text search.

Implications:

- Search scans a bounded recent trace window (default: 300 traces).
- Matching happens in this API, not inside Langfuse.
- Older sessions require narrowing time range or increasing scan size.

---

## Screenshot extraction strategy

When resolving from a screenshot, OpenAI vision (`gpt-4o` by default, configurable via `OPENAI_VISION_MODEL`) runs **verbatim OCR** on the Amul AI UI:

- Targets the **user bubble only** (pink/salmon, top-right) for `user_query`
- Uses `detail: high` for sharper text reading
- Forbids paraphrase, synonym swap, digit conversion, and table flattening
- Sets `agent_response` to `null` when the reply is a data table
- Optional `text_without_bubble_as` (`user` | `agent`) for screenshots without chat bubbles

Search order after extraction:

1. **User query** — short bubble text, matches `trace.input`, fewer OCR errors.
2. **Agent opening snippet** — first sentence/lead-in of agent prose (not the full long reply).
3. **Full agent text** — last resort only when snippet alone is too short.

Table replies → `agent_response` is null; only user query is searched.

Response includes `extracted_user_query` and `extracted_agent_response` for screenshot debugging.

---

## Matching behavior

For each trace, the resolver checks:

1. `trace.input`
2. `trace.output`

Matching rules:

1. **Exact substring**, case-insensitive (whitespace normalized).
2. If full query does not match, try **longest-prefix fallback** by trimming words from the end until a match is found.
3. Prefix fallback stops at `min_prefix_chars` (default: `40`).

Why prefix fallback exists:

- Users often paste text spanning multiple UI sections (paragraph + heading).
- The full paste may not exist as one contiguous string in trace IO, even though a large prefix does.

Ranking:

- Candidates are grouped by `sessionId`.
- Sorted by most recent matching trace timestamp (desc), then `trace_count`.

Result selection:

- `found`: exactly one session matched.
- `ambiguous`: multiple sessions matched.
- `not_found`: no matches in scanned trace window.

---

## How to run locally

### 1) Prerequisites

- Python 3.12+
- Access to Langfuse dev/prod project API keys
- Network access to Langfuse host(s)

### 2) Setup

```bash
cd session_resolver
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp example.env .env
```

Fill `.env`:

```bash
ENVIRONMENT=development
DEBUG=true
HOST=0.0.0.0
PORT=8090

SESSION_FINDER_API_KEY=your-internal-key

LANGFUSE_DEV_PUBLIC_KEY=pk-lf-...
LANGFUSE_DEV_SECRET_KEY=sk-lf-...
LANGFUSE_DEV_BASE_URL=https://langfuse.dev.amulai.in

LANGFUSE_PROD_PUBLIC_KEY=pk-lf-...
LANGFUSE_PROD_SECRET_KEY=sk-lf-...
LANGFUSE_PROD_BASE_URL=https://langfuse.prod.amulai.in

OPENAI_API_KEY=sk-...   # required only for screenshot-based requests
```

Important:

- Langfuse keys must match the host/project they were created for.
- Shell commands do not auto-load `.env`; the app loads it via `python-dotenv`.

### 3) Start server

```bash
uvicorn main:app --reload --port 8090
```

Or:

```bash
python main.py
```

Open API docs:

- Swagger UI: `http://localhost:8090/docs`
- Health: `http://localhost:8090/api/health/`

---

## API usage

### Resolve session from text or screenshot

`POST /api/sessions/resolve`

Headers:

- `Content-Type: application/json`
- `X-API-Key: <SESSION_FINDER_API_KEY>`
- `X-Langfuse-Environment: dev` (or `prod`)

Example:

```bash
curl -X POST "http://localhost:8090/api/sessions/resolve" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SESSION_FINDER_API_KEY" \
  -H "X-Langfuse-Environment: dev" \
  -d '{
    "query": "Give me my milk collection and deduction details of last 7 days",
    "max_candidates": 3,
    "trace_pages": 3,
    "trace_page_size": 100,
    "min_prefix_chars": 40
  }'
```

Request fields:

| Field | Default | Description |
|---|---|---|
| `query` | null | Text to search (user query or assistant answer snippet) |
| `screenshot_base64` | null | Base64 image content for screenshot-based resolution |
| `screenshot_mime_type` | `image/png` | MIME type for screenshot data (`image/png`, `image/jpeg`, `image/webp`) |
| `text_without_bubble_as` | null | When text is visible but no chat bubble, treat it as `"user"` query or `"agent"` response |
| `from_timestamp` | null | Optional lower bound for trace timestamp |
| `to_timestamp` | null | Optional upper bound for trace timestamp |
| `max_candidates` | 3 | Max candidates returned when ambiguous |
| `trace_page_size` | 100 | Traces fetched per page |
| `trace_pages` | 3 | Number of pages scanned (total traces = size × pages) |
| `min_prefix_chars` | 40 | Minimum prefix length for fallback matching |

Input rule:

- Provide at least one of `query` or `screenshot_base64`.
- If only screenshot is provided, OpenAI extracts text from user/agent bubbles when visible.
- Use `text_without_bubble_as` when the screenshot shows plain text without chat bubbles (e.g. a copied snippet or trace view); set to `"user"` or `"agent"` so OCR assigns the text to the correct field.

Screenshot example:

```bash
curl -X POST "http://localhost:8090/api/sessions/resolve" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SESSION_FINDER_API_KEY" \
  -H "X-Langfuse-Environment: dev" \
  -d '{
    "screenshot_base64": "'"$(base64 -w 0 /path/to/screenshot.png)"'",
    "screenshot_mime_type": "image/png",
    "trace_pages": 3,
    "trace_page_size": 100
  }'
```

Example response (`found`):

```json
{
  "status": "found",
  "confidence": 1.0,
  "extracted_query": "...",
  "session_id": "58d8f730-7530-4e48-8879-2105ec872f49",
  "session_created_at": "2026-06-27T17:48:21.195000Z",
  "trace_count": 4,
  "matched_on": {
    "field": "input",
    "snippet": "...",
    "observation_id": "05f87002ca84a2ae97e281d05b348193",
    "trace_id": "05f87002ca84a2ae97e281d05b348193",
    "timestamp": "2026-06-27T17:48:21.195000Z"
  },
  "candidates": [],
  "search_backend": "traces_v1",
  "message": "Resolved a matching Langfuse session via trace-based search."
}
```

Notes on response fields:

- `matched_on.observation_id` currently holds the **trace id** in trace-based mode.
- `candidates[].trace_count` = number of matching traces inside that session.
- `confidence` is `1.0` for single match, `0.5` when ambiguous.

---

## What to test now

Recommended test matrix for third-party/dev validation:

1. **Exact user query match** (short and long text).
2. **Exact assistant answer match** (partial and full paragraph).
3. **Over-pasted text** (paragraph + heading) to validate prefix fallback.
4. **Gujarati text** copied verbatim from trace IO.
5. **Screenshot-only request** (no query) with valid `OPENAI_API_KEY`.
6. **Screenshot MIME validation** (e.g. unsupported `image/gif` should fail).
7. **Ambiguous query** (generic phrase appearing in multiple sessions).
8. **Not found** query outside scan window.
9. **Dev vs prod** switch via `X-Langfuse-Environment`.
10. **Auth failures** (missing/invalid `X-API-Key`).

Suggested tuning experiments:

- Increase `trace_pages` / `trace_page_size` for deeper history.
- Increase `min_prefix_chars` to reduce ambiguous results.
- Add `from_timestamp` / `to_timestamp` for targeted date windows.

---

## Troubleshooting

### `401 Invalid or missing X-API-Key`

- Set `SESSION_FINDER_API_KEY` in `.env`.
- Send the same value in `X-API-Key` header.

### `500 SESSION_FINDER_API_KEY is not configured`

- Missing env var on server startup.

### `502 Session resolution failed ...`

- Usually Langfuse connectivity/auth/host mismatch.
- Verify keys belong to the selected environment host.
- If detail contains **`The read operation timed out`**, Langfuse trace fetch is too slow for the default timeout. Set `LANGFUSE_TIMEOUT_SECONDS=60` (or higher) in `.env` and restart the server. Also try reducing scan size (`trace_pages`, `trace_page_size`) or adding `from_timestamp`/`to_timestamp`.

### `not_found` for text you know exists

- Query may be outside scanned trace window (default 300 traces).
- Pasted text may not exist as one contiguous substring.
- Try:
  - longer prefix source text,
  - higher `trace_pages`,
  - `from_timestamp`/`to_timestamp`.

### Shell curl to Langfuse fails auth but app works

- Ensure shell loaded `.env`:

```bash
set -a
source .env
set +a
```

### Screenshot request fails with OpenAI error

- Ensure `OPENAI_API_KEY` is set on server.
- Ensure `screenshot_base64` is raw base64 (no `data:image/...;base64,` prefix).
- Ensure `screenshot_mime_type` is one of `image/png`, `image/jpeg`, `image/jpg`, `image/webp`.
- If OpenAI returns no usable text, retry with a clearer screenshot crop.

---

## Deterministic Langfuse capability probe

Use this to verify what your Langfuse host supports:

```bash
set -a
source .env
set +a

export LF_HOST="$LANGFUSE_DEV_BASE_URL"
export LF_PK="$LANGFUSE_DEV_PUBLIC_KEY"
export LF_SK="$LANGFUSE_DEV_SECRET_KEY"

curl -s -u "$LF_PK:$LF_SK" "$LF_HOST/api/public/projects" | jq

curl -s -o /tmp/v2.json -w "v2_observations: %{http_code}\n" \
  -u "$LF_PK:$LF_SK" \
  "$LF_HOST/api/public/v2/observations?limit=1&fields=core,basic,io"

curl -s -o /tmp/traces.json -w "traces: %{http_code}\n" \
  -u "$LF_PK:$LF_SK" \
  "$LF_HOST/api/public/traces?limit=1&fields=core,io"

curl -s -o /tmp/sessions.json -w "sessions: %{http_code}\n" \
  -u "$LF_PK:$LF_SK" \
  "$LF_HOST/api/public/sessions?limit=1"
```

Expected on self-hosted:

- `v2_observations: 501`
- `traces: 200`
- `sessions: 200`

---

## Roadmap

1. Return direct Langfuse session URL in response.
2. Optional debug mode exposing scan stats (traces scanned, prefixes attempted).
3. Optional session-id direct lookup shortcut when ID is pasted.
