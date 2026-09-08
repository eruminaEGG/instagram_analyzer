# Instagram Insights Collector MVP Architecture（最小化改訂前）

- Task ID: `TASK-20260907-225017-FD72`
- 作成日: 2026-09-07
- 作成者: 神谷 拓海（System Integration / Architecture）
- 依頼・最終検収: 佐伯 真琴（System Integration / Management）
- 入力正本: `2026-09-07_mvp-requirements.md`
- status: `architecture_proposed`
- 適用時点: 2026-09-07（API仕様・料金は実装開始時にも再確認する）

## 1. 結論

MVPは、1つの EventBridge Scheduler、1つの収集 Lambda、1つの DynamoDB テーブルを中核とする。LambdaはUTCの毎正時に1回起動し、(1) 対象Instagram Professionalアカウントのメディア一覧を全ページ走査して新しいReelsを登録し、(2) 投稿日時から30日未満のReelsのlifetime insightsを取得し、(3) `media_id + UTC観測時刻枠` を一意キーとして保存する。

補助リソースは、トークン用SSM Parameter Store（Standard `SecureString`）、失敗イベント用SQS DLQ、CloudWatch Logs/metrics/alarms、通知用SNS、最小権限IAMに限定する。AWS Step Functions、API Gateway、Vercel、分析、AI、MCPは含めない。

認証は **Instagram API with Instagram Login** を採用候補とする。対象は自社管理のInstagram Professionalアカウント（BusinessまたはCreator）1件、対象メディアは **`media_type=VIDEO` かつ `media_product_type=REELS`** のみとする。ホストは `graph.instagram.com`、権限は `instagram_business_basic` と `instagram_business_manage_insights`、APIは現行最新の **Graph API `v26.0` を明示指定**する。ただし、対象アカウントでのOAuth疎通、トークン更新契約、Reelsごとの利用可能指標は実装前の実機スモークテストを通過するまで確定扱いにしない。

この選定により、Facebook Page連携を前提とするFacebook Login方式の依存をMVPから外す。一方で、利用中アプリの設定やアカウント条件がInstagram Loginに適合しない場合だけ、Facebook Login方式へ設計変更する。

## 2. Scope Gateと既存構成調査

本件はarchitecture課の責務である技術設計、コンポーネント境界、契約、障害境界、移行・復旧方針に該当する。実装、デプロイ、媒体運用判断は行わない。

調査結果は次のとおり。

- プロジェクト配下には入力要件 `2026-09-07_mvp-requirements.md` のみがあり、実装、CloudFormation/SAMテンプレート、GitHub Actions workflow、既存テーブルはない。
- リポジトリ内にも本プロジェクトが再利用できるCloudFormation/SAM、Serverless Framework、GitHub Actionsデプロイ構成は確認できなかった。
- したがって既存挙動や既存データの移行はない。新規の最小構成として開始する。
- 後続実装で既存共通基盤が提示された場合は、同等の責任境界を維持できる範囲でそれを優先し、本書との差分をDecision Recordへ残す。

## 3. 公式仕様の確認結果と実装ゲート

### 3.1 確認済み

Meta公式のInstagram APIコレクションでは、Instagram APIはBusiness/CreatorのProfessionalアカウントを対象とし、mediaとinsightsを取得できる。Instagram Loginでinsightsを読む場合は、Instagram User access token、`graph.instagram.com`、`instagram_business_basic`、`instagram_business_manage_insights` が必要である。自社が所有・管理しApp Dashboardへ追加したアカウントはStandard Access、他社アカウントを扱う場合はAdvanced Accessが必要とされる。本MVPは前者だけを対象とする。

Media Insightsは所有するProfessionalアカウントのメディアに限られ、値が存在しない、または現在利用できない指標は `0` ではなく空データになる場合がある。応答時刻はUTCのISO-8601である。したがって未返却指標をゼロ補完しない。

公式コレクションに掲載されるmedia insights候補から、Reels用の初期候補を次に限定する。

| 区分 | 指標候補 | 保存型 | 扱い |
| --- | --- | --- | --- |
| 基本 | `views`, `reach`, `likes`, `comments`, `saved`, `shares`, `total_interactions` | Number | 実機でReelsに対する返却を確認後、設定へ固定 |
| 視聴 | `ig_reels_avg_watch_time`, `ig_reels_video_view_total_time` | Number | 単位を実レスポンスと公式リファレンスで確認後、設定へ固定 |

