# Iris-Service-Python — Claude Instructions

## Git Safety
- NEVER `git reset --hard` without explicit user confirmation. Run `git status` + check unpushed commits first.
- Before pushing : verify current branch (`dev` not `main`) + `git fetch` + check `HEAD..origin/<branch>` ; pull rebase if behind. See `~/.claude/CLAUDE.md` → "Git Workflow".

## CI/CD Scope
- **GitLab CI exclusively** — do NOT modify `.github/workflows/*` unless explicitly requested.
- When fixing a failing pipeline, read the **actual failure log** (`glab ci trace <job>`) before exploring the whole CI config.

## Project Verification
- State explicitly at the start of each response : (1) which repo (iris-service-python), (2) current branch, (3) remote state.
- When resuming mid-session, `git fetch` + `glab mr list` + `glab ci list` before editing.

## Verify commands before suggesting
- Before suggesting any CLI flag, run `<cmd> --help | grep <flag>` to confirm. If unsure, say **"I'm not sure this exists"**. See `~/.claude/CLAUDE.md` → "Verify commands before suggesting them".

## CI failures : surgical fixes, not `allow_failure` bypasses
NEVER reach for `allow_failure: true` as the fix. Pick (a) fix the root cause, (b) tag-gate the test (pytest `-m` markers + CI marker exclusion), or (c) scope-out via CI `rules: when: never`. Always explain in the commit message.

## Persistent task backlog
**`TASKS.md`** (at the repo root) is the source of truth for pending work across sessions.
- Read it at the start of every session — before doing anything else.
- Update it immediately when a task is added/started/completed.
- This file survives context window resets ; the conversation history does not.
- When all tasks are done : delete `TASKS.md` and commit. No empty file.
- When new tasks arrive : recreate `TASKS.md` from scratch.

## Claude workflow rules (apply to every session)

- **Start every response with the current time** in `HH:MM` format (no timezone). Run `date "+%H:%M"` if uncertain.
- **Do not stop** between tasks — chain all pending work without asking "shall I continue?".
- **Never go silent** : when no background work is in flight, say `⏸  Idle. No background work.` then re-list pending tasks and restart them. Same when polling — explicit "waiting for X, next check at Y".
- **Regularly display the pending task list** — after completing a task, show what remains.
- **Act directly** — read only what is strictly necessary, then make the change.
- **One commit per logical change** — do not batch unrelated fixes.
- **Run the build after every change** (`uv run pytest` + `uv run ruff check` + `uv run mypy`) and fix errors before committing. Build must have zero warnings.
- **Comments explain why**, not what. Write comments that a future Claude session with no conversation history can understand.
- After significant feature work, **do a code review pass** : unused imports, `Any` types, silent error handlers, missing types on async calls, missing tests.
- **Never modify files outside this project** unless explicitly asked.
- **Reference pipelines/MRs/files as clickable URLs.** When a status update or commit message mentions an MR, pipeline, tag, ADR or audit report, emit it as a markdown link (`[!62](https://gitlab.com/iris-7/iris-service-python/-/merge_requests/62)`, `[#308](...)`, `[stable-v0.1.0](...)`, `[ADR](file:///<repo>/docs/adr/…md)`).

## Submodule pattern (2-tier flat α — see common ADR-0060)

This repo has **2 git submodules** (since 2026-04-26 split) :

