# Runner dind migration audit — 2026-05-09

## Context

The `macbook-local` GitLab Runner uses **docker-socket-binding** mode :

```toml
[runners.docker]
  image = "docker:27"
  privileged = true
  volumes = ["/var/run/docker.sock:/var/run/docker.sock", "/cache"]
  network_per_build = true
  wait_for_services_timeout = 120
```

This mode mounts the host Docker socket into the job container, so the job
can run `docker build` etc. as siblings of itself.

## The bug

GitLab CI `services:` (postgres + kafka declared in `.gitlab-ci/test.yml`)
**silently fail to start** in this mode. The job logs show :

```
WARNING: Service runner-...-postgres-0 probably didn't start properly.
Health check error:
service "...-postgres-0-wait-for-service" timeout
Health check container logs:
[empty]
```

The service container is created but crashes (or hangs) before its TCP port
becomes accessible — and **no service logs are captured** to diagnose why.

Then the test container can't resolve the service alias (`postgres:5432`,
`kafka:9092`) and pytest fails with `socket.gaierror: [Errno -2] Name or
service not known` on every integration test (12 affected, 6 errors + 6
failures).

## Failed attempts (all on macbook-local runner config 2026-05-09)

| Setting | Result |
|---|---|
| `network_per_build = true` | DNS would now resolve, but services still crash before listening on their port |
| `wait_for_services_timeout = 120` (was 30s default) | 120s atteint sans que le service écoute. Confirme que ce n'est pas un timing issue |
| `privileged = true` | Identique. Service crée mais crash immédiatement |

Postgres image `postgres:16.6-alpine` works fine when run standalone via
`docker run` outside the runner — so it's not the image, it's the
runner+services interaction.

## Root cause hypothesis

`docker-socket-binding` mode + GitLab `services:` is a **known
incompatibility** in some configurations. The runner spawns service
containers via the host socket but the network bridge between the job
container and these "sibling" service containers depends on Docker
networking that doesn't survive the socket binding.

Reference : [GitLab Runner Docker executor — services with Docker-in-Docker
vs Docker socket binding](https://docs.gitlab.com/runner/executors/docker.html#use-docker-in-docker-with-services).

## Fix path — runner dind migration (next session)

Replace the docker-socket-binding mode with **dind (Docker-in-Docker)** :

1. Change runner image to `docker:dind`
2. Remove the socket binding from `volumes`
3. Add a sidecar `docker:dind` service to every job that needs Docker
4. `DOCKER_HOST = tcp://docker:2375` in default env

This is a **runner-level change** that affects every job (not just python's
integration-tests). It needs :

- A test pass on every project's pipeline (`iris-common`,
  `iris-service-shared`, `iris-service-java`, `iris-service-python`,
  `iris-ui`) to confirm nothing breaks.
- Re-validation of the docker-build / kaniko jobs (which currently use
  the socket binding).
- A rollback plan if dind doesn't work on macOS arm64 with kaniko.

## Temporary mitigation (this session)

`integration-tests` job in `.gitlab-ci/test.yml` is **scope-out** with
`rules: - when: never`, dated TODO `2026-05-16`.

Per CLAUDE.md "(c) Scope-out, not shield" : the absence is explicit,
the tests still live in the repo, local dev still runs them via
testcontainers. The job just doesn't gate the pipeline.

Cleared by the runner dind migration MR.