公式一覧にある `plays`、`ig_reels_aggregated_all_plays_count`、`clips_replays_count`、`reels_skip_rate`、`reposts`、`crossposted_views` 等は、互換性・廃止・アカウント条件を実機確認するまでMVPの必須指標にしない。指標リストはLambdaコードへ散在させず、環境別の非秘密設定 `INSIGHT_METRICS` として固定し、使用値を各snapshotにも保存する。

Graph APIはバージョンをURLに明示する。2026-09-07時点の最新はv26.0であるため `API_VERSION=v26.0` を初期値とし、無指定・自動フォールフォワードを禁止する。

### 3.2 未確定で、実装着手前に必ず確認する事項

| ID | 未確定事項 | 検証方法 | 合格条件 | 不合格時 |
| --- | --- | --- | --- | --- |
| IG-G1 | 対象アカウントがBusiness/Creatorで、自社管理・App Dashboard登録済みか | アカウント管理者とMeta App設定を照合 | Standard Accessで対象user/mediaへアクセス可能 | PMへ返却。Advanced Access申請はMVPに無断追加しない |
| IG-G2 | Instagram LoginのOAuth、長期利用トークンへの交換・更新期限・更新可能時期 | Meta公式Access Token文書と対象Appの実レスポンス `expires_in` を記録 | 発行、更新、失効時の再認可手順と期限を運用手順へ固定 | Facebook Login案を再評価、佐伯承認 |
| IG-G3 | `/{ig-user-id}/media` が返す `media_type`, `media_product_type`, `timestamp` と全ページ走査 | 対象アカウントでread-onlyスモークテスト | Reelsを一意に識別でき、cursorで最後まで取得可能 | 自動検出方式を再設計し、実装を開始しない |
| IG-G4 | 上記9指標のReels互換性、型、単位、空データ | 新旧Reels各1本以上で `/{media-id}/insights` を実行 | 採用指標ごとのレスポンス名、型、単位が記録済み | 非対応指標を設定から除外し、要件者へ差分確認 |
| IG-G5 | レート制限ヘッダーと対象Appの実効クォータ | 正常応答と429応答でヘッダー、エラーコード、`Retry-After`を記録 | 実装が参照できる利用率またはリセット情報を特定 | 保守的な直列化・429停止のみで開始し、投稿数上限をPM決定 |

トークン寿命を推測値で実装しない。IG-G2で確認した `expires_at` を非秘密メタデータとして保持し、期限14日前と7日前に警告する。MVPではOAuth UIや自動認可サーバーを追加せず、管理者が公式手順で再発行・更新し、Parameter Storeの値を上書きする運用とする。

## 4. 採用構成とデータフロー

```text
EventBridge Scheduler (rate 1 hour, UTC scheduled_time)
        |
        v
Collector Lambda (reserved concurrency = 1)
   |-- SSM SecureString: access tokenを実行時取得
   |-- Instagram API: /{ig-user-id}/media を全cursor走査
   |-- DynamoDB: 新規ReelsをMEDIA itemへ冪等登録
   |-- DynamoDB GSI: ACTIVEかつnext_due_at <= scheduled_timeを取得
   |-- Instagram API: /{media-id}/insights
   |-- DynamoDB: OBS itemとRUN summaryを冪等保存
   `-- CloudWatch Logs / metrics
          | alarm
          v
         SNS -> 運用者

