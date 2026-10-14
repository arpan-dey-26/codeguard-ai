#!/usr/bin/env python3
"""Offline webhook replay harness.

Signs a captured GitHub payload with the configured webhook secret and POSTs it
at a running CodeGuard AI instance, exercising the full ingress path with no
GitHub, no tunnel and no network beyond localhost.

Why this exists — three reasons, all of which pay back immediately:

1.  **Development.** Waiting for a real pull request to test a code change is
    a minute-long feedback loop. This is a second.
2.  **Determinism.** The same bytes every time means a failure is reproducible.
3.  **Demo reliability.** A system that works perfectly and cannot be shown
    working scores as a system that does not work. This runs with the venue
    Wi-Fi off (architecture.md §28.2, R17).

**It does not bypass any security check.** It imports
``app.github.signature.compute_signature`` — the same module the server
verifies with — so a replayed delivery is signed by exactly the code that
checks it. A harness with its own signing implementation can drift and mask a
real bug, which defeats the point.

The secret is read from the environment. There is no default and no fallback:
a hard-coded secret in a repository is a secret that has been published.

Usage
-----
    # a pull_request.opened delivery
    python infrastructure/scripts/replay_webhook.py \
        --payload infrastructure/scripts/sample_payloads/pull_request_opened.json

    # replay the same delivery id twice to prove idempotency
    python infrastructure/scripts/replay_webhook.py --payload <file> --delivery-id fixed-1
    python infrastructure/scripts/replay_webhook.py --payload <file> --delivery-id fixed-1

    # prove the endpoint rejects a tampered body
    python infrastructure/scripts/replay_webhook.py --payload <file> --corrupt-signature
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

# Import from the application rather than reimplementing. The harness must use
# production's signing code or it is testing something else.
_BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(_BACKEND))

from app.github.signature import SIGNATURE_HEADER, compute_signature  # noqa: E402

DEFAULT_URL = "http://localhost:8000/api/v1/webhooks/github"


def _load_payload(path: Path) -> bytes:
    """Read the payload as raw bytes, preserving them exactly.

    Deliberately *not* ``json.load`` followed by ``json.dumps``: that changes
    whitespace and key order, which changes the bytes, which changes the HMAC.
    Whatever is on disk is what gets signed and sent — exactly as GitHub does.
    """
    raw = path.read_bytes()
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a GitHub webhook locally.")
    parser.add_argument("--payload", required=True, type=Path, help="Path to a JSON payload")
    parser.add_argument("--event", default=None, help="X-GitHub-Event (default: infer)")
    parser.add_argument("--delivery-id", default=None, help="X-GitHub-Delivery (default: random)")
    parser.add_argument("--url", default=os.environ.get("REPLAY_URL", DEFAULT_URL))
    parser.add_argument(
        "--secret",
        default=os.environ.get("GITHUB_WEBHOOK_SECRET"),
        help="Webhook secret. Defaults to $GITHUB_WEBHOOK_SECRET. Never hard-coded.",
    )
    parser.add_argument(
        "--corrupt-signature",
        action="store_true",
        help="Send a deliberately wrong signature, to prove the endpoint rejects it.",
    )
    parser.add_argument(
        "--omit-signature",
        action="store_true",
        help="Send no signature header at all.",
    )
    parser.add_argument("--print-only", action="store_true", help="Show the request, send nothing")
    args = parser.parse_args()

    if not args.secret and not args.omit_signature:
        parser.error(
            "No webhook secret. Set GITHUB_WEBHOOK_SECRET or pass --secret.\n"
            "The harness has no default: a hard-coded secret is a published secret."
        )

    body = _load_payload(args.payload)
    payload = json.loads(body)

    # Infer the event name from the payload shape when not given, so the
    # common case needs no flag and cannot be mislabelled by a typo.
    event = args.event or ("pull_request" if "pull_request" in payload else "ping")
    delivery_id = args.delivery_id or str(uuid.uuid4())

    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id,
        "User-Agent": "GitHub-Hookshot/replay",
    }
    if not args.omit_signature:
        signature = compute_signature(args.secret, body)
        if args.corrupt_signature:
            # Flip one hex digit: the body is authentic-looking but the HMAC is
            # wrong, which is exactly the tampering case that must be rejected.
            signature = signature[:-1] + ("0" if signature[-1] != "0" else "1")
        headers[SIGNATURE_HEADER] = signature

    print(f"POST {args.url}")
    print(f"  event       : {event}")
    print(f"  delivery id : {delivery_id}")
    print(f"  body bytes  : {len(body)}")
    print(f"  signature   : {'omitted' if args.omit_signature else 'present'}"
          f"{' (deliberately corrupted)' if args.corrupt_signature else ''}")

    if args.print_only:
        return 0

    try:
        import httpx
    except ImportError:  # pragma: no cover
        raise SystemExit("httpx is required: pip install -e '.[dev]' in backend/") from None

    response = httpx.post(args.url, content=body, headers=headers, timeout=15.0)
    print(f"\n<- {response.status_code}")
    print(response.text or "(empty body)")

    # 2xx means the delivery was accepted or recognised as a duplicate; both
    # are correct outcomes. Anything else is a failure worth a non-zero exit so
    # the harness is usable from a shell script.
    return 0 if 200 <= response.status_code < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
