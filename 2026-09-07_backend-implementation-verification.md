# Instagram Insights Collector MVP — Backend 実装・検証記録

- 作成日: 2026-09-07
- 担当: 西村 朱莉（System Integration / Backend Data・Integration）
- Company Task: `TASK-20260907-231025-ACF2`
- 一次検収修正Task: `TASK-20260907-231913-DCEF`
- 対象媒体: Instagram
- 目的: Reels insight の収集・冪等保存を担う Collector Lambda application code を実装する
- 状態: `work_completed`（System QA / 佐伯 真琴の最終検収は未実施）

## 実装成果物

- `backend/src/collector/handler.py`: Lambda composition root。SSM SecureString からのみ token を取得し、環境設定を検証する。
- `backend/src/collector/instagram.py`: `graph.instagram.com/v26.0` client、opaque `paging.next` を終端までそのまま追従、timeout、1/2/4 秒 jitter retry、短い `Retry-After`、失敗分類。
- `backend/src/collector/service.py`: UTC scheduled slot、VIDEO+REELS allowlist、30日未満判定、部分失敗契約、RUN audit item、構造化ログ/metrics。
- `backend/src/collector/repository.py`: MEDIA/OBS/RUN mapper、アカウント分離の sparse GSI page walk、`attribute_not_exists(PK) OR status <> SUCCESS` による OBS 条件付き保存。
- `backend/src/collector/observability.py`: token、Authorization、OAuth code、URL query を入れ子を含め redaction。CloudWatchへ allowlist済み6種の custom metric を送信する。
- `backend/tests/`: cursor複数ページ fixture と unit tests。

AWS resource 定義、IAM、CloudFormation/SAM、Scheduler、GitHub Actions は一切変更していない。分析、MCP、Vercel は追加していない。

## 入出力・失敗・再実行契約

入力は `scheduled_time` を含む Scheduler event。UTCの正時へ切り下げた元イベントのslotを再試行でも維持する。MEDIA itemは `VIDEO` かつ `REELS` のみ登録し、`slot_start < published_at + 30 days` の間のみ insight API を呼ぶ。ちょうど30日以後は COMPLETE 化し、OBSは削除しない。

OBS key は `MEDIA#<media_id> / OBS#<slot>`。ERROR/PARTIAL は SUCCESS への回復を許可する一方、SUCCESS は終端で上書き拒否する。APIの空データはゼロ補完せず `missing_metrics` に保存する。

MEDIA key は `ACCOUNT#<ig_account_id> / MEDIA#<media_id>`。ACTIVE のみが sparse GSI に存在し、`GSI1PK=ACCOUNT#<ig_account_id>#ACTIVE`、`GSI1SK=<next_due_at_iso>#<media_id>` とする。登録時とadvance時にこの組を設定し、queryはアカウントのpartitionとslot上限で取得し、complete時は両GSI属性を削除する。

`TRANSIENT`、`RATE_LIMITED`、`AUTH_EXPIRED`、`DDB_THROTTLED`、`CODE_CONFIG` は起動を失敗にして Lambda async retry/DLQ 契約へ渡す。`MEDIA_API` はそのメディアの `PARTIAL` として記録し、残件を継続する。AUTH_EXPIRED は残件を停止する。Platformが template 側で reserved concurrency 1、async retry 2、event age 1h、DLQ を固定する必要がある（本Taskの変更対象外）。

## 検証結果

実行コマンド:

```sh
cd workspaces/system_integration/projects/instagram-insights-collector/backend
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src
git -C <repo> diff --check -- workspaces/system_integration/projects/instagram-insights-collector
```

結果: passed（unit test 12/12、compile、diff whitespace check）。確認済みのケースは次のとおり。

- `InstagramClient` が実際に受け取ったopaque `paging.next` URLを改変せず、終端まで追従する。
- `InstagramClient` の429で短い `Retry-After` 後に成功すること、待機上限超過とretry予算枯渇が `RATE_LIMITED` になること。
- DynamoDBのregister/query/advance/completeが、アカウント分離された sparse GSIキー契約を一貫して使用・削除すること。
- serviceのfixture経路での複数ページReels allowlist、空metricの非ゼロ補完、ERROR→SUCCESS、SUCCESS上書き拒否、29日23時台と30日境界、AUTH_EXPIRED停止、UTC slot、保存schema、secret redaction。

5xx/transportの1/2/4秒jitter retryは実装済みだが、本Taskのunit test追加対象は429契約であり、実際のネットワーク・Meta APIを用いたretry検証はしていない。

## 残存ゲート

IG-G1〜G5 は認証情報および対象Meta App/Professional accountが未提供のため、実機未確認である。モック実装・fixtureテストは止めていない。

| Gate | 状態 | 実機で必要な確認 |
| --- | --- | --- |
| IG-G1 | pending | Professional account / Standard Access / App Dashboard登録 |
| IG-G2 | pending | OAuth、長期token、`expires_in`、更新手順 |
| IG-G3 | pending | media fields と cursor 全走査 |
| IG-G4 | pending | 各metricのReels互換性、型、単位。確認後 `INSIGHT_METRICS` manifest を固定 |
| IG-G5 | pending | rate-limit header、429、実効quota |

System QAの統合検証（Scheduler/DynamoDB/SSM/DLQ/alarms、実機 API、deployment再現性）は未実施であり、リリース可能・最終完了ではない。
