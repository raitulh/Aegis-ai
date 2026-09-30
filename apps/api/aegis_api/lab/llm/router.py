"""HTTP API for llm (placeholder router — replaced by the context implementation)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["Models"])
