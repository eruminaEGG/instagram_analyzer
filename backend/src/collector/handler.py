"""AWS Lambda composition root; application code remains injectable for tests."""

from __future__ import annotations

import os

from .instagram import InstagramClient
from .repository import DynamoRepository
from .service import CollectorService


def lambda_handler(event, context):
    import boto3  # Provided by Lambda runtime; not needed for local unit tests.
    dynamodb = boto3.resource("dynamodb")
    required = ("INSTAGRAM_ACCOUNT_ID", "DYNAMODB_TABLE_NAME", "INSIGHT_METRICS", "INSTAGRAM_ACCESS_TOKEN")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("missing required configuration: " + ",".join(missing))
    metrics = [part.strip() for part in os.environ["INSIGHT_METRICS"].split(",") if part.strip()]
    api_version = os.environ.get("API_VERSION", "v26.0")
    client = InstagramClient(
        os.environ["INSTAGRAM_ACCESS_TOKEN"],
        api_version,
        os.environ.get("GRAPH_HOST", "https://graph.instagram.com"),
        int(os.environ.get("HTTP_TIMEOUT_SECONDS", "10")),
    )
    service = CollectorService(
        client,
        DynamoRepository(dynamodb.Table(os.environ["DYNAMODB_TABLE_NAME"])),
        os.environ["INSTAGRAM_ACCOUNT_ID"],
        metrics,
        api_version,
    )
    return service.run(event)
