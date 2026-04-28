# TASKS — iris-service-python

Open work only. Per `~/.claude/CLAUDE.md` rules : Python-only items
here ; done items removed (use `git tag -l` for history).

---

## 🌀 IRIS REBRAND (in flight 2026-04-28)

Coordinated rename Iris → Iris. See full context + phases in
[Java TASKS.md](https://gitlab.com/iris-7/iris-service-java/-/blob/main/TASKS.md#-iris-rebrand-in-flight-2026-04-28).

Python-side scope :

- **Code-level** : `iris_service` → `iris_service` package
  (102 files, 1226 refs). Affects FastAPI router prefixes, alembic
  migration revision IDs comments, OTel resource attributes,
  pydantic settings keys, MCP tool names, import-linter contracts.
- **Banner / README** : Phase 1 + 2 from the master plan, deployed
  in this session.
- **Phase 4 (code rename)** : 🔴 dedicated session (too risky inline).

## 🚫 Blocked / partial — UPDATED 2026-04-28

- 🟢 **Wire mutmut in CI** : mutmut 3.5.0 installed + configured
  (`[tool.mutmut]` targeting `src/iris_service/auth`), but
  walks parent FS on `run` and chokes on macOS `.VolumeIcon.icns`.
  **Workaround possible** : the bug is macOS-specific ; in Linux
  CI it should work. Could be wired as a manual GitLab CI job
  (validate stage, on-demand). Track [boxed/mutmut issues](https://github.com/boxed/mutmut/issues)
  for the upstream fix.

- 🟢 **Docker image alpine** : 412 MB → ~280 MB possible. Blocked :
  pydantic_core / cryptography / bcrypt have no musl wheels.
  Revisit when uv ships musl wheels.

## 🤔 À considérer — UPDATED 2026-04-28

- 🚫 **Flip integration-tests CI required** :
  - ✅ `TESTCONTAINERS_RYUK_DISABLED=true` already set in `.gitlab-ci/test.yml`
    (was the 2026-04-27 finding)
  - ❌ **Real blocker now identified** : testcontainers network bridging
    on the macbook-local runner — tests connect to `172.17.0.1:NNNN`
    and get connection refused. The CI job runs in a container, the
    testcontainers spawn on the host docker socket, but the network
    routing between them is broken.
  - ❌ Plus 1 obsolete MCP test : `test_list_tools_returns_14`
    expects 14 tools but the runtime registers 15 ([test_mcp_server.py](src/iris_service/integration/test_mcp_server.py)) — quick fix.
  - To unblock : (1) fix the test count assertion ; (2) investigate
    runner config for proper network bridging OR switch to GitLab
    `services:` for postgres + kafka.

- 🚫 **Flip sonarcloud required** :
  - ✅ `SONAR_TOKEN` IS set at group level (verified 2026-04-28
    via `glab api groups/iris-7/variables`)
  - The `sonarcloud` job should be running ; if it appears
    rule-skipped on the latest pipeline, check the rules
    block in `.gitlab-ci/quality.yml`.

- 🟢 **Migrate Java's `jvm.config` Comments rule to pytest config** :
  preventive rule — adopt the dated-TODO comment pattern for any
  future `allow_failure` shields. No current shields → no immediate
  action needed.

## 📊 SLO/SLA backlog

Quick wins SHIPPED 2026-04-25 : 3 SLOs (Sloth) + multi-burn-rate +
Grafana dashboard + ADR-0058 + sla.md.

Iteration-2 SHIPPED 2026-04-27 in [stable-py-v0.6.8](https://gitlab.com/iris-7/iris-service-python/-/tags/stable-py-v0.6.8) :
- ✅ SLO breakdown / latency heatmap / Apdex dashboards
- ✅ Chaos-driven SLO demo wiring
- ✅ 3 runbooks
- ✅ `docs/slo/review-cadence.md` thin pointer

## 🎨 README polish

Major sync wave **shipped 2026-04-27** :
- ✅ README.fr.md mastery block + 8-row matrix ([!41](https://gitlab.com/iris-7/iris-service-python/-/merge_requests/41))
- ✅ mkdocs landing refresh ([!42](https://gitlab.com/iris-7/iris-service-python/-/merge_requests/42))

Remaining :
- 🟢 **Customer\* rename** — covered by the Customer rename chip
  spawned 2026-04-28 (analysis-only, awaits user click).

## 🎯 Surface fonctionnelle — entités e-commerce

Foundation **shippée 2026-04-26** dans [stable-py-v0.6.4](https://gitlab.com/iris-7/iris-service-python/-/tags/stable-py-v0.6.4) :
- ✅ Alembic 0002/0003/0004 + ORM SQLAlchemy 2.x async
- ✅ Pydantic v2 schemas + FastAPI routers
- ✅ Feature-sliced `src/iris_service/{order,product}/` ⚠️ to rename to `iris_service`

Wave **shippée 2026-04-27** :
- ✅ ADR data model (shared ADR-0059)
- ✅ Hurl smoke flow + new endpoints PUT /orders/{id}/status, GET /products/{id}/orders, PATCH /lines/.../status

Remaining :
- ☐ **Property-based tests Hypothesis** — scheduled `java-ecommerce-coverage-batch` 2026-05-04
- ☐ **pytest-asyncio integration tests** — blocked by testcontainers network issue (see above)
- ☐ **`stability-check.sh` section 3** sur le nouveau code
