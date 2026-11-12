"""Small per-process access and cost guard for the optional assistant."""

import ipaddress
import math
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import Request

from backend.core.exceptions import error_response
from backend.core.settings import Settings


class LLMPolicyError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: int = 0):
        self.status, self.code, self.message, self.retry_after = status, code, message, retry_after


class LLMPolicy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls: deque[float] = deque()
        self.active = 0

    def authorize(self, request: Request):
        settings = self.settings
        host = request.client.host if request.client else ""
        try:
            address = ipaddress.ip_address(host)
            local = address.is_loopback or (settings.environment == "development" and address.is_private)
        except ValueError:
            local = settings.environment == "test" and host == "testclient"
        token = settings.llm_access_token.get_secret_value()
        authorized = bool(token) and secrets.compare_digest(
            request.headers.get("authorization", "").encode("utf-8"), ("Bearer " + token).encode("utf-8")
        )
        if (
            (settings.environment == "production" and not token)
            or (token and not authorized)
            or (not local and not (settings.llm_allow_remote and authorized))
        ):
            raise LLMPolicyError(403, "LLM_ACCESS_DENIED", "Доступ к ассистенту ограничен настройками сервера.")

    @asynccontextmanager
    async def admit(self, request: Request):
        self.authorize(request)
        settings = self.settings
        now = time.monotonic()
        while self.calls and self.calls[0] <= now - 60:
            self.calls.popleft()
        if len(self.calls) >= settings.llm_requests_per_minute:
            raise LLMPolicyError(
                429,
                "LLM_RATE_LIMITED",
                "Лимит запросов к ассистенту исчерпан. Повторите позже.",
                max(1, math.ceil(60 - (now - self.calls[0]))),
            )
        if self.active >= settings.llm_max_concurrent_requests:
            raise LLMPolicyError(429, "LLM_BUSY", "Ассистент занят. Повторите запрос позже.", 1)
        # No await between checking and acquiring: atomic on this app's event loop.
        self.calls.append(now)
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


async def assistant_access(request: Request):
    if request.app.state.llm_use_case is None:
        # Preserve the optional-service contract (503) when there is no
        # provider to protect, including production without credentials.
        yield
        return
    async with request.app.state.llm_policy.admit(request):
        yield


async def policy_error_response(_request: Request, exc: LLMPolicyError):
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return error_response(exc.status, exc.code, exc.message, headers=headers)