Scheduler配送失敗 / Lambda非同期実行の最終失敗 -> SQS DLQ
```

1. Schedulerは `cron(0 * * * ? *)`、Flexible Time Window OFFとし、入力へScheduler context attributeの予定時刻を `scheduled_time` として埋め込む。タイムゾーンはUTCを明示し、実行開始時刻からslotを再計算しない。
2. LambdaはSSMからアクセストークンを復号取得する。値、Authorizationヘッダー、OAuth URLをログへ出さない。
3. `GET /v26.0/{ig-user-id}/media?fields=id,media_type,media_product_type,timestamp,permalink` をcursorがなくなるまで走査する。公式コレクションは結果順序を保証せず、User Insights以外の時刻ページングを保証しないため、「先頭N件だけ」「投稿日で途中終了」はMVPで採用しない。
4. `VIDEO + REELS` の各mediaを条件付き更新し、初見なら `published_at`、`collect_until=published_at+30 days`、`next_due_at=現在の観測枠`、`ACTIVE` を登録する。既知IDは重複作成しない。30日以上前の初見mediaは履歴登録だけ行い、ACTIVEにはしない。
5. sparse GSIから `ACTIVE` かつ `next_due_at <= slot_start` をQueryする。各mediaについてAPI呼び出し直前にも `slot_start < collect_until` を再確認する。
6. 合格済み指標を1回の `GET /v26.0/{media-id}/insights?metric=...` で取得する。新規検出mediaも同じ起動内で初回取得する。
7. 実測値とメタデータをOBS itemへ保存する。成功保存後にMEDIA itemの `last_observed_slot` と `next_due_at=slot_start+1 hour` を進める。
8. `slot_start >= collect_until` のmediaはAPIを呼ばず `COMPLETE` にし、GSI属性を削除する。OBS itemにはTTLを設定せず保持する。
9. 起動全体の件数・失敗分類をRUN item、CloudWatch metrics、構造化ログへ記録する。

## 5. 時刻枠、30日停止、欠損の定義

- `slot_start`: Schedulerの予定時刻をUTCの正時へ切り下げた値。再試行でも元イベントの値を使い、実行時刻から再計算しない。
- `observed_at`: Instagram API応答を受けた実時刻（UTC）。
- `elapsed_seconds`: `observed_at - published_at`。丸めた分析値は作らない。
- 初回観測: 投稿検出後、同じ起動のslotへ保存する。検出遅延目標は0〜60分＋API処理遅延。
- 収集可能条件: `slot_start < published_at + 30 days`。ちょうど30日以後は呼び出さない。
- 同じslotの再試行: 同じOBS itemを更新できる。SUCCESSは終端であり上書きしない。
- 過去slotのバックフィル: Instagram Media Insightsは呼出時点のlifetime値であり、過去時点の値を復元できない。元slot内の再試行だけを許可し、1時間を越えた欠損を後日の現在値で埋めない。欠損はRUN/MEDIAへ `MISSED_UNRECOVERABLE` として残す。
- 保存保持: DynamoDB TTLを使わず永続保持する。テーブル削除・置換時にもRetentionポリシーを適用する。

要件の「1時間ごと」は投稿時刻起点の60分間隔ではなく、グローバルなUTC正時枠と定義する。これにより1スケジュールで全投稿を扱い、投稿から初回観測まで最大約1時間となる。

## 6. コンポーネント責任境界

| コンポーネント | 責務 | 持たない責務 |
| --- | --- | --- |
| EventBridge Scheduler | 毎時イベントの配送、配送失敗の再試行・DLQ | 投稿ごとのschedule、API処理、Lambda内部エラーの判定 |
| Collector Lambda | 検出、対象抽出、API呼び出し、分類、冪等保存、run集計 | 分析、表示、OAuth UI、Infrastructure作成 |
| DynamoDB | media状態、観測snapshot、run summaryの保存とACTIVE Query | 分析集計、30日後削除 |
| SSM Parameter Store | runtime tokenの暗号化保管 | 自動ローテーション、OAuth認可 |
| SQS DLQ | Scheduler配送失敗とLambda最終失敗イベントの退避 | 自動無期限リプレイ |
| CloudWatch/SNS | 30日ログ、メトリクス、アラーム、運用者通知 | 投稿分析、ビジネス評価 |
| CloudFormation/SAM | AWSリソース、IAM、設定名、alarmsを宣言 | トークン値、SNSメール購読承認、Meta App作成 |
| GitHub Actions | test/lint/package、change set、環境承認、deploy、smoke test | 長期AWSキー保管、tokenの中継、手動OAuth同意 |

EventBridge SchedulerのDLQは「Lambdaを呼び出せなかった配送失敗」を扱う。非同期Lambdaが実行後にエラーを返す場合はLambda側の非同期再試行とOnFailure destinationを同じSQS standard queueへ向ける。両者のfailure sourceをpayloadで識別する。

## 7. DynamoDB設計

テーブルは1つ、Standard table class、provisioned `5 RCU / 5 WCU`、暗号化、Point-in-Time Recovery有効を採用する。GSIも `5 RCU / 5 WCU` とし、アカウント内の他テーブル・GSIを含む無料枠消費をデプロイ前に確認する。

### 7.1 キー

| item種別 | PK | SK | 用途 |
| --- | --- | --- | --- |
| MEDIA | `ACCOUNT#<ig_account_id>` | `MEDIA#<media_id>` | 投稿の収集状態 |
| OBS | `MEDIA#<media_id>` | `OBS#<slot_start_iso>` | 1投稿・1観測枠のsnapshot |
| RUN | `RUN#<slot_start_iso>` | `SUMMARY` | 起動単位の監査・欠損確認 |
| CONTROL | `ACCOUNT#<ig_account_id>` | `CONTROL#DISCOVERY` | 最終走査結果、token期限、構成fingerprint |

