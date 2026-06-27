from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, status
from langfuse import Langfuse

from app.config import settings

LangfuseEnvironment = Literal["dev", "prod"]


@dataclass(frozen=True)
class LangfuseCredentials:
    environment: LangfuseEnvironment
    public_key: str
    secret_key: str
    base_url: str


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _mask_public_key(public_key: str) -> str:
    if len(public_key) <= 10:
        return "***"
    return f"{public_key[:8]}...{public_key[-4:]}"


def resolve_langfuse_credentials(environment: str | None) -> LangfuseCredentials:
    env = _clean(environment).lower() or "dev"

    if env not in {"dev", "prod"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Langfuse-Environment must be either 'dev' or 'prod'.",
        )

    if env == "prod":
        public_key = _clean(settings.langfuse_prod_public_key)
        secret_key = _clean(settings.langfuse_prod_secret_key)
        base_url = _clean(settings.langfuse_prod_base_url) or "https://cloud.langfuse.com"
    else:
        public_key = _clean(settings.langfuse_dev_public_key)
        secret_key = _clean(settings.langfuse_dev_secret_key)
        base_url = _clean(settings.langfuse_dev_base_url) or "https://cloud.langfuse.com"

    if not public_key or not secret_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Langfuse {env} credentials are not configured on the server.",
        )

    return LangfuseCredentials(
        environment=env,  # type: ignore[arg-type]
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
    )


def build_langfuse_client(credentials: LangfuseCredentials) -> Langfuse:
    return Langfuse(
        public_key=credentials.public_key,
        secret_key=credentials.secret_key,
        base_url=credentials.base_url,
    )


def langfuse_connection_info(credentials: LangfuseCredentials) -> dict:
    return {
        "environment": credentials.environment,
        "base_url": credentials.base_url,
        "public_key": _mask_public_key(credentials.public_key),
    }
