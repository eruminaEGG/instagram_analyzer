# Instagram Insights Collector — minimal Platform infrastructure

The application stack creates only the approved runtime: one hourly EventBridge
Scheduler, one Backend-owned Lambda, one DynamoDB table, the Scheduler and Lambda
roles, and the Lambda's 30-day standard log group. The source handler is
`backend/src/collector/handler.py`'s `lambda_handler` (SAM Handler:
`collector.handler.lambda_handler`); Platform does not create or modify it.

## GitHub Environment configuration

The protected `production` Environment supplies:

| Name | Type | Meaning |
| --- | --- | --- |
| `AWS_REGION` | variable | Deployment Region. |
| `AWS_DEPLOY_ROLE_ARN` | variable | Existing GitHub OIDC deploy role ARN. |
| `INSTAGRAM_ACCOUNT_ID` | variable | Non-secret account ID. |
| `INSTAGRAM_ACCESS_TOKEN` | secret | Access token passed as the template's `NoEcho` parameter. |

The workflow does not create an OIDC provider, deployment role, or dedicated
artifact bucket. `sam deploy --resolve-s3` uses SAM's managed packaging location.
Do not add the secret to Git, job outputs, artifacts, CloudFormation outputs, or
logs. Update the GitHub Environment secret and rerun the approved workflow to
rotate it.

## Deployment and recovery

The workflow validates, builds the Backend-provided source, and deploys with the
existing OIDC role. A deployment cannot proceed without the protected Environment,
the role, all values above, and a reviewed AWS change set. To halt collection,
disable the named schedule after recording its configuration. For a bad code or
configuration release, deploy the last approved commit, then re-enable the
schedule. The DynamoDB table and log group are retained; do not delete the stack as
a recovery shortcut.

The account's existing deploy role must permit only the named stack resources and
SAM-managed deployment artifacts. Its exact permissions are an account-level
bootstrap responsibility and are intentionally not created by this project stack.
