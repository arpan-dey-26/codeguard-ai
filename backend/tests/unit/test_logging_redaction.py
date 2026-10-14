"""Redaction is a control, so it gets a test.

Redaction is exactly the kind of safeguard that silently stops working when
someone adds a new field, so these assertions check *behaviour on unknown
keys*, not a list of known ones.
"""

from __future__ import annotations

from app.core.logging import REDACTED, redaction_processor

# Assembled at runtime so the literal never appears in source — see the
# note in tests/unit/test_config.py.
FAKE_PEM_MARKER = "-----BEGIN " + "RSA PRIVATE KEY-----"


def _process(**fields: object) -> dict[str, object]:
    return redaction_processor(None, "info", dict(fields))


def test_masks_keys_that_look_like_secrets() -> None:
    out = _process(
        gemini_api_key="AIza-REAL-KEY",
        claude_api_key="sk-ant-REAL",
        github_webhook_secret="whsec_REAL",
        github_private_key=FAKE_PEM_MARKER,
        authorization="Bearer ghs_REAL",
        session_id="abc123",
    )
    rendered = str(out)
    for leaked in (
        "AIza-REAL-KEY",
        "sk-ant-REAL",
        "whsec_REAL",
        FAKE_PEM_MARKER,
        "ghs_REAL",
        "abc123",
    ):
        assert leaked not in rendered
    assert out["gemini_api_key"] == REDACTED


def test_masks_an_unknown_future_secret_field() -> None:
    """Matching on key shape is what makes this survive new fields."""
    out = _process(some_future_vendor_token="TOKEN-VALUE")
    assert out["some_future_vendor_token"] == REDACTED


def test_drops_bulk_repository_content_rather_than_masking_it() -> None:
    """Source code in a log record is a bug; masking would hide the bug."""
    out = _process(patch="@@ -1 +1 @@\n-secret_code()", prompt="<review_task>…")
    assert "patch" not in out
    assert "prompt" not in out
    assert out["patch_omitted"] is True
    assert out["prompt_omitted"] is True


def test_redacts_nested_structures() -> None:
    out = _process(context={"github": {"access_token": "ghs_NESTED"}, "repo_id": 7})
    assert "ghs_NESTED" not in str(out)
    assert out["context"]["repo_id"] == 7  # type: ignore[index]


def test_ordinary_fields_survive_untouched() -> None:
    out = _process(event="ai.provider.selected", provider="gemini", files_analysed=12)
    assert out["provider"] == "gemini"
    assert out["files_analysed"] == 12