MEDIA itemだけに次のGSI属性を持たせるsparse indexとする。

- `GSI1PK = ACCOUNT#<ig_account_id>#ACTIVE`
- `GSI1SK = <next_due_at_iso>#<media_id>`

完了時にGSI属性を削除するため、30日超の投稿は通常Queryから自然に除外される。GSIは結果整合のため、API呼出直前に基表の `status` と `collect_until` を再検証する。

### 7.2 属性

MEDIAには `entity_type`, `account_id`, `media_id`, `media_type`, `media_product_type`, `published_at`, `permalink`, `status`, `collect_until`, `next_due_at`, `last_observed_slot`, `last_success_at`, `consecutive_failures`, `last_error_class`, `created_at`, `updated_at` を保存する。captionや動画本体URLは不要なので保存しない。

OBSには `entity_type`, `account_id`, `media_id`, `media_type`, `media_product_type`, `published_at`, `slot_start`, `observed_at`, `elapsed_seconds`, `api_version`, `requested_metrics`（String SetまたはList）、`metrics`（Map<String, Number>）、`missing_metrics`（List<String>）、`status`（`SUCCESS|PARTIAL|ERROR`）、`error_class`, `api_error_code`, `request_trace_id`, `schema_version=1`, `created_at`, `updated_at` を保存する。APIの空データは `missing_metrics` へ入れ、`metrics[name]=0` を作らない。トークン、レスポンスヘッダー全文、個人データは保存しない。

RUNには `discovered_count`, `eligible_count`, `success_count`, `partial_count`, `error_count`, `stopped_count`, `rate_limited_count`, `duration_ms`, `status`, `error_classes`, `config_fingerprint` を保存する。

### 7.3 冪等性と整合性

- OBSの一意性は `PK=MEDIA#id + SK=OBS#slot` で保証する。
- OBSは条件付きUpdateとし、`attribute_not_exists(status) OR status <> SUCCESS` の場合だけ書く。ERROR/PARTIALからSUCCESSへの回復は許可し、SUCCESSは上書きしない。
- MEDIA登録は同一media_idへのupsertで重複しない。`published_at` と `collect_until` は初回確定後に変更しない。
- OBS保存が成功してからMEDIAのnext_dueを進める。途中停止時は同じslotが再度選ばれ、条件付き書込みで安全に収束する。
- Lambda reserved concurrencyを1にして同一アカウントの競合を抑え、DynamoDB条件式を最終防御とする。
- スキーマ変更は `schema_version` を追加して読み手側互換とする。MVP中に既存itemの一括書換えはしない。

## 8. 失敗、再試行、レート制限

| 分類 | 例 | そのslotの動作 | 通知・復旧 |
| --- | --- | --- | --- |
| `TRANSIENT` | timeout、接続失敗、5xx | 1/2/4秒＋full jitterで最大3回。同slotにERRORを残し、起動を失敗させLambda再試行へ | 継続失敗はDLQ・alarm |
| `RATE_LIMITED` | HTTP 429、利用率閾値超過 | `Retry-After`が短く実行上限内だけ待つ。残件を呼ばずERROR記録し起動失敗 | usage headerをマスク済み数値で記録、alarm、次slotまで停止可 |
| `AUTH_EXPIRED` | 401、Meta認証エラー | 直ちに残件を停止。無駄な再試行をしない | 即時alarm、token更新後に元slot内だけ再実行 |
| `PERMISSION_DENIED` | 403、権限不足 | 当該runを失敗。投稿単位の連続再試行なし | App設定を修正しsmoke test |
| `METRIC_UNAVAILABLE` | 空data、対象外指標 | PARTIAL。返った指標は保存 | 集計通知。設定変更は検証・承認後 |
| `MEDIA_UNAVAILABLE` | 削除・非公開等 | ERROR、連続3回でMEDIAを`BLOCKED` | 運用者確認。推測でCOMPLETEにしない |
| `DDB_THROTTLED` | provisioned capacity不足 | SDKのbounded retry、起動失敗 | 容量・item size確認。増強は料金確認後 |
| `CODE/CONFIG` | 例外、設定欠落 | 起動失敗 | Lambda OnFailure -> DLQ、rollback |

