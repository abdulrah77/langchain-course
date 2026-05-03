"""
utils/response.py

Standardized API response envelope for all routers.
All responses follow: { success, message, data, request_id }
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse


def ok(
    data: Any = None,
    message: str = "OK",
    status_code: int = 200,
    request_id: Optional[str] = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": True,
            "message": message,
            "data": data,
            "request_id": request_id or str(uuid.uuid4()),
        },
    )


def err(
    message: str = "An error occurred",
    status_code: int = 400,
    details: Any = None,
    request_id: Optional[str] = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "message": message,
            "data": details,
            "request_id": request_id or str(uuid.uuid4()),
        },
    )


def api_response(
    *,
    success: bool,
    message: str,
    data: Any = None,
    status_code: int = 200,
) -> dict:
    """Return plain dict (for use in routers that return Pydantic/dict directly)."""
    return {
        "success": success,
        "message": message,
        "data": data,
        "request_id": str(uuid.uuid4()),
    }
