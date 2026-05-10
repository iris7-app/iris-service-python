# Kafka CI image switch — bitnamilegacy/kafka → redpanda — 2026-05-10

## Symptom

Every `integration-tests` pipeline (GitLab CI) failed at service-startup
phase :

```
WARNING: Service runner-...-bitnamilegacy__kafka-1 probably didn't start properly.
Health check error: service ...-wait-for-service timeout
Health check container logs:
  waiting for TCP connection to <id> on [9092]...
  dialing <id>:9092...
  [...repeats every 1s for 30 seconds...]
```

Kafka KRaft single-node bootstrap takes 30-45s : storage format, controller
election, broker registration. The GitLab default `wait_for_services_timeout`
is 30s.

Tried bumping `wait_for_services_timeout = 120` on the local runner config :
it took effect (health check ran 120s) but kafka never accepted TCP
connections in that window — even though kafka logs showed `Created
data-plane acceptor and processors for endpoint : ListenerName(PLAINTEXT)`.
The container is alive, listening internally, but external port forward
fails. Suspected a Mac arm64 + Docker network namespace quirk.

On GitLab SaaS Linux runners (`saas-linux-medium-amd64`) — same failure :
kafka health check times out at 30s. SaaS runners do not honor the
`wait_for_services_timeout` from `config.toml` (the runner config is GitLab-
managed, not user-controllable).

## Investigations

| Approach | Result |
|---|---|
| `network_per_build = true` on local runner | DNS resolves now, but kafka still doesn't accept connections in 30s |
| `privileged = true` on local runner | Same — kafka container starts but external port unreachable |
| `wait_for_services_timeout = 120` | Health check waits 120s, kafka still unreachable on that port |
| Pure dind runner (separate `macbook-local-dind` instance, no socket binding) | Kafka container starts, internal port listens, but service health check timeout — same root cause |
| Route to GitLab SaaS runner | Default 30s timeout, no override available — fails |

Common bottleneck across all five attempts : kafka KRaft is too slow to
bootstrap to satisfy a 30s TCP-dial health check.

## Fix : switch to redpanda

[Redpanda](https://redpanda.com) is a wire-protocol-compatible Kafka broker
written in C++ that boots in ~3-5s. The `aiokafka` client connects to it
transparently via `kafka:9092` — no application code change needed.

Configuration in `.gitlab-ci/test.yml` :

```yaml
services:
  - name: redpandadata/redpanda:v24.2.10
    alias: kafka
    command:
      - redpanda
      - start
      - --kafka-addr=PLAINTEXT://0.0.0.0:9092
      - --advertise-kafka-addr=PLAINTEXT://kafka:9092
      - --smp=1
      - --memory=512M
      - --reserve-memory=0M
      - --node-id=0
      - --check=false
      - --mode=dev-container
```

`--mode=dev-container` enables the relaxed defaults (no replication, no
disk-flush, no metric collection) appropriate for CI ephemeral runs.

## GitHub Actions side

GitHub Actions `services:` does not accept arbitrary `command:` overrides.
Kept the bitnamilegacy/kafka image there but with a long-tail healthcheck
(20 retries × 10s = 200s) so the slow kafka bootstrap completes before
the dependent step starts. Workflow :
`.github/workflows/integration-tests.yml`.

## Local dev (testcontainers fallback)

`tests/integration/conftest.py` continues to work unchanged — it auto-
detects `IRIS_KAFKA_BOOTSTRAP_SERVERS` (set in CI) and falls back to
testcontainers-spawned kafka if not set. Local `pytest` still spawns a
fresh kafka container on demand, which boots slow but is fine for an
on-demand dev run (no health-check gating).

If you want to also switch local dev to redpanda for faster pytest runs,
edit `tests/integration/conftest.py` `kafka_container_factory` to spawn
`redpandadata/redpanda` instead.

## Trade-offs

- ✅ **Boot time** : redpanda 3-5s vs kafka 30-45s
- ✅ **Wire compat** : aiokafka client unchanged
- ✅ **No app code change** : producers + consumers continue to talk port 9092
- ⚠️ **Production parity** : prod uses Apache Kafka. CI now uses redpanda. The
  wire protocol is the same but edge-case behaviours can differ (rare —
  redpanda is widely Kafka-API tested). Compensating control : load tests
  + smoke tests still target real kafka clusters.
- ⚠️ **Cluster scenarios** : multi-broker / partition rebalance / failover
  scenarios are not testable on a single redpanda node in dev-container
  mode. These remain manual against real kafka clusters in staging.

## Reverting

If a redpanda-specific issue ever surfaces, revert by switching back to
`bitnamilegacy/kafka:3.7-debian-12` with the original KAFKA_CFG_* env vars
(see git history of `.gitlab-ci/test.yml` pre-2026-05-10). Re-introduce
`wait_for_services_timeout` on the runner side — but accept it won't work
on SaaS.
