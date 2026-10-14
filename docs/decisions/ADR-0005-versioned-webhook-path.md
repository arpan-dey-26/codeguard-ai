# ADR-0005 — Mount the webhook receiver at `/api/v1/webhooks/github`

- **Status:** Accepted · **Date:** 2026-09-15 · **Phase:** 1
- **Amends:** `architecture.md` §17.1

## Context

The baseline specifies `POST /webhooks/github`, outside the versioned API,
on the reasoning that the webhook surface has a different authentication model
(HMAC rather than a session) and a different threat model from the dashboard.

Both remain true. But the webhook payload shape and our acknowledgement
contract can still change, and an unversioned path gives no way to run two
behaviours during a transition.

## Decision

Mount the receiver at `POST /api/v1/webhooks/github`.

No alias at the unversioned path. A second unauthenticated public route is
attack surface for no benefit, and a GitHub App has exactly one webhook URL,
so there is no migration to stage.

## Consequences

- One public path, versioned, changeable without a lockstep deploy.
- The route lives under the versioned router but does **not** share the
  dashboard's dependency chain: it authenticates by signature, and that
  difference is documented at the mount point.
- The GitHub App's webhook URL must use this path. `docs/setup.md` says so.

## Alternatives rejected

- **Keep `/webhooks/github`.** Rejected: no versioning story.
- **Serve both.** Rejected: doubles the unauthenticated surface to solve a
  migration problem that a single-URL App cannot have.
