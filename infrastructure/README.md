# Infrastructure

Deployment configuration. Empty until Phase 5, deliberately.

`architecture.md` §26 specifies the target: a PaaS (Render / Railway / Fly)
running the API and worker from one image, managed PostgreSQL, and the SPA on
a static CDN host. **No Kubernetes** — the workload is two processes and a
database, and an orchestrator for that is résumé-driven design.

Planned contents:

- `render.yaml` (or `fly.toml` / `railway.json`) — service definitions
- `scripts/` — the webhook replay harness (Phase 1), secret rotation helpers

The webhook replay harness is worth building early: it POSTs captured webhook
payloads at a correctly computed signature, which makes the whole pipeline
developable and demoable with no network and no GitHub at all.
