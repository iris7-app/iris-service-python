# ADR-0014 — Test coverage : 90 % floor + Hypothesis property tests on selected paths

**Status** : Accepted
**Date** : 2026-04-26
**Sibling project** : `../iris-service-java` (Java side uses different
                       coverage strategy : per-module thresholds via JaCoCo,
                       no property-based testing pre-Hypothesis-Java)

## Context

Two interlocking decisions about Python's test strategy were made
unilaterally during the 2026-04 industrial-Python push (per
[ADR-0007](0007-industrial-python-best-practices.md)) and need to be
documented as their own ADR :

1. **What coverage threshold to enforce ?** The Java side uses per-module
   JaCoCo thresholds (e.g. domain ≥ 90 %, controllers ≥ 75 %, infra ≥ 60 %).
   The Python side could mirror that, OR pick a single global floor.
2. **Where to use property-based testing (Hypothesis) ?** Hypothesis is
   already an accepted technique on the Python side
   ([ADR-0011](0011-hypothesis-property-based-testing.md)) but the
   selection criteria (which paths get property tests vs example-based
   tests) was implicit and worth stating.

This ADR captures both as a single test strategy.

## Decision

### Coverage : single global floor of **90 %**

The whole codebase is held to a **single 90 % line-coverage floor**,
measured by `pytest --cov=src --cov-report=term`. The 90 % is enforced
by `bin/dev/stability-check.sh` section 3 (amber finding when below ;
not blocking the script's exit code, but a clear warning). It is NOT
enforced as a CI hard fail (because flaky transient drops would block
unrelated work) — instead the maintainer treats it as a release-gate
soft check.

**Why single global vs Java's per-module** :

- Python's domain/infra split is **less rigid** than Java's. The same
  module often holds Pydantic schemas (light) + business logic (heavy)
  + repo-specific glue (medium). Per-module thresholds would either
  be lax everywhere (60 %) or punitive in the wrong places (75 % on a
  glue file is hard to justify).
- A single 90 % threshold is **easier to communicate** in CLAUDE.md
  and in PR reviews ("did the new file lower us below 90 % ?" is a
  binary question).
- The 90 % floor matches the **community signal** for production
  Python services (e.g. FastAPI's own repo, Pydantic's own repo).
- Currently observed coverage on this repo is ~92-94 %, comfortably
  above the floor. Drops below 90 % indicate a real coverage gap
  (untested error path, untested branch) rather than expected noise.

### Property-based testing : on selected paths only

[Hypothesis](https://hypothesis.readthedocs.io/) is used **selectively**
on paths where the input space is large enough that example-based
tests miss edge cases. Concretely :

| Path | Why property-based ? | Example |
|---|---|---|
| **JWT round-trip** | Token payload is a freeform dict ; example tests cover only the obvious shapes. Hypothesis generates dicts with arbitrary keys + unicode + edge integers to catch encoding bugs. | `@given(payload=dict_strategy())` → encode → decode → assert equal |
| **Pydantic DTOs (request/response)** | Schema validators have many implicit invariants (length, regex, range). Hypothesis explores them. | `@given(payload=st.builds(CustomerCreate, ...))` → POST → assert 201 OR 422 with predictable error |
| **LIFO buffer (Redis recent customers)** | Buffer behaviour under concurrent push/pop is tricky. Hypothesis generates random push/pop sequences + asserts invariants (LIFO order, max-N elements). | `@given(ops=st.lists(push_or_pop_strategy()))` → apply → assert state |
| **Pagination boundaries** | Off-by-one bugs hide at page boundaries. Hypothesis generates random page-size + total-count combinations. | `@given(total=st.integers(0, 1000), page_size=st.integers(1, 50))` |

**Where NOT used** :

- **Unit tests of pure logic** (calculator, formatter, mapper) — example
  tests are sufficient and faster to read. Property tests add overhead
  (Hypothesis runs ~100 examples per test) without proportional gain.
- **Integration tests** — Hypothesis composes poorly with fixture setup
  (each example would re-seed DB, taking minutes). Use example-based
  integration tests with `pytest.mark.integration`.
- **HTTP endpoint smoke tests** — covered by `bin/dev/api-smoke.sh`
  (curl-based) ; property-based here would cost more than it
  gains.

### How the two interact

Property tests count toward coverage (each `@given` example is a
regular pytest run with coverage instrumentation), so adding a
property test on a thinly-tested path can lift coverage above 90 %
faster than writing 10 example tests. This is the preferred route
when an existing module is below the floor : add a property test for
the central function rather than 5 example tests for branches.

## Consequences

### Positive

- **Single, communicable threshold** : "90 % or above" is the rule.
  No table to memorise.
- **Property tests catch real bugs that example tests miss** : 3 bugs
  caught during the 2026-04 push (JWT roundtrip with unicode payload,
  pagination off-by-one at total=0, LIFO buffer eviction order) that
  example tests would not have surfaced.
- **stability-check section 3 = clear release gate** : amber finding
  below 90 %, green at-or-above, integrated with the 6-section preflight
  ([ADR-0013](0013-stability-check-six-section-design.md)).

### Negative

- **Hypothesis adds CI time** : ~+15 % on pytest runs (100 examples
  per `@given` test ; deadline=200ms per example). Mitigated by :
  selective use (4 paths today, growing to ~10 max).
- **Hypothesis sometimes flakes** on time-sensitive code. Mitigated by :
  `@settings(deadline=None)` on the rare cases where the operation
  is intrinsically slow (e.g. bcrypt password hashing in JWT round-trip).
- **Single global threshold can mask sub-module gaps** : a critical
  domain file at 70 % coverage is hidden by an over-tested glue file
  at 99 %. Mitigated by : code review + the per-file `--cov-report=term`
  output being readable in stability-check, even if not enforced.

### Neutral

- **CI does NOT hard-fail at <90 %** by design (transient drops happen).
  The maintainer treats it as a release-gate signal, not a merge
  blocker. If we ever scale past solo maintainership, flipping to
  hard-fail becomes the right move (1-line change in `pyproject.toml`
  `[tool.pytest.ini_options] minversion = "..."` + add
  `--cov-fail-under=90`).
- **Hypothesis vs schemathesis** : we use Hypothesis directly rather than
  Schemathesis (which generates fuzz tests from OpenAPI). Schemathesis
  is more cohesive for HTTP-level fuzzing but adds a heavy runtime
  layer. Today's selective Hypothesis use covers our needs ; we can
  add Schemathesis later if HTTP fuzz becomes a priority.

## Alternatives considered

### Per-module thresholds (Java pattern)

Mirror Java's JaCoCo per-package thresholds in `pyproject.toml`'s
`[tool.coverage.report]` `fail_under` per-section (via custom plugin).
Rejected for the rigidity argument above + the lack of a clean
domain/infra split in current Python codebase.

### CI hard-fail at <90 %

Add `--cov-fail-under=90` to the pytest invocation in CI. Rejected for
solo-maintainer flakiness reasons ; can be flipped on later.

### No property-based testing at all

Stick to example-based testing. Rejected because Hypothesis already
caught 3 real bugs during 2026-04 ; the ROI is positive on the
selected paths.

### Schemathesis for HTTP fuzzing

Use Schemathesis to auto-fuzz the FastAPI endpoints from OpenAPI.
Deferred for now — `bin/dev/api-smoke.sh` covers happy paths,
property tests on Pydantic DTOs cover schema invariants. Schemathesis
becomes attractive when we have endpoints with complex stateful
interactions (e.g. multi-step workflows) — not yet.

## References

- [`pyproject.toml`](https://gitlab.com/iris-7/iris-service-python/-/blob/main/pyproject.toml) `[tool.pytest.ini_options]` + `[tool.coverage.report]`
- [`tests/unit/test_jwt_roundtrip.py`](https://gitlab.com/iris-7/iris-service-python/-/blob/main/tests/unit/test_jwt_roundtrip.py) — example of `@given(payload=...)` use
- [`bin/dev/stability-check.sh`](https://gitlab.com/iris-7/iris-service-python/-/blob/main/bin/dev/stability-check.sh) section 3 — coverage gate
- [ADR-0011 — Hypothesis property-based testing](0011-hypothesis-property-based-testing.md) — the foundational decision
- [ADR-0013 — stability-check 6-section design](0013-stability-check-six-section-design.md) — the script that enforces this
- [ADR-0007 — Industrial Python best practices](0007-industrial-python-best-practices.md) — broader quality bar
