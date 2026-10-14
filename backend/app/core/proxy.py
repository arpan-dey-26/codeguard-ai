"""Correcting the client address and scheme behind a reverse proxy.

Uvicorn ships ``--proxy-headers``, and it is not used here for one reason: its
trust model is a list of proxy *addresses*, which in a container platform are
allocated dynamically and are not known when the process starts. The model used
instead is a **hop count** — "there are N proxies in front of me" — which is
static, knowable, and the thing an operator actually knows about their topology.

The problem being solved is specific. ``X-Forwarded-For`` is a list that each
hop appends to, and **any client can send one**. Reading the first entry — the
obvious thing to do, since that is "the original client" — reads whatever the
client wrote there, which makes rate limiting by address trivially bypassable
and session records fiction. The correct entry is the **Nth from the right**,
where N is the number of proxies you actually trust: everything to the left of
that was supplied by something upstream of your own infrastructure.

``TRUSTED_PROXY_COUNT = 0`` disables the whole thing, and is the default. With
nothing in front, the TCP peer is the client and the headers are noise at best.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_HEADER_ENTRIES = 32


class ProxyHeadersMiddleware:
    """Rewrite ``scope['client']`` and ``scope['scheme']`` from trusted hops.

    Pure ASGI: it mutates the scope before anything builds a ``Request`` from
    it, so every consumer downstream — routing, logging, rate limiting, the
    session's IP hash — sees one consistent answer rather than each deciding
    for itself which header to believe.
    """

    def __init__(self, app: Any, *, trusted_proxy_count: int = 0) -> None:
        self.app = app
        self._hops = max(0, int(trusted_proxy_count))

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if self._hops == 0 or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", ())
        }

        client_host = self._client_from(headers.get("x-forwarded-for"))
        if client_host:
            port = scope["client"][1] if scope.get("client") else 0
            scope["client"] = (client_host, port)

        scheme = self._scheme_from(headers.get("x-forwarded-proto"))
        if scheme:
            scope["scheme"] = scheme

        await self.app(scope, receive, send)

    def _client_from(self, header: str | None) -> str | None:
        """The address contributed by the outermost proxy we trust.

        Counted from the **right**, because the rightmost entry was written by
        the hop closest to us and each step left is one hop further out. Taking
        ``entries[0]`` instead — the intuitive "original client" — takes the
        value the client itself supplied.
        """
        if not header:
            return None
        entries = [part.strip() for part in header.split(",")][-_MAX_HEADER_ENTRIES:]
        entries = [e for e in entries if e]
        if not entries:
            return None
        index = len(entries) - self._hops
        if index < 0:
            # Fewer entries than we expect hops: either the request did not come
            # through the full chain, or somebody stripped the header. Trusting
            # the leftmost value here is the exact mistake this class exists to
            # avoid, so nothing is trusted and the TCP peer stands.
            logger.warning(
                "proxy.forwarded_for.too_short",
                entries=len(entries),
                expected_hops=self._hops,
            )
            return None
        return entries[index]

    def _scheme_from(self, header: str | None) -> str | None:
        if not header:
            return None
        # Same counting rule as the address, then a strict allow-list: the value
        # decides whether the application believes it is on HTTPS.
        entries = [part.strip().lower() for part in header.split(",") if part.strip()]
        if not entries:
            return None
        index = len(entries) - self._hops
        if index < 0:
            return None
        scheme = entries[index]
        return scheme if scheme in ("http", "https") else None


__all__ = ["ProxyHeadersMiddleware"]
