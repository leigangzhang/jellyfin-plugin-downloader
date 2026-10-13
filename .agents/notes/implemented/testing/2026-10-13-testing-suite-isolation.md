# Decision: Test suite isolation — stub the HTTP boundary, and make the stub prove it was called

English | [中文](2026-10-13-testing-suite-isolation.zh.md)

Status: implemented
Date: 2026-10-13
Class: testing

## Problem

A test module whose docstring promised "all offline" was talking to a live third-party
index on every run. Two integration cases patched `search_pool.search_api`, but
`fetch_query_pages(..., search=search_api)` binds that default at definition time, so the
patch never reached the pager and real HTTP went out under a test that claimed to be
offline. Measured with a socket hook, the module produced 96 real connections to
`m.diao.im` in 39–50s per run, sending the test's search terms to a third party. The same
trap had already fooled an independent review script built to check that very claim, which
is what makes it worth a record rather than a note: the isolation was asserted in prose,
and the assertion that would have caught it did not exist.

## Decision

Index access in tests is stubbed at the HTTP boundary, and the stub is asserted.

- `tests/test_backend_integration.py` replaces `search_pool.urllib.request.urlopen` with a
  `FakeIndexHttp` that serves fixture pages, and each call site asserts that the stub was
  actually asked (`assertTrue(index_http.requests)`), so a stub that is bypassed fails the
  test instead of silently passing it.
- The pattern is not new: `tests/test_backend_sources.py` already stubbed that boundary and
  documents why patching the module attribute does not reach the pager. This decision makes
  it the rule for the integration module too.
- `backend/search_pool.py` is left alone — the repair is on the test side, because the
  definition-time binding is correct production behaviour and the test was the thing
  making a false claim.

## Alternatives considered

**Give `fetch_query_pages` no default and pass `search` at the call site.** It would make
the seam injectable by plainly patching the module attribute, but it changes production
code to accommodate a test, and the test-side fix leaves the production path untouched.

**Keep the module-attribute patch and correct the docstring.** Declined: the tests would
still send live queries, and the failure mode — a patch that silently misses — stays
invisible.

**Delete the integration module and rely on the per-module tests.** Declined: the
end-to-end property this record's sibling decision needs (an external candidate reaching
the ranking) only exists at that level, and coverage would drop rather than the trap.

**Trust the docstring and add no assertion on the stub.** Declined: that is the state this
record exists to end.

## Consequences

The module runs in about 10s instead of 39–50s and makes no external calls; the full suite
drops from ~50s to ~26s, and a machine without a route to the index runs the same tests
rather than 96 fast failures. The cost is a fixture to maintain and one more assertion per
call site, and the guard only covers what it stubs: a leaked connection from a subprocess
or an already-open socket would not be seen by this hook. A related trap is now written
down for the next reader — `tests/test_backend_integration.py` still has one call site
whose ladder is empty for its title, so its `assertTrue(requests)` cannot fail today; the
non-strict call site carries the unconditional assertion.

## Testing

- [C1] `tests` — `tests/test_backend_integration.py` carries the boundary stub and the
  stub-was-called assertions; reverting the stub to a module-attribute patch produced 192
  external events with three failing assertions.
- [C2] `source` — the red proof for the pair of guards sharing this seam needs **both**
  sides reverted (the writer in `search_pool.py` and the read guard in `probe_magnets.py`);
  reverting one side alone stays green, and that is recorded so a reviewer does not read it
  as an assertion without teeth.
- [C3] `tests` — `tools/check-invariants.py` keeps the surrounding rules; a first draft
  that synchronised with `time.sleep` instead of `os.utime` was rejected by the
  no-time-based test-sync rule.
