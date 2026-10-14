# Sample webhook payloads

Hand-built fixtures shaped like real GitHub deliveries, reduced to the fields
CodeGuard AI actually reads (see `backend/app/github/payloads.py`).

They are fixtures, not captures: no real repository, account, installation or
identifier appears here. Everything is invented, so the files are safe to
commit and safe to publish.

To capture a real payload for debugging, copy it from the **Advanced** tab of
your GitHub App's settings page — and strip anything identifying before
committing it.

| File | Event | Action |
|---|---|---|
| `pull_request_opened.json` | `pull_request` | `opened` |
| `pull_request_synchronize.json` | `pull_request` | `synchronize` |
| `pull_request_draft.json` | `pull_request` | `opened` (draft — ignored) |
| `unsupported_event.json` | `issues` | `opened` (ignored) |
