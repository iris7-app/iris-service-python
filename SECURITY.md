# Security policy

## Reporting a vulnerability

Iris-service-python is the **Python sibling** of [`iris-service-java`](https://gitlab.com/iris-7/iris-service-java),
a portfolio demo project — not a production system with real users.
It still takes security seriously because the same code and configuration
patterns are used by others as reference.

**Please do not file public issues for security vulnerabilities.**

Instead, report them privately :

- **Email** : security@iris-7.com (monitored)
- **GitLab** : open a
  [confidential issue](https://gitlab.com/iris-7/iris-service-python/-/issues/new?issue[confidential]=true)
  — only maintainers can see it.
- **GitHub** : the mirror accepts
  [security advisories](https://github.com/iris7-app/iris-service-python/security/advisories/new)
  (private to maintainers).

Include, at minimum :

- A short description of the issue
- Reproduction steps (curl command, file path, log excerpt)
- Affected version(s) — the tag or commit SHA
- Your assessment of severity (CVSS optional)

## Response timeline

| Step | Target |
|---|---|
| Acknowledgement | within 7 days |
| Initial triage | within 14 days |
| Fix or mitigation | within 30 days for high/critical, 90 days for medium |
| Public disclosure | coordinated with reporter, default 90 days from fix |

## Scope

In-scope :
- HTTP API endpoints (`/customers`, `/auth`, `/actuator/*`, `/customers/{id}/enrich`,
  `/customers/{id}/bio`, `/customers/diagnostic/*`)
- JWT auth flow (issue + refresh + revocation)
- Kafka request-reply enrichment
- Outbound integrations (Ollama LLM, JSONPlaceholder)
- Docker image + supply chain
- CI/CD pipeline (GitLab Runners, secrets handling)
- Infrastructure-as-code (Terraform, K8s manifests in shared submodule)

Out of scope :
- The portfolio demo's deliberate diagnostic endpoints (`/customers/diagnostic/*`) —
  they intentionally produce 5xx / slow responses for observability demos.
- Third-party services (Ollama, JSONPlaceholder, Auth0, GitLab.com, GCP, OVH).

## Security baseline

The project's security posture is documented in :

- [ADR-0002 — Auth (JWT + rotation + bcrypt)](docs/adr/0002-auth-jwt-with-rotation.md)
- [ADR-0007 — Industrial Python practices](docs/adr/0007-industrial-python-best-practices.md)
- [SLA promise](docs/slo/sla.md) — what's covered, what's not

Automated security tooling :

- **pip-audit** — CVE scanning of pyproject.toml + lockfile (hard CI gate ;
  3 CVEs caught + fixed during dev : pytest 9.0.3, fastapi 0.136.1, starlette 1.0.0).
- **ruff bandit** rules (`S` ruleset) — security antipatterns in source code.
- **gitleaks** — secret scan in pre-commit + CI.
- **mypy strict** — catches many type-confusion bugs that lead to vulnerabilities
  (e.g. accepting `dict[str, Any]` where a typed model would have rejected
  malformed payloads).
- **import-linter** — architectural boundaries that prevent secret-leaking
  imports (e.g. `config` can't be imported by anything else from the project).

## Known vulnerabilities (current state)

`pip-audit` ignores **CVE-2026-3219** (pip 26.0.1, bundled in uv's Python distribution,
no upstream fix released as of 2026-04-25). Re-checked monthly ; the ignore is dated
in `.gitlab-ci/quality.yml`.

## Hall of fame

Reporters who responsibly disclose vulnerabilities will be credited here
(with their consent) :

- *no entries yet — be the first ?*
