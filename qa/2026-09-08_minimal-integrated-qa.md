# Instagram Insights Collector 最小構成・統合QA証跡

## メタデータ

- Company Task: `TASK-20260908-094530-9784`
- QA担当: 藤井 七海（System Integration / QA）
- 検証日時: 2026-09-08 09:55 JST
- 対象: `workspaces/system_integration/projects/instagram-insights-collector/`、`.github/workflows/instagram-insights-collector.yml`
- 参照Task: architecture `TASK-20260907-232607-D242`、Backend `TASK-20260908-092514-DB43`、Platform `TASK-20260908-092530-DCE7` / `TASK-20260908-093800-300E` / `TASK-20260908-094209-982F`
- 判定: **passed（資格情報を必要としないローカル・静的統合QA）**
- リリース判定: **保留**。AWS/Instagram資格情報がなく、実deploy、実Scheduler起動、実DynamoDB保存、Instagram Graph API疎通は未検証である。これらをpassedとは扱わない。

## 対象版の識別

検証時点の主要ファイルSHA-256:

| ファイル | SHA-256 |
| --- | --- |
| `infra/template.yaml` | `5213583b7ff53c150daae1afb542b4ff364131fd7a4dc5653bbd6626706a883b` |
| `infra/README.md` | `2093d0d9a9949642393eae85b1e4ac32ed165113458b4d64ce811502a1636b9d` |
| `backend/README.md` | `d484c1e5eada402286e044d2abc1e8f6028ba4509ac6cbe1798e51883e15a2ba` |
| `backend/src/collector/service.py` | `0f6bc2c75dca9c9f3d852dd83fe4e0e5c7799d76d73e576677a6cf52b7f7481f` |
| `backend/src/collector/repository.py` | `1870cafc150096b3e8b745fd9510034e31da37468e351ee52d0b15886c1ebb95` |
| `backend/tests/test_collector.py` | `a1ac3b9f9d769ae8e5b7bd19a15ffeab846dac84d9a362b086d933f576b6e645` |
| `.github/workflows/instagram-insights-collector.yml` | `d9dae2796a8dc124953e0eb02ee2c3fcd60237962a9a19198769f5c3847a6241` |

## 1. 最小構成

`infra/template.yaml`をリソース単位で確認した。

| 確認項目 | 実測 | 判定 |
| --- | --- | --- |
| EventBridge Scheduler | `AWS::Scheduler::Schedule` 1個、`cron(0 * * * ? *)`、UTC | passed |
| Lambda | `AWS::Serverless::Function` 1個 | passed |
| DynamoDB | `AWS::DynamoDB::Table` 1個、PK/SKのみ | passed |
| 必須付帯リソース | IAM Role 2個、LogGroup 1個 | passed |
| 禁止構成 | SQS、SNS、Alarm、PITR、SSM、GSI、custom metricsはいずれも定義なし | passed |
| 最小IAM | LambdaはLog書込と対象tableのGetItem/PutItem、Schedulerは対象LambdaのInvokeFunctionのみ | passed |

静的assertionで各リソース数と禁止語を照合し、`static_contract_assertions=passed`を確認した。DynamoDB tableとLogGroupには削除・更新置換時のRetainがあり、READMEにはScheduler停止、直前承認版への再deploy、table/log保持の復旧手順が記載されている。復旧手順の実環境実行は残存ゲートとする。

## 2. Backend unit / 振る舞い

実行コマンド:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend/src python3 -m unittest discover -s backend/tests -v
```

結果: **12 tests / 12 passed / 0 failed / 0 errors**。

確認した12テスト:

1. `test_auth_and_rate_limit_failures_are_classified_after_partial_progress`
2. `test_conditional_put_collision_is_idempotent`
3. `test_dynamo_mapper_uses_only_observation_key_and_conditional_put`
4. `test_exactly_30_days_old_media_is_excluded`
5. `test_long_retry_after_is_rate_limited`
6. `test_media_api_failure_keeps_scanning_but_fails_lambda_without_error_item`
7. `test_missing_metric_creates_partial_item_without_zero_fill`
8. `test_real_client_follows_opaque_paging_next_to_terminal_page`
9. `test_retry_exhaustion_is_rate_limited`
10. `test_same_media_and_utc_slot_is_idempotent_before_insights`
11. `test_short_retry_after_then_success`
12. `test_structured_logs_redact_secret_and_do_not_log_exception_message`

要件との対応:

| 要件 | 実装・テスト根拠 | 判定 |
| --- | --- | --- |
| Reels限定 | `media_type == VIDEO`かつ`media_product_type == REELS` | passed |
| 厳密30日未満 | `slot < published_at + timedelta(days=30)`。ちょうど30日は除外するテストあり | passed |
| cursor終端 | APIのopaqueな`paging.next`をそのまま追跡し、`next`なしで終了 | passed |
| `media_id + UTC枠`冪等 | PK=`MEDIA#<id>`、SK=`OBS#<UTC slot>`。insights取得前のGetItemで既存を除外 | passed |
| 条件付きPut | `attribute_not_exists(PK) AND attribute_not_exists(SK)`。競合は既保存として扱う | passed |
| missing metric | 値を0補完せず`missing_metrics`へ記録し、item status=`PARTIAL` | passed |
| 部分失敗・認証・429 | media単位で継続し、最後に分類済み集約失敗。短いRetry-Afterのみ再試行し、長時間/枯渇は`RATE_LIMITED` | passed |
| token非露出 | 構造化ログは例外メッセージを出さず、token/Authorization/URL queryをredactするテストあり | passed |

