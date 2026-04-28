# ADR-0011 : Hypothesis property-based testing on selected paths

**Status** : Accepted
**Date** : 2026-04-25
**Sibling** : `iris-service-java` uses jqwik for property-based tests
(same paradigm, JVM idiom).

## Context

Example-based tests (`test_foo_returns_42` for input 5) cover the cases the
author thought of. They miss :
- **Edge cases** : empty strings, max-int boundaries, unicode normalisation,
  IDNA punycode in emails.
- **Adversarial input** : SQL injection patterns in string fields, control
  characters, malformed UTF-8.
- **Combinatorial bugs** : invariants that hold for `(a, b)` and `(c, d)`
  but break for `(a, d)` because the test data was always paired the same way.

Property-based testing flips the paradigm : declare a PROPERTY (e.g. "for
any valid `(username, role)`, `decode(encode(x)) == x`"), and the tool
generates 100s of inputs trying to find a counter-example.

## Decision

Use **[Hypothesis](https://hypothesis.readthedocs.io/)** (>=6.122) for
property-based testing on **selected paths only** :

1. **JWT round-trip** (`tests/unit/test_property_based.py`) : for any
   `(username, role)`, `decode(encode(x))` returns the original claims.
2. **Customer DTO bounds** : for any name in `[2, 120]` chars + valid
   email, `CustomerCreate.model_validate({...})` succeeds.
3. **RecentCustomerBuffer LIFO invariant** : whatever the sequence of
   `add()` calls, `get_recent()` returns at most MAX_SIZE items in
   reverse-chronological order.

NOT used for :
- Trivial CRUD wiring (the property would be "stuff goes in, stuff comes
  out" — example tests are clearer).
- I/O-heavy tests (DB / Kafka / HTTP) — the cost of 100 generations × IO
  is wasted on infrastructure rather than logic invariants.

## Consequences

**Pros** :
- **Real bugs caught during authoring** (2026-04-25 dev session) :
  - Pydantic v2 EmailStr applies IDNA punycode decoding (`xn--11b4c3d` →
    `कॉम`) on non-ASCII domains. The naive assertion
    `dto.email == input_email.lower()` failed because the round-trip is
    NOT a string identity. Test rewritten to decompose local + domain.
  - LIFO buffer test had a spec-vs-mock state-mixing bug : both the
    expected-state list and the mock-store list were the same variable,
    causing double-mutation. Hypothesis surfaced it within seconds.
- **Shrinking** : when Hypothesis finds a counter-example, it shrinks to
  the SMALLEST input that still fails — debugging is fast.
- **Coverage of edge cases the author wouldn't think of** : empty strings,
  whitespace-only, unicode emoji, max-length strings.
- **Selected-paths discipline** : the cognitive cost of writing properties
  is non-trivial ; restricting to high-value paths keeps the team's energy
  focused.

**Cons** :
- **Slower than example tests** : 100 examples × ~5ms each = 500ms per test.
  Mitigated via per-test settings (`max_examples=50` for crypto-bound JWT).
- **Property design is an art** : a too-permissive property doesn't catch
  bugs ; a too-strict property finds false positives. Documented in test
  docstrings : "Catches : signing/verifying byte-encoding mismatches, ..."
- **Skill ramp** : new contributors need to learn Hypothesis strategies
  (`st.text`, `st.builds`, `unique_by`, `filter`). Mitigation : 8 example
  tests in the repo serve as the on-ramp.

**Alternatives considered** :

| Library | Why not |
|---|---|
| **Faker / mimesis** | Generates fake data but doesn't shrink on failure ; no property invariants |
| **pytest-quickcheck** | Older, abandoned upstream ; Hypothesis is the de-facto successor |
| **schemathesis** | Property-based for OpenAPI schemas — useful future addition for API contract tests, complementary not replacement |

## Validation

- 8 hypothesis tests in `tests/unit/test_property_based.py`.
- Total runtime : < 1s on the property suite (50-100 examples each, capped
  via `_JWT_PROFILE` settings for crypto-bound tests).
- Real bugs found during authoring : 2 (documented in ADR + commit messages).
- pytest fixture integration : works with `pytest-asyncio` async fixtures
  via `@pytest.mark.asyncio` on the test fn.

## See also

- ADR-0007 : Industrial Python practices §4 (selected paths rationale)
- [Hypothesis docs](https://hypothesis.readthedocs.io/)
- [QuickCheck (the original Haskell PBT)](https://hackage.haskell.org/package/QuickCheck)
- [jqwik (Java PBT, sibling repo)](https://jqwik.net/)