Metaの固定クォータ値は対象App、ユースケース、アクセスレベルに依存し得るため、本書では未確認の「200 calls/hour」等を契約値として置かない。実レスポンスに `X-App-Usage`、`X-Business-Use-Case-Usage`、`Retry-After` 等が現れるかIG-G5で確認し、確認できた利用率が80%を超えたら新規API呼び出しを止める。ヘッダーが得られない場合は直列呼び出し、同時実行1、429即停止を安全側の制御とする。

Lambda非同期設定は `MaximumRetryAttempts=2`, `MaximumEventAgeInSeconds=3600` とする。元イベントのslotを維持するので重複しない。Schedulerの配送再試行も最大イベント年齢1時間とし、最終失敗をDLQへ置く。DLQ保持は14日、visibility timeoutは運用手順に合わせる。自動redrive event sourceは作らず、原因解消後に運用者が対象イベントを確認して手動再実行する。

## 9. 秘密管理と最小権限

- Instagram access tokenは `/instagram-insights/<env>/access-token` のSSM Standard `SecureString` に保存し、AWS管理KMSキーを使う。Parameter Store standard tierは追加料金なしだが、自動ローテーションはない。
- トークン値はCloudFormation parameter、GitHub Actions secret、Lambda環境変数、リポジトリへ渡さない。Lambda環境変数にはparameter名だけを設定する。
- OAuth更新時は権限を持つ運用者が公式手順でtokenを取得し、直接AWSへ `PutParameter --overwrite` する。更新後にread-only smoke testを行い、`expires_at` と確認者をCONTROL itemへ記録する。
- GitHub ActionsはGitHub OIDCで環境別AWS roleを引き受ける。長期AWS access keyを保存しない。deploy roleは対象stackとartifact bucketだけ、Lambda runtime roleは当該SSM parameterのGetParameter、当該DynamoDB table/index、CloudWatch Logsへの書込みだけを許可する。
- ログ共通関数で `access_token`, `Authorization`, OAuth code、request URL queryを必ずredactする。API error bodyもallowlist項目だけを出す。
- DynamoDB、SQS、CloudWatch Logs、SAM artifact bucketは暗号化する。外向き通信だけのLambdaにVPCは追加しない（NAT Gateway費用と運用を避ける）。

## 10. CloudFormation/SAMとGitHub Actionsの境界

### CloudFormation/SAMが所有するもの

- `AWS::Serverless-2016-10-31` を使うCloudFormation template（SAMはpackage/deploy補助であり、runtimeリソースの正本はstack）
- Scheduler、Lambda、DynamoDB table/GSI、SQS DLQ、SNS topic、CloudWatch log group/alarms、IAM roles/policies
- 環境別のAPI version、指標設定、account id、SSM parameter名、ログ保持、alarms閾値
- DynamoDBは `DeletionPolicy: Retain`, `UpdateReplacePolicy: Retain`、PITR有効
- log groupはRetention 30日、DLQはRetention 14日
- stack outputs（function名、table名、schedule名、DLQ URL、SNS topic ARN）

SSM SecureStringの値とSNS email購読承認はstack外の手動運用とする。SAMのmanaged artifact bucketはデプロイ成果物専用で、runtimeデータを置かない。

### GitHub Actionsが所有するもの

1. PR: backend unit test、schema/contract test、CloudFormation/SAM lint、secret scan。
2. main: GitHub OIDCで環境roleを取得し、`sam build`、`sam deploy --no-fail-on-empty-changeset --resolve-s3`。変更セットを作り、GitHub Environment承認後に本番適用する。
3. deploy後: parameterの「存在」だけを確認（値は取得・表示しない）、Lambda read-only smoke、Scheduler state、alarm状態を確認する。
4. workflowログへCloudFormation parameter値を出さない。環境変数はregion、stack名、account id、parameter名など非秘密だけ。

ロールバックは、CloudFormationの自動rollbackを有効にし、失敗時はSchedulerをdisableして前コミットのartifact/templateを再deployする。DynamoDB tableを置換・削除しない。API version/metricsの変更だけなら前設定へ戻す。データschemaの破壊的変更はMVPでは禁止する。

