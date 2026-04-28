# TASKS — iris-service-python

Open work only. Per `~/.claude/CLAUDE.md` rules : Python-only items
here ; done items removed (use `git tag -l` for history).

---

## 🚫 Blocked

- **mutmut in CI** : mutmut 3.5.0 installed + configured
  (`[tool.mutmut]` targeting `src/iris_service/auth`), but walks parent
  FS on `run` and chokes on macOS `.VolumeIcon.icns`. Linux CI should
  work — could wire as a manual GitLab CI job. Track
  [boxed/mutmut issues](https://github.com/boxed/mutmut/issues) for
  the upstream fix.

- **Docker image alpine** : 412 MB → ~280 MB possible. Blocked :
  pydantic_core / cryptography / bcrypt have no musl wheels.
  Revisit when uv ships musl wheels.

## 🤔 À considérer

- **Flip integration-tests CI required** :
  - Real blocker : testcontainers network bridging on macbook-local
    runner — tests connect to `172.17.0.1:NNNN` and get connection
    refused. CI job runs in a container, testcontainers spawn on host
    docker socket, network routing broken.
  - Plus obsolete MCP test : `test_list_tools_returns_14` expects 14
    tools but runtime registers 15 ([test_mcp_server.py](src/iris_service/integration/test_mcp_server.py))
    — quick fix.
  - Unblock : (1) fix test count assertion ; (2) investigate runner
    config for proper network bridging OR switch to GitLab `services:`
    for postgres + kafka.

## 🎯 e-commerce coverage (scheduled `java-ecommerce-coverage-batch` 2026-05-04 14:00)

- ☐ Property-based tests with Hypothesis on order / product
  invariants
- ☐ pytest-asyncio integration tests (blocked by testcontainers
  network issue above)
- ☐ `stability-check.sh` section 3 to cover the new modules
