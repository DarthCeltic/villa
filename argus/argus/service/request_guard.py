"""Request guards for the read-only observe service."""
from __future__ import annotations

import asyncio
import os
import pathlib
import re
import time

DEFAULT_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "observe", "testserver"})
HOSTS_ENV = "ARGUS_ALLOWED_HOSTS"
OWN_SITE_VALUES = frozenset({"same-origin", "none"})
ACTIVE_CONTENT_SUFFIXES = frozenset({".html", ".htm", ".xhtml", ".svg", ".xml", ".xsl", ".js", ".mjs"})
FILE_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"}


def allowed_hosts(environ=None) -> frozenset:
    env = os.environ if environ is None else environ
    extra = {h.strip().lower() for h in (env.get(HOSTS_ENV) or "").split(",") if h.strip()}
    return DEFAULT_HOSTS | extra


_HOST_PORT = re.compile(r"(?P<host>\[[0-9a-f:.]+\]|[^\[\]:@/\\ ]+)(?::(?P<port>[0-9]{1,5}))?")


def host_name(header: str) -> str:
    """The host part of a Host header, lower-cased, without a port and without IPv6 brackets."""
    h = (header or "").strip().lower()
    if h.count(":") > 1 and not h.startswith("["):
        return h if re.fullmatch(r"[0-9a-f:]+", h) else ""
    m = _HOST_PORT.fullmatch(h)
    if not m:
        return ""
    host = m.group("host")
    return host[1:-1] if host.startswith("[") else host


def host_ok(header, allowed=None) -> bool:
    """An absent Host header (HTTP/1.0, some probes) is not a browser and is allowed; a present one must be ours."""
    if header is None:
        return True
    return host_name(header) in (allowed if allowed is not None else allowed_hosts())


def fetch_site_ok(value) -> bool:
    """Only the service's own pages (`same-origin`), a person typing the address (`none`) or a non-browser (no header) may call it."""
    return value is None or str(value).strip().lower() in OWN_SITE_VALUES


def redact_home(text: str) -> str:
    """Replace the operator's home directory with `~` so a path shown to a screen or a screenshot does not name the operator."""
    if not isinstance(text, str) or not text:
        return text
    home = str(pathlib.Path.home())
    for form in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
        if form and len(form) > 3:
            text = text.replace(form, "~")
    return text


def file_headers(name: str, media_type: str | None = None) -> dict:
    """Headers for a file served from the artifact roots: never sniffed, never scripted, and downloaded rather than rendered when it could run code."""
    headers = dict(FILE_HEADERS)
    lowered = name.lower()
    if any(lowered.endswith(s) for s in ACTIVE_CONTENT_SUFFIXES) or (media_type or "").startswith(("text/html", "application/xhtml", "image/svg", "text/xml", "application/xml")):
        safe = "".join(c for c in os.path.basename(name) if c.isalnum() or c in "._- ") or "download"
        headers["Content-Disposition"] = 'attachment; filename="%s"' % safe
    return headers


class BodyLimit:
    """Refuse a request body over `max_bytes` whether it is declared (`Content-Length`) or streamed (chunked), and one that stops arriving, before the application reads more than the limit."""

    class _TooLarge(BaseException):
        """Not an Exception: the framework turns any Exception raised while it reads a body into a 400, which would hide the 413."""

    class _TooSlow(BaseException):
        """The body stopped arriving for longer than the idle limit."""

    IDLE_S = 30.0
    TOTAL_S = 120.0

    def __init__(self, app, max_bytes: int, idle_s: float | None = None, total_s: float | None = None):
        self.app, self.max_bytes = app, int(max_bytes)
        self.idle_s = self.IDLE_S if idle_s is None else float(idle_s)
        self.total_s = self.TOTAL_S if total_s is None else float(total_s)

    async def _refuse(self, send, status: int = 413) -> None:
        if status == 408:
            body = b'{"error":"request body stalled","idle_limit_s":%d}' % int(self.idle_s)
        else:
            body = b'{"error":"request body too large","limit_bytes":%d}' % self.max_bytes
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _verdict(exc: BaseException):
        """413, 408, or None: our own signal, possibly wrapped in an exception group by a task group between here and the reader."""
        if isinstance(exc, BodyLimit._TooLarge):
            return 413
        if isinstance(exc, BodyLimit._TooSlow):
            return 408
        if isinstance(exc, BaseExceptionGroup):
            found = {BodyLimit._verdict(e) for e in exc.exceptions}
            if found and None not in found:
                return 413 if 413 in found else 408
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > self.max_bytes
            except ValueError:
                too_big = True
            if too_big:
                await self._refuse(send)
                return
        seen, started, body_done = 0, False, False
        deadline = time.monotonic() + self.total_s

        async def limited_receive():
            nonlocal seen, body_done
            if body_done:
                return await receive()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BodyLimit._TooSlow()
            try:
                message = await asyncio.wait_for(receive(), timeout=min(self.idle_s, remaining))
            except asyncio.TimeoutError:
                raise BodyLimit._TooSlow() from None
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise BodyLimit._TooLarge()
                if not message.get("more_body"):
                    body_done = True
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BaseException as exc:
            status = self._verdict(exc)
            if status is None:
                raise
            if not started:
                await self._refuse(send, status)
