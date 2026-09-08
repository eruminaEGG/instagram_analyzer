# TASK-20260908-092530-DCE7 / TASK-20260908-093800-300E — Platform minimal implementation record

Date: 2026-09-08  
Owner: 大森 海斗 (System Integration / Platform)  
Upstream design task: `TASK-20260907-232607-D242`  
Scope: infrastructure and GitHub Actions only. No Backend logic was changed.

Director review correction task: `TASK-20260908-093800-300E`. This revision
aligns the deployment package and environment-variable wiring to the existing
Backend source; it adds no AWS resource or platform feature.

## Delivered configuration

- `infra/template.yaml` creates exactly one hourly EventBridge Scheduler, one
  Lambda, one DynamoDB table, two inline-policy IAM roles, and the Lambda standard
  log group.
- The schedule is UTC `cron(0 * * * ? *)`, flexible window off, and has zero retry
  attempts. The Lambda package is `backend/src/`, its Backend-owned handler is
  `collector.handler.lambda_handler`, and the table name is supplied as
  `DYNAMODB_TABLE_NAME`.
- The table is provisioned at 5 RCU / 5 WCU with only `PK` and `SK`, default
  AWS-owned encryption, and retain policies.
- The Lambda role may write only its log stream and perform `GetItem` / `PutItem`
  on this table. The Scheduler role may invoke only this Lambda.
- The protected GitHub Environment supplies `INSTAGRAM_ACCESS_TOKEN`; the workflow
  passes it only as `InstagramAccessToken`, a `NoEcho` CloudFormation parameter,
  which becomes the Lambda environment variable. It is absent from outputs,
  artifacts, shell output, and repository files.
- GitHub Actions uses the pre-existing OIDC deploy role and `sam deploy --resolve-s3`.
  No bootstrap stack or project-specific artifact bucket remains.

## Explicit exclusions verified

The template and workflow contain no resource, permission, or configuration for:

- dead-letter queues or queue delivery;
- notifications or alarms;
- point-in-time recovery;
- parameter-store access;
- secondary indexes; or
- application-published monitoring metrics.

## Verification evidence

| Check | Result |
| --- | --- |
| Scope and ownership | Passed: Platform resources/workflow only; Backend code untouched. |
| Runtime-process impact check | No process stopped. Sandbox denies `ps`, so process-inspection evidence remains an environment-side gate. |
| Corrected Backend source wiring | Passed: `CodeUri` is `../backend/src/`, handler is `collector.handler.lambda_handler`, Lambda receives the table name through `DYNAMODB_TABLE_NAME`, and the workflow checks `$PROJECT_DIR/backend/src`. |
| Template lint and SAM validation | Passed after this correction: `SAM_CLI_TELEMETRY=0 sam validate --lint --template-file infra/template.yaml` accepted the template. |
| SAM build | Passed after this correction: `sam build` resolved `backend/src`, copied the Python source, and emitted a deployable template. No `requirements.txt` was present, so no dependency installation was needed. |
| IAM and exclusion static check | Passed: exactly two runtime roles; the only application permissions are log stream writes, table `GetItem`/`PutItem`, and Lambda invoke. Static assertions found no excluded resource, permission, bootstrap, or dedicated artifact-bucket configuration. |
| Workflow syntax check | Passed: YAML parser accepted `.github/workflows/instagram-insights-collector.yml`; it checks the corrected source path and uses the existing OIDC role, protected Environment secret, and `--resolve-s3`. |
| Committed-secret check | Passed: focused scan of delivered template/workflow found no credential value pattern; the PR workflow runs Gitleaks. |
| AWS deploy | Remaining gate: re-confirmed `aws sts get-caller-identity` returned `NoCredentials`; no deploy, change-set, or live resource check was attempted. |

`sam` attempted to update metadata below the sandboxed home directory after
validation/build and received a permission denial. Both requested checks had
already succeeded, and the build artifacts were written only to a temporary
directory outside the project.

## Remaining release gates

1. The existing Backend handler at `backend/src/collector/handler.py` is built by
   SAM and must continue to pass its architecture-defined DynamoDB and
   secret-redaction tests.
2. A GitHub administrator configures the protected `production` Environment and
   its existing OIDC deploy role with only the needed permissions.
3. An authorized operator reviews the CloudFormation change set, deploys, and
   confirms the Lambda DryRun plus Scheduler state without exposing the token.
4. System QA passes the end-to-end and secret-exposure checks; 佐伯 真琴 then performs
   final acceptance. This Platform submission is not a release approval.
