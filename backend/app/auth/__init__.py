"""Authentication: sessions, CSRF, cookies and the GitHub OAuth flow.

Split by concern so each piece is testable on its own:

``tokens``    pure functions — opaque tokens, hashing, CSRF derivation
``cookies``   one place where cookie attributes are decided
``oauth``     the GitHub conversation, and nothing else

The route handlers live in ``app.api.v1.auth`` and the request-time enforcement
in ``app.api.authn``; neither of those belongs here, because this package must
stay importable by the worker, which has no web framework.
"""
