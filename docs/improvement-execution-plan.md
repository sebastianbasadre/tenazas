# OpenClaw/Tenazas Improvement Execution Plan

This document tracks the implementation path for the hardening and maintainability phases.

## Phase 0: Baseline

- Use `pyproject.toml` for local Python metadata and test discovery.
- Use `requirements.txt` and `requirements-dev.txt` for reproducible local installs.
- Keep `tests/run_tests.sh` as the container-compatible runner, but make it portable across host paths.

## Phase 1: Control Plane Hardening

- Job runner defaults to localhost.
- Job runner requires an operator-provided token.
- n8n and OpenClaw share the token only through environment variables.
- Docker socket access remains a privileged dependency and should stay isolated behind allowlisted runtime APIs.

## Phase 2: Secrets And State

- Keep `.env`, OAuth tokens, credentials, SSH keys, runtime logs, n8n data, and generated datasets out of git.
- Use `.env.example` and `openclaw.example.json` as templates.
- Rotate any credential that was ever committed or exposed in local logs.

## Phase 3: Reproducible Deployments

- Pin OpenClaw npm version in the Docker build.
- Configure Prometheus, Grafana, and SearXNG image tags in `.env` rather than using implicit `latest` in compose.

## Phase 4: Runtime Boundaries

- Treat `workspace.runtime` as the importable package.
- Move config validation into a central module.
- Break up large routers/executors in compatibility-preserving steps.

## Phase 5: Typed Config

- Validate operator config at startup and in tests.
- Prefer env var overrides for host-specific infrastructure settings.

## Phase 6: Supervision

- Keep startup shell behavior compatible, but move token/config parsing into runtime helpers.
- Future work: split long-running services into first-class compose services or a process supervisor.

## Phase 7: Docs

- Keep runbooks separate from developer setup.
- Update test counts and architecture notes when suites are added or removed.
