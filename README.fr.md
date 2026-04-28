# iris-service-python

<sub>[English](README.md) · **Français**</sub>

> **Ce que ce projet démontre comme maîtrise**
>
> _Un survol 30 secondes des thèmes centraux de la maîtrise backend actuelle —
> chaque axe est vérifié à chaque tag `stable-py-v*`. Source de vérité pour
> "ce que cette révision garantit" : `git show stable-py-vX.Y.Z`._
>
> - 🤖 **IA** — Serveur FastMCP (Anthropic `mcp[cli]≥1.27`) + transport streamable-http monté sur `/mcp` + 14 outils in-process miroirs du backend Java (décorateurs `@tool` par méthode, DTO Pydantic v2 frozen) + log d'audit par appel d'outil (action `MCP_TOOL_CALL`) + cache d'idempotence sur `create_order` + authz par rôle (`require_role(ROLE_ADMIN)`).
> - 🔒 **Sécurité** — JWT HS256 (15 min, rotation refresh-token) + **middleware X-API-Key** (parité avec `ApiKeyAuthenticationFilter` Java, défaut `demo-api-key-2026`) + RBAC (`ROLE_ADMIN` / `ROLE_USER`, les deux accordés sur le chemin API-key) + garde host anti DNS-rebinding + redaction env-var `(?i).*(password|secret|token|key|credential).*` + gate dur pip-audit (zéro shield `allow_failure`).
> - 🧠 **Fonctionnel** — Onboarding & enrichissement client (lookup JSONPlaceholder + génération de bio par LLM Ollama) + domaine Order / Product / OrderLine (6 invariants depuis l'ADR-0059 partagée, vérifiés via 8 tests de propriétés Hypothesis) + événements d'audit Kafka + endpoints diagnostic d'incident (`slow-query`, `db-failure`, `kafka-timeout`).
> - ☁️ **Infrastructure & Cloud** — Image Docker (412 Mo sur debian-slim ; alpine bloqué sur les wheels musl pydantic_core / cryptography / bcrypt) + déploiement GKE via la même famille de chart que le frère Java + Workload Identity Federation + Postgres asyncpg + Kafka aiokafka + Redis async client.
> - 📊 **Observabilité** — Traces + logs + métriques OpenTelemetry → stack LGTM (Tempo / Loki / Mimir / Grafana) + exporter `starlette-prometheus` + 3 SLOs as code via Sloth (miroir Java) + alerting multi-burn-rate + 4 dashboards (vue d'ensemble SLO, Apdex, heatmap latence, breakdown SLO par `path_template`) + annotations chaos-driven sur les SLO + 3 runbooks.
> - ✅ **Qualité** — `pytest --cov-fail-under=90` gate bloquant (~308 tests unit + integration, 94.59 % de couverture sur la suite complète) + `mypy --strict` + `ruff check` + `ruff format --check` + `import-linter` (l'ArchUnit Python) + tests de propriétés Hypothesis + Testcontainers (Postgres) + asgi-lifespan + 19 tests dédiés au middleware X-API-Key.
> - 🔄 **CI/CD** — GitLab CI 9 jobs sur `lint / test / integration / package / sonar / pages` + matrice de compat Python 3.12 / 3.13 / 3.14 (manuel) + Conventional Commits (lefthook + commitlint) + auto-merge avec `--remove-source-branch=false` + gate dur pip-audit + import-linter + Renovate hebdo + push miroir GitHub sur tag.
> - 🏛 **Architecture** — Feature-slicing sous `src/iris_service/{customer, order, product, mcp, auth, …}` (miroir de la structure de package Java) + exposition MCP `@tool` par méthode (ADR-0062, règle "produces vs accesses" — ZÉRO client HTTP vers Loki / Mimir / Grafana / GitLab / kubectl dans ce jar Python) + sous-modules polyrepo flat α (ADR-0060) + 7 non-négociables Clean Code.
> - 🛠 **DevX** — `uv` (astral, 100× plus rapide que pip) + hooks Lefthook commit-msg + pre-push + `bin/dev/api-smoke.sh` (flows Hurl) + tâches programmées pour TODO datés + integration-tests + audit flip-gates sonarcloud (critère 5-greens-consecutifs tracé dans `TASKS.md`) + Renovate + template CI Conventional Commits (partagé via `infra/common/`).

[![pipeline](https://gitlab.com/iris-7/iris-service-python/badges/main/pipeline.svg)](https://gitlab.com/iris-7/iris-service-python/-/pipelines)
[![coverage](https://img.shields.io/badge/coverage-90.21%25-success)](https://gitlab.com/iris-7/iris-service-python/-/pipelines)
[![Python 3.14](https://img.shields.io/badge/Python-3.14_+_3.13_3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
![SLO 99.5%](https://img.shields.io/badge/SLO-99.5%25_+_burn_rate-2D7FF9)
![mypy strict](https://img.shields.io/badge/mypy-strict-blue)

## Ce que ce projet démontre

Miroir Python de [`iris-service-java`](https://gitlab.com/iris-7/iris-service-java) —
mêmes préoccupations backend industrielles, exprimées dans la stack Python moderne :

- **Pipeline d'onboarding client industriel** (registration → validation → enrichissement
  externe via JSONPlaceholder + Ollama LLM → événements d'audit Kafka → suivi d'état →
  endpoints de diagnostic d'incident) — pas une démo CRUD.
- **Discipline de typage émulant Java** : `mypy --strict` + Pydantic v2 + `Final` /
  `Literal` / `TypeAlias` (PEP 695) partout ; **127 tests unitaires**, **couverture 90.21%**
  avec gate bloquant `--cov-fail-under=90` ; **8 tests property-based hypothesis** ;
  **import-linter** = ArchUnit pour Python.
- **Même observabilité** : OpenTelemetry (traces + logs + métriques) → stack LGTM,
  exporter starlette-prometheus, **3 SLOs définis-as-code via Sloth** avec alerting
  multi-window multi-burn-rate (Google SRE Workbook).
- **Même supply chain sécurité** : JWT (pyjwt) + bcrypt 5.x rotation, **gate CVE pip-audit**
  (3 CVEs corrigés pendant le dev), `gitleaks`, exit-tickets datés `--ignore-vuln`.
- **Même discipline CI** : GitLab CI exclusivement, runner group-level, conventional-commits,
  hooks lefthook 3-niveaux, ruleset ruff complet, Docker multi-arch via buildx.

La cible Python est **3.14 (branche par défaut)** — exploration de la stack la plus récente —
mais la matrice de compatibilité en CI compile + teste vert sur **3.12 + 3.13** depuis le même
code. Cible production conservatrice = 3.12 (le plus ancien avec PEP 695 `type` keyword +
ergonomie `Final` / `Literal`).

Voir [ADR-0007 — Pratiques Python industrielles](docs/adr/0007-industrial-python-best-practices.md)
pour la baseline de 13 décisions + [documentation SLO/SLA](docs/slo/).

## TL;DR pour les recruteurs (lecture 60 sec)

- **Démonstrateur polyrepo** : implémentation Python du même backend industriel servi par
  [`iris-service-java`](https://gitlab.com/iris-7/iris-service-java). Infra +
  observabilité + templates CI partagés via le submodule git
  [`iris-service-shared`](https://gitlab.com/iris-7/iris-service-shared).
- **mypy --strict sur 41 fichiers source** : Final / Literal / TypeAlias / aliases PEP 695,
  pas d'Any implicite, pas de defs non typées.
- **Couverture 90.21%** avec gate dur `--cov-fail-under=90` ; 127 tests unitaires + property-based
  hypothesis + 5 tests d'intégration kafka_client via testcontainers.
- **SLO/SLA-as-code** via Sloth : 3 SLOs (availability 99% / latency p99 < 500ms /
  enrichment 99.5%) sur 30j + alerting multi-burn-rate + dashboard Grafana.
- **Gate dur pip-audit** : 3 CVEs détectées + corrigées pendant le dev (pytest 9.0.3,
  fastapi 0.136.1, starlette 1.0.0).

## Ce que ça prouve pour un architecte backend senior

| Préoccupation | Ce que ce repo démontre | Pourquoi ça compte en production |
|---|---|---|
| **Discipline de typage** | `mypy --strict` sur 41 fichiers ; aliases PEP 695 `type` ; constantes `Final[T]` ; `Literal["access","refresh"]` pour le narrowing du type de token ; 5 ADRs (0008-0012) documentent la discipline. | Le typing runtime-only de Python obtient des garanties équivalentes au compile-time ; les refactors restent sûrs. |
| **Architecture async-first** | Chaque chemin I/O est `async def` ; SQLAlchemy 2.x async + asyncpg + aiokafka + redis-py async + httpx.AsyncClient ; corrélation `ContextVar` propagée à travers les coroutines. (ADR-0008) | Une event-loop par worker absorbe des centaines de requêtes concurrentes vs ~10 sur des workers sync — même hardware, débit ×10. |
| **Rigueur des tests** | 127 tests unit + 8 hypothesis property-based (ont trouvé 2 vrais bugs pendant l'écriture) + 5 tests d'intégration kafka_client via testcontainers + pytest-benchmark sur les hot paths (JWT 9µs, bcrypt 280ms). Couverture 90.21% avec gate bloquant `--cov-fail-under=90`. | La couverture n'est pas du chiqué — la gate fait échouer la CI ; le property-based attrape les edge cases que les tests par exemple ratent. |
| **Frontières architecturales** | `import-linter` enforce 4 contrats : config-leaf, indépendance db↔kafka, indépendance des adapters d'intégration, observability-leaf. La CI échoue sur violation. (ADR-0007 §5) | La flexibilité d'import de Python = risque de drift ; l'enforcement par tooling > la bonne volonté du reviewer. |
| **Supply chain sécurité** | JWT (pyjwt) + rotation bcrypt 5.x, **gate dur pip-audit** (3 CVEs attrapés pendant le dev), gitleaks secret scan, exit-tickets datés `--ignore-vuln`, règles OWASP via ruff bandit. | Le pinning, c'est la moitié — savoir quand une version pinnée devient vulnérable, c'est l'autre moitié. |
| **Observabilité** | OTel SDK → Collector → LGTM ; logs JSON structlog ; métriques starlette-prometheus ; **3 SLOs as code via Sloth** avec alerting multi-window multi-burn-rate (Google SRE Workbook). (ADR-0012) | « Sommes-nous dans les clous ce mois-ci ? » devient une question objective avec un dashboard Grafana. |
| **Modernisation outillage** | `uv` remplace pip + setuptools + virtualenv + pyenv (5-10× plus rapide, lockfile cross-platform). Syntaxe de typage PEP 695. (ADR-0009) | Reste à la pointe de l'outillage Python ; démontre la capacité à évaluer + adopter les nouveaux leaders de l'écosystème. |
| **Parité Java** | Mêmes 3 SLOs, même contrat Kafka, même baseline sécurité que le frère Java. Le submodule partagé (`iris-service-shared`) enforce le plancher commun. | Démontre la capacité à garder plusieurs implémentations stack cohérentes sans verrouillage monorepo. |

## Pile technique

| Couche | Technologie | Équivalent Java |
|---|---|---|
| Framework web | **FastAPI** 0.136 | Spring Boot 4 Web MVC |
| DTO + validation | **Pydantic** v2.11 | Jackson + Bean Validation |
| ORM | **SQLAlchemy** 2.0 async | Spring Data JPA / Hibernate |
| Migrations | **Alembic** 1.14 | Flyway |
| Auth JWT | **pyjwt** + **bcrypt** 5.x | Spring Security + jjwt |
| Kafka | **aiokafka** 0.13 | Spring Kafka |
| Redis | **redis-py** 5.2 (asyncio) | Spring Data Redis |
| Observabilité | **OpenTelemetry SDK** + Prometheus | Micrometer + OTel SDK |
| **SLO/SLA-as-code** | **Sloth** + multi-burn-rate | Sloth (miroir) |
| Logging | **structlog** | Logback + structured logging |
| Rate limiting | **slowapi** | bucket4j |
| Gestionnaire de paquets | **uv** (Astral) | Maven |
| Tests | **pytest** + **pytest-asyncio** + **hypothesis** | JUnit 5 + Mockito |
| Property-based | **hypothesis** | jqwik |
| Benchmarks | **pytest-benchmark** | JMH |
| Lint / Format | **ruff** + **mypy** strict | Checkstyle + SpotBugs + PMD |
| Tests d'archi | **import-linter** (4 contrats) | ArchUnit |
| Scan CVE | **pip-audit** | OWASP Dependency-Check |
| Tests conteneurs | **testcontainers-python** | Testcontainers |
| Docker | multi-stage + uvicorn (Py 3.14 slim) | multi-stage + Spring Boot |

## Démarrage rapide

```bash
# Installer les dépendances
uv sync --all-extras

# Lancer le serveur dev (hot reload)
uv run iris-service

# Ou avec uvicorn explicite
uv run uvicorn iris_service.app:app --reload --port 8080

# Lancer les tests
uv run pytest

# Lint + vérification de types
uv run ruff check src tests
uv run mypy src

# Démo complète (postgres + redis + kafka + LGTM + app)
bin/demo-up.sh
```

## Structure du projet

```
src/iris_service/
  api/            # Routers FastAPI (= controllers Spring)
  auth/           # JWT + utilisateur injecté par dépendance (= Spring Security)
  customer/       # Domaine Customer (CRUD + RecentCustomerBuffer)
  integration/    # Services externes (TodoService, BioService stubs)
  messaging/      # Producers/consumers Kafka
  observability/  # Setup OTel + métriques custom
  middleware/     # CORS + Prometheus + SlowAPI rate-limit + structlog + request-id
  config/         # Réglages Pydantic (= application.yml)
  app.py          # Factory FastAPI + lifespan + middleware

tests/
  unit/           # pytest pur, deps mockées
  integration/    # backed par testcontainers (postgres, kafka, redis)

alembic/          # Migrations DB (= Flyway)
infra/            # docker-compose, postgres init, stack observabilité
infra/shared/     # Submodule iris-service-shared (dev-stack + CI templates + ...)
infra/k8s/        # Manifests Kubernetes (Deployment + Service + HPA + PDB)
docs/             # Documentation mkdocs (autodoc API + ADRs + ops)
docs/adr/         # Architecture Decision Records (6 ADRs)
bin/              # Scripts d'ops (run.sh, demo-up.sh, etc.)
```

## Endpoints (miroir du service Java)

- `GET /customers` — liste paginée (dispatch v1 / v2 via `X-API-Version`)
- `POST /customers` — création + publication CustomerCreatedEvent (Kafka FAF)
- `GET /customers/{id}` — lecture
- `PUT /customers/{id}` — remplacement
- `PATCH /customers/{id}` — mise à jour partielle
- `DELETE /customers/{id}` — suppression
- `GET /customers/recent` — 10 derniers depuis le ring buffer Redis
- `GET /customers/{id}/audit` — trace d'audit synthétique
- `GET /customers/{id}/enrich` — enrichissement Kafka request-reply
- `GET /customers/{id}/todos` — JSONPlaceholder + retry tenacity
- `GET /customers/diagnostic/slow-query` — induit une requête lente (Tempo)
- `GET /customers/diagnostic/db-failure` — induit une erreur 500 (Loki)
- `GET /customers/diagnostic/kafka-timeout` — induit un 504 (Problem+JSON)
- `POST /auth/login` — émission JWT
- `POST /auth/refresh` — rotation refresh token
- `GET /auth/me` — claims utilisateur courant
- `GET /actuator/health` — composite (DB + Redis + Kafka)
- `GET /actuator/health/{liveness,readiness}` — sondes k8s
- `GET /actuator/info` — métadonnées runtime
- `GET /actuator/prometheus` — endpoint scrape métriques
- `GET /actuator/quality` — signaux qualité de code agrégés
- `POST /mcp/` — transport streamable-http Model Context Protocol (voir [README EN](README.md#ai-integration-via-mcp))

## Intégration IA via MCP

Miroir de [ADR-0062](https://gitlab.com/iris-7/iris-service-java/-/blob/main/docs/adr/0062-mcp-server-tool-exposure-per-method.md)
du frère Java — Iris expose un serveur
[Model Context Protocol](https://modelcontextprotocol.io/) in-process sur
`/mcp/`. Un client LLM (Claude Desktop, `claude mcp add`, l'Inspecteur MCP)
se connecte avec le même JWT que l'API REST et obtient un catalogue typé
de 14 outils sans nouvelle plomberie HTTP.

**Contrainte architecturale** : le backend reste infrastructure-agnostic
— ZÉRO client HTTP vers Loki / Mimir / Grafana / GitLab / GitHub /
kubectl dans le process FastAPI. Uniquement ce que le backend produit
DÉJÀ in-process : ring buffer `logging` Python, REGISTRY
`prometheus_client`, OpenAPI auto-généré FastAPI, et le domaine
Order/Product/Customer.

Voir le [README EN](README.md#ai-integration-via-mcp) pour la table
détaillée des 14 outils, les contrôles d'auth/audit/redaction, et le
flow démo de 60 secondes (commandes shell quasi-identiques).

## Philosophie compat

Comme le miroir Java — Python 3.14 par défaut, support Python 3.12/3.13 via
matrix CI informationnelle. Python 3.11 abandonné 2026-04-25 (EOL 2027-10).

## Projets sœurs

- [`../iris-service-java`](../iris-service-java) — backend Java/Spring Boot 4 (canonique)
- [`../../js/iris-ui`](../../js/iris-ui) — frontend Angular 21 (fonctionne contre l'un ou l'autre backend)
- [`../iris-service-shared`](../iris-service-shared) — infrastructure partagée (dev-stack docker-compose + templates CI + scripts budget + ADRs cross-cutting)

## Documentation

- 📚 Site mkdocs : https://iris-7.gitlab.io/iris-service-python/ (publié sur GitLab Pages à chaque push main)
- 📋 ADRs : `docs/adr/` (6 records — choix de stack, auth, Kafka, observabilité, settings, logging)
- 🔬 Java sibling : https://gitlab.com/iris-7/iris-service-java
- 🌐 Mirroir GitHub : https://github.com/iris7-app/iris-service-python

## Licence

MIT
</content>
</invoke>