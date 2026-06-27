# Session Resolver

Internal FastAPI service to resolve a Langfuse session from pasted query/answer text (and later screenshots).

## Current Langfuse capability notes (dev instance)

Validated on `https://langfuse.dev.amulai.in`:

- `v2_observations`: `501` (not available)
- `v1_observations`: `200`
- `traces`: `200`
- `sessions`: `200`

This means the resolver **cannot** rely on observations v2 on self-hosted right now.

## Resolver behavior

The resolver now uses **trace-based search only**:

1. Fetch recent traces (`fields=core,io`)
2. Match pasted text against `trace.input` and `trace.output` in API code
   - Full exact substring (case-insensitive) is attempted first
   - If no hit, longest-prefix fallback is used down to `min_prefix_chars`
3. Group by `sessionId`, rank, then fetch session details

Response includes `search_backend`:

- `traces_v1`

## Deterministic capability checks

From `session_resolver/`:

```bash
set -a
source .env
set +a

export LF_HOST="$LANGFUSE_DEV_BASE_URL"
export LF_PK="$LANGFUSE_DEV_PUBLIC_KEY"
export LF_SK="$LANGFUSE_DEV_SECRET_KEY"
```

Check auth:

```bash
curl -s -u "$LF_PK:$LF_SK" "$LF_HOST/api/public/projects" | jq
```

Probe endpoint support:

```bash
curl -s -o /tmp/v2.json -w "v2_observations: %{http_code}\n" \
  -u "$LF_PK:$LF_SK" \
  "$LF_HOST/api/public/v2/observations?limit=1&fields=core,basic,io"

curl -s -o /tmp/v1.json -w "v1_observations: %{http_code}\n" \
  -u "$LF_PK:$LF_SK" \
  "$LF_HOST/api/public/observations?limit=1"

curl -s -o /tmp/traces.json -w "traces: %{http_code}\n" \
  -u "$LF_PK:$LF_SK" \
  "$LF_HOST/api/public/traces?limit=1&fields=core,io"

curl -s -o /tmp/sessions.json -w "sessions: %{http_code}\n" \
  -u "$LF_PK:$LF_SK" \
  "$LF_HOST/api/public/sessions?limit=1"
```