## 3. Scheduler / Lambda / SAM / workflow契約

| 確認項目 | 実測 | 判定 |
| --- | --- | --- |
| Scheduler Input | `scheduled_time`に`<aws.scheduler.scheduled-time>`、`trigger`に固定識別子 | passed |
| handler event | `scheduled_time`必須文字列として読み、UTC正時へ正規化 | passed |
| CodeUri / Handler | `../backend/src/` / `collector.handler.lambda_handler`。実ファイルあり | passed |
| env var | template、handler、infra/backend READMEでtable、account ID、access token、API version、metricsが整合 | passed |
| README | 最小構成、設定、検証、復旧記述が実装と整合 | passed |
| SAM validate | SAM CLI 1.165.0、`sam validate --lint --template-file infra/template.yaml`成功 | passed |
| SAM build | 隔離した`/private/tmp`配下へbuildし、`Build Succeeded`。生成Lambdaコードの`compileall`も成功 | passed |
| workflow YAML | Ruby Psych ASTで構文解析成功。path filter、validate→deploy依存、main限定、protected `production` environmentを確認 | passed |
| secret取扱い | GitHub Environment secret参照、OIDC、`NoEcho` parameter、`set +x`、値非表示の存在確認を確認 | passed |

SAM validate/build終了後、SAM CLIがsandbox外の`~/.aws-sam/metadata.json`へ書き込めずPermissionError警告を出したが、両コマンドのexit codeは0で、validate/build本体は成功した。製品コードの不具合とは判定しない。

ローカル環境に`actionlint`と`gitleaks`はなく、GitHub Actionsランナー上でのworkflow実行およびgitleaks action実行はしていない。workflowの静的構文・設定確認はpassed、CI実行結果は残存ゲートとする。

## 4. DynamoDB保存item

`observation_item()`の保存フィールドは次の観測データのみだった。

```text
PK, SK, instagram_account_id, media_id, media_type, media_product_type,
published_at, slot_start, observed_at, elapsed_seconds, metrics,
missing_metrics, api_version, status
```

- PK/SKは観測キー`MEDIA#...` / `OBS#...`のみ。
- `RUN`、`CONTROL`、`ERROR`、media registry itemを生成・保存するコードなし。
- DynamoDB操作は観測キーの`GetItem`と観測itemの条件付き`PutItem`のみ。
- `status`は観測応答の完全性を示す`SUCCESS` / `PARTIAL`であり、制御状態ではない。

判定: **passed**。

## 残存リリースゲート

検証環境では`INSTAGRAM_ACCESS_TOKEN`、`INSTAGRAM_ACCOUNT_ID`、AWS profile/access key/secret/regionが未設定で、`aws sts get-caller-identity`も`NoCredentials`となった。このため次を未検証とし、**リリース可能判定を保留**する。

1. AWS CloudFormation/SAM実deployおよび実リソース構成の確認
2. Lambda DryRun、Scheduler `ENABLED`、毎正時の実起動
3. 実DynamoDBへのitem shape、条件付きPut、同一枠再実行の確認
4. 実Instagram Graph APIの権限、Reels paging、metric可用性、認証失効・429挙動
5. GitHub Actions validate/deploy/post-deploy jobとgitleaks actionの成功
6. 実環境でのScheduler停止・承認版再deployによる復旧確認

## 最終所見

資格情報不要の統合QA範囲では、受入条件を阻害する不具合は検出しなかった。**ローカル・静的QAはpassed**とし、本証跡を佐伯 真琴へ提出する。上記残存ゲートがpassedになるまでは、実環境リリース可能またはE2E passedとは扱わない。
