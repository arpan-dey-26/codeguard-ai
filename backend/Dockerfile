# CodeGuard AI backend image.
#
# Multi-stage, with a `development` target used by docker-compose and a
# `runtime` target for deployment. One image serves both the API and the
# worker; only the command differs (architecture.md §26.4).
#
# Phase 5 hardening, and what each change actually prevents:
#
#   * The base image is selected through a build argument so a deployment can
#     pin it **by digest**. `python:3.12-slim` is a moving target: the image
#     that passed CI is not necessarily the image that deploys an hour later,
#     which makes a build unreproducible and a supply-chain substitution
#     invisible. The default here is the tag, because a digest written into
#     this file goes stale and silently blocks security updates; pinning is the
#     deployment's job and the deployment guide says how.
#   * The runtime stage installs **runtime dependencies only**. It previously
#     installed `.[dev]`, so pytest, ruff and mypy — and their transitive
#     dependencies — shipped to production. Every one of those is attack
#     surface in exchange for nothing.
#   * The **development** stage also runs as non-root. It ran as root, and a
#     development container is exactly where an unreviewed dependency first
#     executes.
#   * `.dockerignore` keeps `.env`, `.git` and the test suite out of the build
#     context, so a secret cannot be copied into a layer by `COPY . .`.
#   * `--no-new-privileges` and a read-only root filesystem are set in
#     docker-compose and documented in the deployment guide; they are not
#     expressible in a Dockerfile.

# --- base --------------------------------------------------------------------
# Production builds pass a digest:
#
#     docker build \
#       --build-arg PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:<digest> ...
#
# Resolve the current digest with `docker buildx imagetools inspect
# python:3.12-slim-bookworm` and record it in the deployment's build
# configuration, where it can be reviewed and rotated.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE} AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# One user, created in the base stage so every downstream stage can use it.
# A fixed high uid so a host bind-mount's ownership is predictable.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin codeguard

# --- builder -----------------------------------------------------------------
# Builds a prefix tree that the later stages copy. Nothing from this stage's
# filesystem other than /install reaches a runtime image, so build tools,
# caches and headers stay here.
FROM base AS builder

COPY pyproject.toml ./
COPY app ./app
RUN pip install --prefix=/install "."

# --- builder-dev -------------------------------------------------------------
# The development toolchain, kept in its own stage so it is impossible for the
# runtime stage to pick it up by accident.
FROM base AS builder-dev

COPY pyproject.toml ./
COPY app ./app
RUN pip install --prefix=/install ".[dev]"

# --- development -------------------------------------------------------------
# Used by docker-compose. Source is bind-mounted over /app, so the code copied
# during the build is replaced at run time and --reload picks up host edits.
FROM base AS development

COPY --from=builder-dev /install /usr/local
COPY --chown=codeguard:codeguard . .

USER codeguard
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- runtime -----------------------------------------------------------------
FROM base AS runtime

# Runtime dependencies only. See the header: the development toolchain used to
# ship here and does not any more.
COPY --from=builder /install /usr/local
COPY --chown=codeguard:codeguard app ./app
COPY --chown=codeguard:codeguard alembic ./alembic
COPY --chown=codeguard:codeguard alembic.ini pyproject.toml ./

# Non-root. A container process that does not need root should not have it —
# it is the cheapest possible reduction of blast radius.
USER codeguard
EXPOSE 8000

# Readiness, not liveness: this must fail while the database is unreachable so
# the orchestrator stops routing traffic here, rather than restarting a
# perfectly healthy process (architecture.md §17.6).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3).status==200 else 1)"

# Migrations are NOT run here. They run as a release command before new
# instances take traffic; running them on startup means two booting instances
# race each other, and a failed migration becomes a crash loop instead of a
# visible deploy failure.
#
# No secret is baked into this image. Every credential arrives as an
# environment variable or a mounted secret at run time, which is what lets one
# image be promoted from staging to production unchanged.
#
# Deliberately NOT `--proxy-headers`: uvicorn's implementation trusts a list of
# proxy addresses, which are not knowable at build time in a container
# platform, and `--forwarded-allow-ips "*"` means trusting a header any client
# can send. `app.core.proxy.ProxyHeadersMiddleware` does the same job with a
# hop count instead — see TRUSTED_PROXY_COUNT.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
