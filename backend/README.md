# Instagram Insights Collector backend

This directory contains the approved minimal, single-Lambda collector. It has no
GSI query, SSM read, RUN/CONTROL/ERROR item, or custom CloudWatch metric path.

## Runtime contract

The Scheduler event must provide `scheduled_time`. The Lambda normalizes it to an
hour-aligned UTC `slot_start`, then follows the Instagram `paging.next` URL until
there is no next page. A media object is collected only when all conditions hold:

- `media_type` is `VIDEO`;
- `media_product_type` is `REELS`;
- `slot_start` is strictly earlier than `published_at + 30 days`.

For each discovered Reel, it idempotently maintains a same-table post-list item
at `ACCOUNT#<instagram_account_id>` / `MEDIA#<published_at>#<media_id>`. It
contains `media_id`, `published_at`, `permalink`, `media_type`,
`media_product_type`, `analysis_due_at` (published time plus 30 days), and an
initial `analysis_status` of `pending`. `caption` is included only when the API
returns it. A refresh retains a later analysis status using only the existing
`GetItem` and conditional-`PutItem` permissions.

For each observation candidate, it performs a strongly consistent `GetItem` for
`MEDIA#<media_id>` / `OBS#<slot_start>`. Existing observations do not call the
insights endpoint. New observations are fetched and written with
`attribute_not_exists(PK) AND attribute_not_exists(SK)`.

Only an observation item is written. Its required attributes are `PK`, `SK`,
`instagram_account_id`, `media_id`, `media_type`, `media_product_type`,
`published_at`, `slot_start`, `observed_at`, `elapsed_seconds`, `metrics`,
`missing_metrics`, `api_version`, and `status`. `status` is `SUCCESS` or
`PARTIAL`; missing metrics are listed and never zero-filled. The collector
requests `follows` separately from the configured metrics: a numeric response is
saved, while an unsupported or absent value is listed in `missing_metrics`
without losing the other insight values.

No AI call is made. The repository exposes an intentionally unused future
boundary for conditional final-analysis items at
`MEDIA#<media_id>` / `ANALYSIS#FINAL#<version>`. That versioned record cannot
overwrite an `OBS#...` item.

When an API failure affects one media item, collection continues for other media.
The Lambda writes secret-safe structured standard logs and fails after the scan so
the failed hourly slot is visible. It writes no error item. The Instagram client
retries only a short explicit `Retry-After`; authentication, long waits, and
retry exhaustion are classified without including response bodies or credentials
in logs.

Required environment variables:

- `INSTAGRAM_ACCOUNT_ID`
- `DYNAMODB_TABLE_NAME`
- `INSIGHT_METRICS` (comma-separated Instagram metric names)
- `INSTAGRAM_ACCESS_TOKEN`

Optional variables are `API_VERSION` (default `v26.0`), `GRAPH_HOST`, and
`HTTP_TIMEOUT_SECONDS`.

## Local verification

From this directory, run:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The suite covers real-client opaque paging, rate-limit handling, the exact
30-day boundary, post-index and observation idempotency, versioned final
analysis isolation, numeric/unsupported `follows`, partial metrics, per-media
API failures, and secret-safe logs. External Instagram and AWS integration
remain credential-gated; see the project verification record.