## 11. 監視、障害確認、復旧

### 11.1 最小監視

- CloudWatch Logs: JSON構造化ログ、30日保持。run_id、slot、media_id、結果、分類、duration、API request traceを出し、秘密は出さない。
- 標準metrics: Lambda `Errors`, `Throttles`, `Duration`, `AsyncEventsDropped`; Scheduler `TargetErrorCount`/DLQ系; SQS `ApproximateNumberOfMessagesVisible`; DynamoDB `ReadThrottleEvents`, `WriteThrottleEvents`。
- カスタムmetrics（最大6）: `RunSuccess`, `AuthFailure`, `RateLimited`, `ExpectedObservations`, `SuccessfulObservations`, `MissingObservations`。
- alarms: 2時間連続でRunSuccessなし、AuthFailure >= 1、Lambda Errors/DLQ message >= 1、MissingObservations >= 1、DynamoDB throttle >= 1。SNS emailへ通知する。
- Cost: AWS Budgetsで月額しきい値を設定する。ただしBudget自体の無料枠・料金はplatform担当が対象アカウントで確認する。

### 11.2 復旧手順の契約

1. alarmからRUN item、Lambda log、DLQ eventを照合し、配送・認証・API・保存のどこで失敗したか分類する。
2. 認証失効ならtokenをSSMへ上書きし、tokenを表示しないread-only smoke testを行う。
3. 元slotから1時間以内なら、その `scheduled_time` を明示して手動invokeする。条件付き書込みにより成功済み投稿は重複しない。
4. 1時間を超えた過去slotは、現在値を過去値として保存しない。OBS/RUNへ欠損を記録し、`MISSED_UNRECOVERABLE` として残す。
5. code/config障害はSchedulerをdisableし、前コミットを再deployする。復旧確認後にenableし、次回runのExpected/Successfulを照合する。
6. DynamoDB誤更新・削除はPITRで別テーブルへ復元し、件数とキー重複をQA確認後に切替える。元テーブルは保持する。

## 12. 無料利用枠試算

### 12.1 前提

- 30日月、毎時720回、対象アカウント1件。
- 1か月の新規Reels数 `P` は、定常状態の30日以内ACTIVE件数と同程度と置く。
- Reels 1本は最大720観測、OBS item平均2KB、各API呼び出し平均1秒、Lambda 256MB、起動固定処理5秒。
- Media一覧は全ページを毎時取得。アカウント生涯media件数を `M`、1ページ件数を実機値 `L` とすると、discovery callは `720 * ceil(M/L)`。表は `ceil(M/L)=1` と仮定する。
- 正常時のinsights callは `720 * P`。リトライ、ページ増加、token検証は別。
- DynamoDB Standard、table 5 RCU/5 WCU + GSI 5 RCU/5 WCU。AWS payer/Region内の他リソースが無料枠を消費していない前提。

| 月間新規Reels P | insights API calls/月 | discovery込みMeta calls/月 | Lambda概算GB-s/月 | OBS writes/月 | OBS保存増分/月 | logs概算/月 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 2,880 | 3,600 | 1,620 | 2,880 | 5.6MB | 約4MB |
| 30 | 21,600 | 22,320 | 6,300 | 21,600 | 42.2MB | 約23MB |
| 100 | 72,000 | 72,720 | 18,900 | 72,000 | 140.6MB | 約74MB |

計算式は `Lambda GB-s = 720 * (5 + P * 1) * 0.25`、`OBS storage = P * 720 * 2KB`。API latency、item size、ログ量は実測後に差し替える。

### 12.2 無料枠との比較

- EventBridge Scheduler: 月720 invocationは月14,000,000 invocationの無料枠より小さい。
- Lambda: 月720 requestと最大18,900 GB-s（P=100）は、月1,000,000 request、400,000 GB-sの無料枠より小さい。上記前提ではACTIVEが約2,200件を超えるとcompute無料枠へ近づく。
- DynamoDB: provisioned合計はtable+GSIで10 RCU/10 WCU、無料枠25 RCU/25 WCU内。保存25GBは、2KB×720観測なら累計約18,000投稿で到達する。P=100/月では約15年だが、実itemが大きいほど早まる。
- DynamoDB throughput: 2KB itemは1 snapshotあたり概ね2 WCUを使う。5 WCUでは約2件/秒が安全目安で、APIを直列化する。処理の並列化やitem肥大化でcapacityを増やし、table+GSIが各無料枠を超えると課金対象になる。
- CloudWatch Logs: 5GB/月の無料取り込み枠に対し表の想定は十分小さい。ただしdebug bodyや無制限保持は禁止する。custom metrics/alarmsの無料枠・現行料金は対象AWS契約でplatformがdeploy前に確認する。
- SQS: DLQの正常時requestはほぼ0で、月1,000,000 request無料枠内。SNSも想定alarm件数なら月1,000 email通知無料枠内。
- SSM Standard Parameterは追加料金なし。Secrets Managerへ変更する場合はsecret月額/API料金が発生する。

