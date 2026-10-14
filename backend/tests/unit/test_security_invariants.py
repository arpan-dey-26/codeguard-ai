"""Phase 1 security invariants, asserted rather than assumed.

Each test here corresponds to a line in the threat model (architecture.md
§21.1). They are cheap, they run without a database, and they fail loudly if
someone later removes the control they protect.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from app.core.config import AppEnv, Settings
from app.core.logging import REDACTED, redaction_processor
from app.github.app_auth import InstallationToken

APP_DIR = pathlib.Path(__file__).resolve().parents[2] / "app"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": AppEnv.TEST,
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


# -- secrets never leave the process -------------------------------------


def test_github_secrets_are_not_in_the_safe_summary() -> None:
    settings = _settings(
        github_app_id="123456",
        github_private_key="-----BEGIN " + "PRIVATE KEY-----\nsecret\n",
        github_webhook_secret="whsec_REAL_SECRET",
    )
    rendered = str(settings.safe_summary())
    assert "whsec_REAL_SECRET" not in rendered
    assert "PRIVATE KEY" not in rendered
    # Booleans *about* secrets are useful and safe.
    assert settings.safe_summary()["github_webhook_secret_configured"] is True


def test_installation_tokens_are_redacted_by_the_logger() -> None:
    out = redaction_processor(
        None,
        "info",
        {
            "event": "github.request",
            "installation_token": "ghs_REAL",
            "authorization": "Bearer ghs_REAL",
        },
    )
    assert "ghs_REAL" not in str(out)
    assert out["installation_token"] == REDACTED


def test_webhook_secret_and_signature_are_redacted_by_the_logger() -> None:
    out = redaction_processor(
        None,
        "info",
        {"github_webhook_secret": "whsec_REAL", "signature": "sha256=abc", "x": 1},
    )
    assert "whsec_REAL" not in str(out)
    assert "sha256=abc" not in str(out)
    assert out["x"] == 1


def test_webhook_payloads_are_never_logged() -> None:
    """Payloads carry PR titles, bodies and branch names — third-party content."""
    out = redaction_processor(
        None,
        "info",
        {"payload": {"pull_request": {"title": "secret project name"}}, "raw_body": b"..."},
    )
    assert "secret project name" not in str(out)
    assert out["payload_omitted"] is True
    assert out["raw_body_omitted"] is True


def test_installation_token_repr_is_safe() -> None:
    from datetime import UTC, datetime, timedelta

    token = InstallationToken("ghs_LEAK_ME", datetime.now(UTC) + timedelta(hours=1))
    assert "ghs_LEAK_ME" not in f"{token!r} {token!s} {token}"


# -- source-level invariants ---------------------------------------------


def test_no_module_executes_repository_content() -> None:
    """Absolute rule: nothing from a repository is ever run."""
    banned = {"exec", "eval", "compile"}
    offenders: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in banned
            ):
                offenders.append(f"{path.name}:{node.lineno} {node.func.id}()")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"subprocess", "pickle", "marshal", "shelve"}:
                        offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
            if isinstance(node, ast.ImportFrom) and node.module in {
                "subprocess",
                "pickle",
                "marshal",
            }:
                offenders.append(f"{path.name}:{node.lineno} from {node.module}")
    assert offenders == [], f"Execution primitives found: {offenders}"


def test_no_ai_sdk_is_imported_anywhere() -> None:
    """Phase 1 contains zero external AI calls, and this proves it structurally."""
    ai_modules = {"google", "google.generativeai", "google.genai", "anthropic", "openai"}
    offenders: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.name}: {a.name}"
                    for a in node.names
                    if a.name.split(".")[0] in ai_modules
                ]
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] in ai_modules
            ):
                offenders.append(f"{path.name}: {node.module}")
    assert offenders == [], f"AI SDK imports found: {offenders}"


def test_no_ai_dependency_is_declared() -> None:
    pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text().lower()
    for package in ("google-genai", "google-generativeai", "anthropic", "openai"):
        assert package not in pyproject


def test_signature_verification_uses_the_raw_body() -> None:
    """The endpoint must read request.body(), never a parsed-then-dumped object."""
    source = (APP_DIR / "api" / "v1" / "webhooks.py").read_text()
    assert "await request.body()" in source
    body_index = source.index("await request.body()")
    verify_index = source.index("verify_signature(")
    parse_index = source.index("json.loads(raw_body)")
    assert body_index < verify_index < parse_index, (
        "Order must be: read raw bytes -> verify -> parse"
    )


def test_the_size_guard_runs_before_signature_verification() -> None:
    """An oversized body must cost us a length check, not an HMAC."""
    source = (APP_DIR / "api" / "v1" / "webhooks.py").read_text()
    assert source.index("github_webhook_max_body_bytes") < source.index("verify_signature(")


# Three packages may own an HTTP client, and only three. Each is a boundary
# with its own auth, retry policy and error classification. A fourth client
# anywhere else means those rules have been re-implemented, which is how one
# call site quietly stops honouring Retry-After or starts retrying a POST.
#
#   app/github        the GitHub REST API, authenticated as the *App*
#   app/ai/providers  AI vendors
#   app/auth          GitHub's OAuth endpoints, authenticating a *person*
#
# `app/auth` was added in Phase 5 and is a genuinely separate upstream: it
# talks to github.com (not api.github.com), it authenticates with an OAuth
# client secret rather than an App private key, and its failures map to
# different user-visible outcomes. Routing it through `app/github` would mean
# that client holding two unrelated credential types and two retry policies —
# which is the coupling this invariant exists to prevent, not an example of it.
# `test_the_oauth_client_authenticates_people_not_the_app` below keeps the
# widening from becoming a hole.
_HTTP_OWNING_PACKAGES = (("github",), ("ai", "providers"), ("auth",))


def _owns_an_http_client(path: pathlib.Path) -> bool:
    parts = path.relative_to(APP_DIR).parts[:-1]
    return any(parts[-len(pkg) :] == pkg for pkg in _HTTP_OWNING_PACKAGES if len(parts) >= len(pkg))


def test_no_raw_httpx_calls_outside_the_client_packages() -> None:
    """Every vendor quirk belongs in one client, not scattered across services.

    Widened in Phase 3 to admit `app/ai/providers`, which legitimately owns the
    Gemini transport. The invariant is unchanged in substance: exactly one place
    per upstream, and nowhere else.
    """
    offenders: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        if _owns_an_http_client(path):
            continue
        source = path.read_text()
        if "httpx.AsyncClient(" in source or "httpx.get(" in source or "httpx.post(" in source:
            offenders.append(str(path.relative_to(APP_DIR)))
    assert offenders == [], f"Raw httpx usage outside the client packages: {offenders}"


def test_the_oauth_client_authenticates_people_not_the_app() -> None:
    """The Phase 5 widening must not become a second path to repository data.

    `app/auth` is allowed its own HTTP client so it can complete an OAuth flow.
    It must not acquire the App's credentials while it is there: an OAuth
    module holding the App private key could mint installation tokens, which
    would put an unaudited second route to every repository behind the login
    endpoint.
    """
    for path in (APP_DIR / "auth").rglob("*.py"):
        source = path.read_text()
        assert "github_private_key" not in source, f"{path.name} reaches for the App private key"
        assert "app.github.app_auth" not in source, f"{path.name} imports the App authenticator"
        assert "get_installation_token" not in source, f"{path.name} mints installation tokens"


def test_the_oauth_client_never_persists_a_user_token() -> None:
    """The user access token is used for two calls and dropped.

    It is never written to the database, a cookie, a log or a response, because
    the application has no offline use for it — all repository work uses
    installation tokens. Retaining it would be storing a credential to enable
    nothing.
    """
    source = (APP_DIR / "auth" / "oauth.py").read_text()
    for forbidden in ("INSERT", "set_cookie", "session.add", "access_token=", "token_hash"):
        assert forbidden not in source, (
            f"oauth.py appears to persist the user token via {forbidden!r}"
        )


def test_the_ai_provider_never_talks_to_github() -> None:
    """The widened rule above must not become a hole.

    An AI provider that could reach GitHub would put an unaudited second path
    to the pull request behind a component whose output is untrusted by design.
    """
    for path in (APP_DIR / "ai").rglob("*.py"):
        source = path.read_text()
        assert "api.github.com" not in source
        assert "app.github" not in source, f"{path.name} imports the GitHub client"


def test_the_replay_harness_reuses_production_signing() -> None:
    """A harness with its own signing code can drift and mask a real bug."""
    source = (REPO_ROOT / "infrastructure" / "scripts" / "replay_webhook.py").read_text()
    assert "from app.github.signature import" in source
    assert "hmac.new" not in source, "The harness must not reimplement signing"


def test_the_replay_harness_has_no_default_secret() -> None:
    source = (REPO_ROOT / "infrastructure" / "scripts" / "replay_webhook.py").read_text()
    assert 'os.environ.get("GITHUB_WEBHOOK_SECRET")' in source
    assert "whsec_" not in source


def test_no_repository_cloning() -> None:
    """Phase 1 reads metadata. It does not clone, and it does not run git."""
    for path in APP_DIR.rglob("*.py"):
        source = path.read_text()
        assert "git clone" not in source
        assert "GitPython" not in source


@pytest.mark.parametrize(
    "field", ["github_private_key", "github_webhook_secret", "gemini_api_key", "claude_api_key"]
)
def test_all_credential_fields_are_secret_typed(field: str) -> None:
    """SecretStr means an accidental f-string prints asterisks, not the value."""
    from pydantic import SecretStr

    annotation = Settings.model_fields[field].annotation
    assert SecretStr in getattr(annotation, "__args__", (annotation,))
