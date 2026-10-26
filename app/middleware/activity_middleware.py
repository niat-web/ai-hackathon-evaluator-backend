"""Record a signed-in request after the response is ready."""

from __future__ import annotations

import asyncio
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.services.activity_log_service import ActivityLogService, describe_operation, should_record
from app.utils.async_io import run_sync
from app.utils.time import now_ist_iso


logger = logging.getLogger(__name__)


class ActivityLogMiddleware(BaseHTTPMiddleware):
    """Writes one application log per signed-in action. Logging never fails the request."""

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        self._schedule(request, response.status_code, started)
        return response

    def _schedule(self, request: Request, status_code: int, started: float) -> None:
        path = request.url.path
        if not should_record(request.method, path):
            return
        actor = getattr(request.state, "activity_actor", None)
        if not isinstance(actor, dict) or not actor.get("user_id"):
            return
        area, operation = describe_operation(request.method, path)
        entry = {
            "created_at": now_ist_iso(),
            "user_id": actor.get("user_id") or "",
            "name": actor.get("name") or "",
            "email": actor.get("email") or "",
            "role": actor.get("role") or "",
            "method": request.method.upper(),
            "path": path,
            "area": area,
            "operation": operation,
            "status_code": int(status_code),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        asyncio.create_task(self._write(entry))

    @staticmethod
    async def _write(entry: dict) -> None:
        try:
            await run_sync(ActivityLogService().record, entry)
        except Exception:
            logger.warning("Application log was not saved", exc_info=True)