無料を保証しない。特に、(1) AWSアカウント全体ですでに無料枠を消費、(2) media履歴が複数ページ化してMeta callsとLambda durationが増加、(3) API遅延・429再試行、(4) OBSが2KB超、(5) DynamoDBの累積保存が25GB超、(6) CloudWatchログが5GB超、(7) provisioned capacityを25 RCU/WCU超へ増強、のいずれかで課金またはAPI制約へ達し得る。Meta API callsはAWS課金ではないが、実効クォータの主要リスクである。

## 13. 代替案と選定根拠

| 案 | 判断 | 理由 |
| --- | --- | --- |
| 投稿ごとにSchedulerを720回分作る | 不採用 | schedule管理、停止、重複の複雑性が増え、1つの毎時runで十分 |
| Step Functions Mapで投稿ごと並列取得 | 不採用 | 100件規模までは単一Lambdaで時間内。状態機械、課金、レート超過面を増やす |
| SQSを投稿処理queueとして常用 | 不採用 | MVP件数では不要。DLQ用途だけに限定する |
| Media一覧の先頭ページだけ取得 | 不採用 | 公式仕様がorderingを保証しないため欠落リスクがある。全cursor走査を採用 |
| Webhookで投稿検出 | 保留 | 受信用公開endpoint、検証、署名、可用性が増える。公式に対象投稿publish eventを実機確認でき、全走査がレート上限になる場合に再評価 |
| Facebook Login | 条件付き代替 | Page連携と追加権限が必要。既存AppがFacebook Login前提、またはInstagram LoginがIG-G1/G2不合格の場合のみ採用 |
| DynamoDB On-Demand | 不採用 | 低負荷には簡単だが、明示的なDynamoDB無料枠はprovisioned 25 RCU/WCU。5/5で予測可能なMVPを優先 |
| Secrets Manager | 不採用 | 自動rotationを本MVPで実装しないため有料secretを増やさない。SSM Standard SecureStringと手動更新で足りる |
| 2テーブル（registry/snapshot） | 不採用 | 単一table+sparse GSIで必要なaccess patternを満たし、リソースと容量管理を減らす |

## 14. 後続Task分解案

### Backend担当

範囲: Collector Lambdaのapplication codeだけ。Instagram client、cursor全走査、Reels allowlist、時刻枠、30日判定、DynamoDB item mapper、条件付き冪等保存、エラー分類、redaction、structured metricsを実装する。AWSリソース定義は変更しない。

完了条件案:

- IG-G1〜G5の実機確認記録と、確定したmetric manifestがある。
- fixture/unit testで投稿検出、空metric、同一slot再実行、ERROR→SUCCESS、SUCCESS上書き拒否、29日23時台/30日境界、cursor複数ページを確認する。
- token、Authorization、query stringがlogに出ないtestがある。
- 1 Lambda起動の部分失敗・systemic失敗の返却契約がplatform設定と一致する。

### Platform担当

範囲: CloudFormation/SAM、IAM、OIDC、GitHub Actions、SSM parameter運用、Scheduler、DynamoDB、SQS DLQ、CloudWatch/SNS、PITR、deploy/rollback runbook。Backend codeの業務ロジックを変更しない。

完了条件案:

- clean AWS環境へ手順どおり再現deployでき、no-op deployも成功する。
- GitHubとworkflow logにtokenや長期AWS keyがなく、runtime/deploy roleが最小権限である。
- log 30日、DLQ 14日、DynamoDB Retain/PITR、reserved concurrency 1、async retry age 1hがtemplateで固定される。
- alarm→SNS、失敗→DLQ、rollback、token更新がrunbookどおり確認できる。
- 対象payer/Regionの既存無料枠消費と料金を再計算する。

