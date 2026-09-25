# Gate 2 — Data Readiness Review

## Pipeline run

- [ ] Complete flow runs with one command
- [ ] Same run can be safely repeated
- [ ] Raw API responses are preserved
- [ ] Processed output is written only after validation

## Validation

| Check | Status | Evidence / note |
|---|---|---|
| Required columns |  |  |
| Critical nulls |  |  |
| Order uniqueness |  |  |
| Freshness |  |  |
| Dispatch retrieval completeness |  |  |

## Reliability

| Capability | Status | Evidence / note |
|---|---|---|
| Bounded retries |  |  |
| Useful failure message |  |  |
| Logging |  |  |
| Idempotent rerun |  |  |
| Configuration outside core logic |  |  |

## Known limitations

Document anything that remains unresolved.

Example:
- `driver_arrived_at_restaurant` is still not reliably captured.

## Gate decision

**READY / NOT READY**

Reason:
