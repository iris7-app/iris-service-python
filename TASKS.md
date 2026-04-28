# TASKS — iris-service-python

Open work only. Per `~/.claude/CLAUDE.md` rules : Python-only items
here ; done items removed (use `git tag -l` for history).

---

## 🚫 Blocked upstream

- **mutmut in CI** : mutmut 3.5.0 walks parent FS on `run` and
  chokes on macOS `.VolumeIcon.icns`. Linux CI should work — could
  wire as a manual GitLab CI job. Track
  [boxed/mutmut issues](https://github.com/boxed/mutmut/issues).

- **Docker image alpine** : 412 MB → ~280 MB possible. Blocked :
  pydantic_core / cryptography / bcrypt have no musl wheels.
  Revisit when uv ships musl wheels.

## 🤔 À considérer

- **Flip integration-tests CI required** :
  - Real blocker : testcontainers network bridging on macbook-local
    runner — tests connect to `172.17.0.1:NNNN` and get connection
    refused. CI job runs in a container, testcontainers spawn on host
    docker socket, network routing broken.
  - Unblock options : (1) investigate runner config for proper
    network bridging ; OR (2) switch to GitLab `services:` for
    postgres + kafka (requires re-authoring the IT setup to use
    services rather than Testcontainers).

## 🎯 e-commerce coverage gates (scheduled `java-ecommerce-coverage-batch` 2026-05-04 14:00)

- ☐ Property-based tests with Hypothesis on order / product
  invariants (mirror the 10 jqwik properties on Java side : 6 on
  Order/totalAmount + 5 on Product stock/price)
- ☐ pytest-asyncio integration tests (blocked by testcontainers
  network issue above — see "Flip integration-tests CI required")
- ☐ `stability-check.sh` section 3 to cover the new modules
  (mirrors Java's `bin/dev/sections/code.sh` pattern)