### QA担当

範囲: System QA。設計Task自体ではなく、backend/platform統合後の公開前検証を行う。

完了条件案:

- 新規Reelsの自動登録、1時間枠、複数ページ、同一slot二重起動、途中失敗再実行、30日境界をevidence付きで確認する。
- SUCCESS/PARTIAL/ERROR、missing metric非ゼロ補完、保存必須属性とschema versionを確認する。
- 認証失効、429、5xx、DynamoDB throttle、Lambda timeout、DLQ、alarm、手動復旧をfault injectionで確認する。
- CloudFormationの新規/更新/no-op/rollback、DynamoDB保持、secret scanを確認する。
- passedをTaskへ記録するまでリリース可能扱いにしない。

### Management（佐伯）確認事項

- Reels限定とUTC正時枠をMVPの「投稿動画」「1時間ごと」として承認する。
- IG-G1/G2のアカウント管理者・Meta App情報を割り当てる。
- 指標互換性検証後の必須metric manifestを確定する。
- APIクォータ実測が全履歴走査に不足する場合の投稿検出方式変更を判断する。

## 15. 自己確認

| 確認項目 | 結果 |
| --- | --- |
| 入力要件との一致、Out of Scope除外 | passed |
| 既存構成調査 | passed（実装資産なし） |
| 認証・権限・対象media・指標の根拠と未確定事項 | passed |
| data flow、境界、DynamoDB、冪等性、30日停止 | passed |
| failure、retry、rate limit、secret、監視、復旧 | passed |
| CloudFormation/GitHub Actions責任境界 | passed |
| 投稿数別無料枠試算と超過条件 | passed（前提明示、deploy時再計算必要） |
| 代替案・選定根拠・rollback | passed |
| backend/platform/qa分解 | passed |
| 実装、分析、AI、MCP、Vercel UIの追加なし | passed |

設計としての自己確認はpassed。API実機ゲートIG-G1〜G5と佐伯の上記判断はpendingであり、後続実装のSystem QA passed前にリリース可能・業務上完了とは扱わない。

## 16. 公式根拠

- Meta, [Instagram API official collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api) — Professional account、Facebook/Instagram Login、ordering非対応、cursor pagination、権限概要（2026-09-07確認）
- Meta, [Instagram Insights official collection](https://www.postman.com/meta/instagram/folder/23987686-f659d7d1-d74c-44e4-9192-9b1e8694c511) — Login別host/token/permissions、Access Level、owned professional media、UTC、空data（2026-09-07確認）
- Meta, [Get media insights request](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api?entity=request-23987686-1ff01566-3509-48bd-a0f4-8571a91ccfdf) — `/{ig_media_id}/insights` とmetric候補（2026-09-07確認）
- Meta, [Graph API changelog](https://developers.facebook.com/docs/graph-api/changelog/) / [Platform versioning](https://developers.facebook.com/docs/apps/versions/) — v26.0とversion明示（2026-09-07確認）
- AWS, [EventBridge Scheduler pricing](https://aws.amazon.com/eventbridge/pricing/) — 月14M invocation無料枠（2026-09-07確認）
- AWS, [Scheduler retry and DLQ](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule.html) — retry policyとSQS DLQ（2026-09-07確認）
- AWS, [Invoke a Lambda function on a schedule](https://docs.aws.amazon.com/lambda/latest/dg/with-eventbridge-scheduler.html) — SchedulerからLambdaへの非同期invoke（2026-09-07確認）
- AWS, [Lambda pricing](https://aws.amazon.com/lambda/pricing/) — 月1M requests、400,000 GB-s無料枠（2026-09-07確認）
- AWS, [Lambda asynchronous error handling](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-error-handling.html) — 非同期retry、重複可能性、DLQ（2026-09-07確認）
- AWS, [DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/) — Standardの25GB、provisioned 25 WCU/25 RCU無料枠（2026-09-07確認）
- AWS, [Systems Manager Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html) — Standard parameter追加料金なし、SecureString/KMS、自動rotationなし（2026-09-07確認）
- AWS, [CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/) — logs等の無料枠と超過料金（2026-09-07確認）
- AWS, [SQS FAQ](https://aws.amazon.com/sqs/faqs/) / [SNS FAQ](https://aws.amazon.com/sns/faqs/) — SQS月1M requests、SNS月1,000 email notifications等の無料枠（2026-09-07確認）
