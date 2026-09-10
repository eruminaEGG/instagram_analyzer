# Post index / final analysis boundary implementation verification

- Task: `TASK-20260910-200918-7AD8`
- Date: 2026-09-10
- Owner: 西村 朱莉 (System Integration / Backend)
- Scope: same-table post index, future final-analysis storage boundary, and optional `follows` observation metric.

## Implemented

- Every discovered `VIDEO` + `REELS` media object receives an idempotent post-list item at `ACCOUNT#<account_id>` / `MEDIA#<published_at>#<media_id>`, including permalink, publication time, media identifiers, `analysis_due_at`, and `analysis_status: pending`.
- Post-list refreshes retain an existing analysis status and use only strongly consistent `GetItem` plus conditional `PutItem`; no table, index, service, or IAM expansion was made.
- The repository provides `final_analysis_item` and a conditional `save_final_analysis` boundary for isolated `MEDIA#<media_id>` / `ANALYSIS#FINAL#<version>` records. No AI request is made by the collector.
- The Instagram media request asks for optional `caption`. Omission does not block collection.
- Existing configured insights are fetched first. `follows` is requested separately; only numeric values are stored. Unsupported/absent `follows` is recorded in `missing_metrics`, with no zero fill and no loss of the other values.
- The observation key, hourly UTC slot, strict 30-day cutoff, conditional idempotency, error classification, and secret-safe logging remain unchanged.

## Verification

Command run from `backend/`:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Result: **passed — 23 tests**.

Coverage includes opaque Instagram paging, short/long/retry-exhausted 429 behavior, exact 30-day boundary, observation idempotency, post-index data and outside-window indexing, conditional and version-isolated final-analysis records, numeric and unsupported `follows`, partial failure continuation, and secret redaction.

## Changed files

- `backend/src/collector/instagram.py`
- `backend/src/collector/repository.py`
- `backend/src/collector/service.py`
- `backend/tests/test_collector.py`
- `backend/README.md`
- `2026-09-10_post-index-final-analysis-verification.md`

## Remaining gates

- Real Instagram Graph API validation for `caption` and `follows` compatibility remains credential-gated and was not inferred from local mocks.
- DynamoDB/AWS integration remains credential-gated; local tests verify the request/key/conditional-write contract only.
- Director review and required independent QA remain pending; this is implementation self-verification, not release approval.