- `infra/common/` → [iris-common](https://gitlab.com/iris-7/iris-common) — universal cross-repo conventions (release scripts, ADR drift tooling, Conventional Commits CI template, Renovate base). Consumed by all 4 iris-7 repos including UI.
- `infra/shared/` → [iris-service-shared](https://gitlab.com/iris-7/iris-service-shared) — backend infrastructure (clusters, terraform, K8s, OTel collector, postgres+kafka+redis dev stack, dashboards-lgtm, backend ADRs). Consumed by java + python only (NOT ui).

**Pattern α (flat 2-submodule)** chosen over β for : independent SHA pinning per consumer, symmetric path everywhere (`infra/common/bin/...`), standard clone (no `--recursive`). Full rationale : [common ADR-0060](https://gitlab.com/iris-7/iris-common/-/blob/main/docs/adr/0060-flat-vs-transitive-submodule-inheritance.md).

**Where to find what** :
- Universal scripts (pre-sync, changelog, gitlab-release, regen-adr-index) → `infra/common/bin/...`
- Backend infra scripts (cluster lifecycle, budget, runner-healthcheck) → `infra/shared/bin/...`
- Backend deploy manifests → `infra/shared/deploy/...`
- Backend dev stack compose → `infra/shared/compose/dev-stack.yml`

**Tag prefix for this repo** : `stable-py-v` (per [common ADR-0061](https://gitlab.com/iris-7/iris-common/-/blob/main/docs/adr/0061-per-repo-tag-namespace-pattern.md) — Python uses prefix-disambiguated namespace from Java's `stable-v`). Run release scripts as : `infra/common/bin/ship/changelog.sh --tag-prefix stable-py-v`.

**Clone instruction** :
```bash
git clone https://gitlab.com/iris-7/iris-service-python.git
cd iris-service-python
git submodule update --init   # 2 submodules, NO --recursive needed
```

## Project overview

Python 3.13 service mirroring `../iris-service` (Java/Spring Boot 4) and
served behind the Angular `../../js/iris-ui` frontend (the UI's API client
should work against either backend transparently — same OpenAPI contract).

- **Entry point** : `src/iris_service/app.py` (FastAPI app factory)
- **Config** : `pyproject.toml` (uv) + `config/` (env-specific) + `.env` (local secrets, gitignored)
- **Sibling Java backend** : `../iris-service/`
- **Sibling UI frontend** : `../../js/iris-ui/`

## Python rules — critical

- **Type hints everywhere.** No untyped function bodies. mypy in strict mode.
- **Async by default.** Endpoints, DB access, Kafka, Redis — all async. Sync-only operations (fast computations) are fine but DB I/O must be async.
- **Pydantic v2 for ALL DTOs.** No `dataclass` for request/response models — Pydantic provides validation + serialisation + OpenAPI in one shot.
- **No `from X import *`.** Explicit imports only.
- **Avoid `Any` types.** Use specific types or `TypeVar`/`ParamSpec`. If `Any` is unavoidable, comment why.
- **Dependency injection via FastAPI `Depends()`.** No manual singletons. Test substitution via `app.dependency_overrides`.

## Build and quality

```bash
uv sync --all-extras                 # install runtime + dev deps
uv run pytest                        # unit + integration tests
uv run ruff check src tests          # lint
uv run ruff format src tests         # format
uv run mypy src                      # type check
uv run lint-imports                  # arch tests (import-linter)
```

## Git workflow

- Branch : `dev`. One commit per logical change.
- Push : `git push origin dev`.
- Pre-push hook runs `pytest -x` + `ruff check` — do not skip.
- When merging MR : `glab mr merge <id> --auto-merge --squash=false --remove-source-branch=false`.
- **Always pass `--remove-source-branch=false`** — GitLab deletes source branch by default, which would destroy `dev`.
- Never push to `main` directly.
- **Tag stable-vX.Y.Z ONLY after the post-merge `main` pipeline goes green.**

## Known gotchas

- **MCP server is in-process only.** The `/mcp/` endpoint (mounted by
  `iris_service/mcp/mount.py`) ONLY surfaces what the backend
  already produces in-process — Python `logging` ring buffer,
  `prometheus_client` REGISTRY, FastAPI's auto-OpenAPI, the
  Order/Product/Customer domain. No HTTP clients to Loki / Mimir /
  Grafana / GitLab / kubectl. External infra MCPs live outside
  this codebase. See [ADR-0062](https://gitlab.com/iris-7/iris-service-java/-/blob/main/docs/adr/0062-mcp-server-tool-exposure-per-method.md)
  in the Java sibling for the produces-vs-accesses decision rule.
- **MCP env vars** :
  - `IRIS_MCP_RING_BUFFER_SIZE` — capacity of the in-process log
    ring buffer that feeds the `tail_logs` tool. Default 500.
    Malformed / non-positive values fall back to 500 with a warning.
  - `IRIS_MCP_DISABLE_HOST_GUARD=1` — disables the MCP SDK's
    DNS-rebinding host-header guard. Set this only in tests using
    `httpx.ASGITransport` (synthetic `Host: test`). Production keeps
    the guard ON ; the SDK validates Host against `resource_server_url`.
- **MCP admin scope** — admin JWTs carry BOTH `ROLE_USER` and
  `ROLE_ADMIN` in the OAuth scopes list. The SDK's
  `required_scopes=[ROLE_USER]` gate (set in `mount.py`) accepts
  admin tokens this way ; per-tool `require_role(ROLE_ADMIN)` still
  discriminates inside admin-only tools.

## Compat philosophy

Default target : Python 3.13.
Compat targets (informational matrix) : 3.11, 3.12, 3.14 (when released).
Each compat cell runs the full test suite — same prod-grade requirement as
the Java sibling project (see `../iris-service/docs/adr/0060-sb3-compat-prod-grade.md`).

## ADRs

`docs/adr/` follows the same numbering + template as the Java sibling. Cross-references between projects are encouraged when an architectural decision affects both.

## Key architecture patterns

```
FastAPI app
  ├── lifespan (startup/shutdown — DB pool, Kafka producer/consumer, OTel)
  ├── middleware (CORS, request ID, logging, rate limiting)
  ├── routers (APIRouter per domain : auth, customer, actuator)
  └── DI services (Depends() : DB session, Kafka producer, Redis client)
```

## Type safety rules

- **No `Any` types** in router signatures or service methods. Use `BaseModel` subclasses for DTOs.
- All async functions return `Coroutine[Any, Any, T]` implicitly — declare the `T` explicitly.
- Repository / service methods return DTOs, NOT ORM entities (avoid lazy-load surprises).
- Dependency-injected services have a `Protocol` interface + concrete impl (= Java DI pattern).

## Comments

- Comments must explain **why** a decision was made, not just what the code does.
- For any non-obvious design choice, write a comment that a Claude session could use to understand context.
- Example good : `# Rate limit is 100 req/min per IP — chosen to match Cloudflare's DDoS threshold without blocking legitimate batch clients`
- Example bad : `# Set rate limit`
