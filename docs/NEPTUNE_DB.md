# Amazon Neptune Ingestion & Operations Guide

This guide details the end-to-end architecture, configuration, and step-by-step procedures required to ingest RDF data into **Amazon Neptune** using **AWS Lambda** and the **Neptune S3 Bulk Loader API**.

---

## 1. Overview & Architecture

Amazon Neptune functions as the enterprise Knowledge Graph (KG) store, supporting W3C RDF 1.1, SPARQL 1.1, and Property Graphs (openCypher / Apache Gremlin).

In AWS production:
1. Upstream pipeline steps output canonical RDF N-Quads (`.nq`) to Amazon S3:
   - T-Box (Ontology schema): `s3://<bucket>/ontology/centree/ontology.nq`
   - A-Box (Instance data):   `s3://<bucket>/knowledge/abox/instances.nq`
2. An S3 `ObjectCreated` event (or an EventBridge rule) triggers a dedicated **AWS Lambda function (`graph_load_handler`)** running inside the Neptune VPC.
3. The Lambda function calls the Neptune REST Loader API (`POST /loader`) to initiate an asynchronous S3 bulk load job.
4. Neptune pulls the N-Quads file directly from S3 via an internal AWS network path (VPC Gateway Endpoint for S3) and loads it into the specified named RDF graph.
5. Lambda polls the loader job status until completion or failure, then publishes the outcome to CloudWatch Logs. On failure it raises an exception to trigger the Lambda DLQ or Step Functions error path.

> [!IMPORTANT]
> Neptune must be placed in **private VPC subnets** with no public IP. All client access — including from Lambda — is strictly within the VPC. Lambda must be deployed in the same VPC (or a peered VPC) with the correct Security Group configuration.

---

## 2. Prerequisites & Resource Requirements

Before configuring ingestion, provision the following AWS resources:

### 2.1 Amazon Neptune Cluster

| Property | Value |
|---|---|
| **Engine** | Neptune DB (Serverless or Provisioned) |
| **Graph Model** | RDF / SPARQL (Triple/Quad store) |
| **Port** | `8182` (default, TCP) |
| **VPC Subnets** | Minimum 2 **private** subnets in different AZs (Neptune Subnet Group required) |
| **IAM Database Authentication** | `Enabled` — required for SigV4 request signing from Lambda |
| **Deletion Protection** | `Enabled` for production |

### 2.2 Amazon S3 Bucket

- Bucket versioning: `Enabled`
- Encryption: SSE-S3 (`AES256`) or SSE-KMS
- Block all public access: `On`
- Expected artifact paths:
  - `s3://<bucket>/ontology/centree/ontology.nq` (T-Box)
  - `s3://<bucket>/knowledge/abox/instances.nq` (A-Box)

### 2.3 Networking (VPC Configuration)

| Resource | Configuration |
|---|---|
| **Neptune Security Group** | Inbound TCP `8182` from the Lambda Security Group ID |
| **Lambda Security Group** | Outbound TCP `8182` to Neptune SG; outbound HTTPS `443` to VPC endpoints |
| **S3 VPC Gateway Endpoint** | `com.amazonaws.<region>.s3` — required so Neptune can fetch S3 objects without traversing public internet or requiring a NAT Gateway |
| **CloudWatch Logs VPC Interface Endpoint** | `com.amazonaws.<region>.logs` — recommended so Lambda writes logs without internet egress |
| **Secrets Manager VPC Interface Endpoint** | `com.amazonaws.<region>.secretsmanager` — if using Secrets Manager for credentials |

---

## 3. IAM Roles & Permissions

Two distinct IAM roles are required.

### 3.1 Neptune Cluster S3 Read Role (`NeptuneLoadFromS3Role`)

This role is **assumed by the Neptune cluster engine** to pull RDF data from S3. It must be associated with the Neptune cluster.

**Trust Relationship:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "rds.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

**Permission Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
      "Resource": "arn:aws:s3:::my-esa-knowledge-bucket"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::my-esa-knowledge-bucket/*"
    }
  ]
}
```

**Attach the role to the Neptune cluster** (only needed once, or when the role ARN changes):
```bash
aws neptune add-role-to-db-cluster \
  --db-cluster-identifier my-neptune-cluster \
  --role-arn arn:aws:iam::123456789012:role/NeptuneLoadFromS3Role \
  --feature-name s3Import
```

### 3.2 AWS Lambda Execution Role (`LambdaNeptuneIngestRole`)

This role allows the Lambda function to run inside the VPC, call Neptune APIs (via IAM DB Auth), pass the S3 loader role, and write CloudWatch logs.

**Managed Policies to attach:**
- `AWSLambdaVPCAccessExecutionRole` — grants permission to create and manage the VPC ENI used by Lambda

**Inline Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "neptunedb:connect",
        "neptunedb:StartLoaderJob",
        "neptunedb:GetLoaderJobStatus",
        "neptunedb:CancelLoaderJob",
        "neptunedb:ReadDataViaQuery",
        "neptunedb:WriteDataViaQuery"
      ],
      "Resource": "arn:aws:neptune-db:ap-south-1:123456789012:cluster-ABCDEF12345/*"
    },
    {
      "Effect": "Allow",
      "Action": ["iam:PassRole"],
      "Resource": "arn:aws:iam::123456789012:role/NeptuneLoadFromS3Role"
    }
  ]
}
```

> [!NOTE]
> The `iam:PassRole` permission is required because Lambda passes `NeptuneLoadFromS3Role` as `iamRoleArn` in the `/loader` API call body. Without it, the call will return a `403 AccessDenied`.

---

## 4. Step-by-Step Implementation Guide

### Step 1: Configure Lambda Environment Variables

Set the following on the Lambda function configuration (**Configuration → Environment variables**):

| Variable | Description | Example |
|---|---|---|
| `NEPTUNE_ENDPOINT` | Cluster writer endpoint hostname | `my-neptune.cluster-abc123.ap-south-1.neptune.amazonaws.com` |
| `NEPTUNE_PORT` | Neptune port | `8182` |
| `NEPTUNE_LOAD_IAM_ROLE_ARN` | ARN of the S3-read role attached to Neptune | `arn:aws:iam::123456789012:role/NeptuneLoadFromS3Role` |
| `AWS_REGION` | Deployment region | `ap-south-1` |
| `NEPTUNE_USE_IAM_AUTH` | Enable SigV4 request signing | `true` |
| `S3_BUCKET_NAME` | S3 bucket holding the N-Quads artifacts | `my-esa-knowledge-bucket` |
| `POLL_INTERVAL_SECONDS` | Seconds between loader status polls | `5` |
| `MAX_POLL_TIMEOUT_SECONDS` | Max seconds to wait for load to complete | `300` |

> [!WARNING]
> Lambda has a **maximum execution timeout of 900 seconds (15 minutes)**. If your N-Quads file is very large (millions of triples), polling synchronously inside Lambda may time out. In that case, adopt an **async pattern**: have Lambda submit the load job, store the `loadId` in DynamoDB, and use a separate scheduled Lambda or Step Functions wait state to poll completion.

---

### Step 2: Lambda Dependencies

Neptune's Bulk Loader API is called over HTTPS. The following Python packages are required:

| Package | Source | Notes |
|---|---|---|
| `botocore` | **Bundled in Lambda runtime** — no install needed | Used for SigV4 request signing (`SigV4Auth`) |
| `boto3` | **Bundled in Lambda runtime** — no install needed | Available if needed for other AWS SDK calls |
| `requests` | **NOT bundled** — must include in deployment package or Lambda Layer | Used only if you swap `urllib.request` for `requests` |

The Lambda implementation in [`src/esa/graph_neptune.py`](../src/esa/graph_neptune.py) uses only the standard library (`urllib.request`) and `botocore`, so **no additional packages beyond the Lambda runtime are required**.

---

### Step 3: Lambda Ingestion Handler (`graph_load_handler`)

The Lambda function handles two invocation modes:
- **S3 Event Notification:** receives `Records[0].s3` with bucket and key.
- **Direct invocation:** receives `{ "s3_uri": "s3://bucket/key" }` or `{ "bucket": "...", "key": "..." }`.

It determines the target named graph from the S3 key path, submits a Neptune bulk load job, polls for completion, and returns the loaded record count.

```python
import os
import json
import time
import logging
import urllib.parse
import urllib.request
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

logger = logging.getLogger()
logger.setLevel(logging.INFO)

NEPTUNE_ENDPOINT = os.environ["NEPTUNE_ENDPOINT"]
NEPTUNE_PORT     = os.environ.get("NEPTUNE_PORT", "8182")
NEPTUNE_ROLE_ARN = os.environ["NEPTUNE_LOAD_IAM_ROLE_ARN"]
REGION           = os.environ.get("AWS_REGION", "ap-south-1")
USE_IAM_AUTH     = os.environ.get("NEPTUNE_USE_IAM_AUTH", "true").lower() in ("true", "1", "yes")
BASE_URL         = f"https://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}"


def _signed_request(method: str, path: str, body: bytes = b"", headers: dict = None) -> dict:
    """Execute an HTTPS request against Neptune, optionally signed with AWS SigV4."""
    headers = headers or {}
    url = f"{BASE_URL}{path}"

    if USE_IAM_AUTH:
        credentials = Session().get_credentials()
        aws_req = AWSRequest(method=method, url=url, headers=headers, data=body)
        SigV4Auth(credentials, "neptunedb", REGION).add_auth(aws_req)
        headers = dict(aws_req.headers)

    req = urllib.request.Request(
        url,
        data=body if method in ("POST", "PUT") else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def trigger_bulk_load(s3_source_uri: str, named_graph: str = None) -> str:
    """Submit an S3 bulk load job to the Neptune /loader API. Returns the loadId."""
    payload = {
        "source":      s3_source_uri,
        "format":      "nquads",
        "iamRoleArn":  NEPTUNE_ROLE_ARN,
        "region":      REGION,
        "failOnError": "TRUE",
        "parallelism": "MEDIUM",
        "mode":        "AUTO",
    }
    if named_graph:
        payload["parserConfiguration"] = {"namedGraphUri": named_graph}

    body    = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    logger.info("Submitting bulk load for %s → named graph: %s", s3_source_uri, named_graph)
    res     = _signed_request("POST", "/loader", body=body, headers=headers)
    load_id = res["payload"]["loadId"]
    logger.info("Load job created. LoadId: %s", load_id)
    return load_id


def poll_load_status(load_id: str, poll_interval: int = 5, timeout: int = 300) -> dict:
    """Poll the loader status until LOAD_COMPLETED, LOAD_FAILED, or timeout."""
    start = time.time()
    path  = f"/loader/{load_id}?details=true&errors=true"

    while time.time() - start < timeout:
        res         = _signed_request("GET", path)
        status_info = res.get("payload", {}).get("overallStatus", {})
        status      = status_info.get("status")

        logger.info("Job %s — status: %s (%.0fs elapsed)", load_id, status, time.time() - start)

        if status == "LOAD_COMPLETED":
            logger.info("Load SUCCESS. Records loaded: %d", status_info.get("totalRecords", 0))
            return res
        if status in ("LOAD_FAILED", "LOAD_CANCELLED"):
            errors = res.get("payload", {}).get("errors", [])
            logger.error("Load FAILED. Status: %s. Errors: %s", status, errors)
            raise RuntimeError(f"Neptune load failed ({status}): {errors}")

        time.sleep(poll_interval)

    raise TimeoutError(f"Load job {load_id} did not complete within {timeout}s")


def lambda_handler(event, context):
    """
    Lambda entry point — handles both S3 Event Notifications and direct invocations.
    """
    logger.info("Event: %s", json.dumps(event))

    # 1. Resolve the S3 URI
    if "Records" in event and event["Records"]:
        record = event["Records"][0]["s3"]
        bucket = record["bucket"]["name"]
        key    = urllib.parse.unquote_plus(record["object"]["key"])
        s3_uri = f"s3://{bucket}/{key}"
    else:
        s3_uri = event.get("s3_uri")
        if not s3_uri:
            bucket = event.get("bucket", os.environ.get("S3_BUCKET_NAME"))
            key    = event.get("key", "knowledge/abox/instances.nq")
            s3_uri = f"s3://{bucket}/{key}"

    # 2. Route to the appropriate named graph based on S3 key path
    if "ontology" in s3_uri:
        named_graph = "https://example.org/esa/ontology"
    elif "abox" in s3_uri or "instances" in s3_uri:
        named_graph = "https://example.org/esa/instances"
    else:
        named_graph = None   # Neptune will use the default graph

    # 3. Submit load job and poll to completion
    poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
    poll_timeout  = int(os.environ.get("MAX_POLL_TIMEOUT_SECONDS", "300"))

    load_id = trigger_bulk_load(s3_uri, named_graph=named_graph)
    result  = poll_load_status(load_id, poll_interval=poll_interval, timeout=poll_timeout)

    records_loaded = result["payload"]["overallStatus"].get("totalRecords", 0)

    return {
        "statusCode": 200,
        "body": {
            "message":       "Neptune bulk load completed successfully",
            "loadId":        load_id,
            "s3Uri":         s3_uri,
            "namedGraph":    named_graph,
            "recordsLoaded": records_loaded,
        },
    }
```

---

### Step 4: Configure the S3 Event Trigger

To trigger `graph_load_handler` automatically when upstream pipeline steps write N-Quads to S3:

#### Option A — S3 Bucket Event Notification (simple)
In the S3 Console → **Properties → Event notifications → Create event notification**:

| Setting | Value |
|---|---|
| **Event types** | `s3:ObjectCreated:*` |
| **Prefix filter (instances)** | `knowledge/abox/` |
| **Prefix filter (ontology)** | `ontology/centree/` |
| **Suffix filter** | `.nq` |
| **Destination** | Lambda function → `graph_load_handler` |

> [!WARNING]
> S3 cannot have two event notifications with overlapping prefixes pointing to the same Lambda. Create **two separate event notifications** — one for `knowledge/abox/` and one for `ontology/centree/`.

#### Option B — Amazon EventBridge Rule (recommended for enterprise)

1. Enable EventBridge notifications on the S3 bucket (**Properties → Amazon EventBridge → On**).
2. Create an EventBridge rule with the following event pattern:

```json
{
  "source": ["aws.s3"],
  "detail-type": ["Object Created"],
  "detail": {
    "bucket": { "name": ["my-esa-knowledge-bucket"] },
    "object": {
      "key": [
        { "prefix": "knowledge/abox/" },
        { "prefix": "ontology/centree/" }
      ]
    }
  }
}
```

3. Set the rule target to the `graph_load_handler` Lambda function.

EventBridge offers richer filtering, retry policies, and dead-letter queue support compared to direct S3 notifications.

---

### Step 5: Named Graph Segregation & Loading Order

**Phase 1 must complete before Phase 2.** Use Step Functions or explicit sequencing in your orchestration:

| Phase | S3 Source | Named Graph IRI | Purpose |
|---|---|---|---|
| **1 — T-Box (Ontology)** | `s3://<bucket>/ontology/centree/ontology.nq` | `https://example.org/esa/ontology` | Loads OWL classes, object properties, and datatype properties. Provides schema context for cross-graph SPARQL queries. |
| **2 — A-Box (Instances)** | `s3://<bucket>/knowledge/abox/instances.nq` | `https://example.org/esa/instances` | Loads resolved customer, account, loan, and branch entity instances and their relationships. |

---

### Step 6: Post-Ingestion Validation via SPARQL

Run the following validation queries against Neptune's SPARQL endpoint (`POST https://<endpoint>:8182/sparql`) after each load:

#### Query 1 — Verify Statement Counts per Named Graph
```sparql
SELECT ?g (COUNT(*) AS ?triples)
WHERE {
  GRAPH ?g { ?s ?p ?o }
}
GROUP BY ?g
ORDER BY DESC(?triples)
```

#### Query 2 — Customer 360 Traversal
```sparql
PREFIX ex:  <https://example.org/esa/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT ?name ?creditScore ?branch ?accountType ?loanType ?loanAmount
WHERE {
  GRAPH <https://example.org/esa/instances> {
    ?customer rdf:type ex:Customer ;
              ex:fullName ?name ;
              ex:creditScore ?creditScore .
    OPTIONAL { ?customer ex:registeredAt ?branch . }
    OPTIONAL { ?customer ex:ownsAccount ?acc . ?acc ex:accountType ?accountType . }
    OPTIONAL {
      ?customer ex:hasLoan ?loan .
      ?loan ex:loanType ?loanType ;
            ex:loanAmount ?loanAmount .
    }
  }
}
LIMIT 25
```

#### Query 3 — Cross-Graph Schema Validation
Identify A-Box predicates that are not declared in the T-Box ontology graph (excludes `rdf:type` which is a built-in W3C property):
```sparql
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT DISTINCT ?predicate
WHERE {
  GRAPH <https://example.org/esa/instances> {
    ?s ?predicate ?o .
  }
  FILTER(?predicate != rdf:type)
  FILTER NOT EXISTS {
    GRAPH <https://example.org/esa/ontology> {
      { ?predicate rdf:type owl:ObjectProperty }
      UNION
      { ?predicate rdf:type owl:DatatypeProperty }
    }
  }
}
```
A result with **zero rows** means all instance predicates are fully declared in the ontology.

---

## 5. Monitoring, Observability & Alerting

### Lambda Metrics (CloudWatch)

| Metric | Alarm Condition | Action |
|---|---|---|
| `Errors` | `> 0` for any 1-minute period | Notify via SNS → PagerDuty/email |
| `Duration` | `> 600,000ms` (10 min) | Investigate load size; consider async polling pattern |
| `Throttles` | `> 0` sustained | Increase Lambda reserved concurrency |

Set Lambda **timeout to `900s`** (maximum). Set **reserved concurrency to `2`** (one for T-Box, one for A-Box) to prevent runaway invocations.

### Neptune Cluster Metrics (CloudWatch)

| Metric | Description | Alert Threshold |
|---|---|---|
| `LoaderLoadErrors` | Errors from the S3 Bulk Loader | `> 0` |
| `SparqlRequestsPerSec` | Active SPARQL query throughput | Monitor baseline; alert on unusual spikes |
| `NCUUtilization` | CPU/memory for Serverless | `> 80%` → consider raising max NCU |
| `CPUUtilization` | For provisioned instances | `> 80%` sustained |
| `FreeableMemory` | Provisioned instance free memory | `< 20%` |
| `BufferCacheHitRatio` | Query cache effectiveness | `< 90%` (sustained drop indicates under-provisioning) |

### Dead-Letter Queue (DLQ) & Retries

- Attach an **Amazon SQS Dead-Letter Queue** to the Lambda function.
- Set **Maximum retry attempts to `2`** on the Lambda event source mapping.
- Monitor `ApproximateNumberOfMessagesVisible` on the DLQ SQS queue; alarm if `> 0`.
- Messages in the DLQ contain the original S3 event and can be replayed once the root cause is resolved.

---

## 6. Troubleshooting

| Error | Likely Cause | Resolution |
|---|---|---|
| `Connection refused` / `ETIMEDOUT` on port `8182` | Lambda is not in the Neptune VPC, or the Neptune Security Group does not allow inbound `8182` from Lambda's SG. | Verify Lambda VPC config matches Neptune's VPC and subnets. Add inbound rule on Neptune SG: `TCP 8182` from Lambda SG ID. |
| `HTTP 403 Forbidden` / `InvalidSignatureException` | IAM database authentication is enabled but requests are unsigned, or credentials are invalid/expired. | Set `NEPTUNE_USE_IAM_AUTH=true`. Verify Lambda execution role has `neptunedb:connect` and `neptunedb:StartLoaderJob` on the cluster resource ARN. |
| `S3 AccessDenied` during bulk load | Neptune cluster does not have `NeptuneLoadFromS3Role` attached, or the role lacks `s3:GetObject`. | Confirm the role is associated via `aws neptune add-role-to-db-cluster`. Check the S3 bucket policy does not block the role's principal. |
| `LOAD_FAILED` with `PARSING_ERROR` | Malformed N-Quads in the source file — invalid IRIs, unescaped characters, or blank node inconsistencies. | Validate the `.nq` file locally using PyOxigraph before pushing to S3 (Step 5 with `--validate-shacl`). |
| `iam:PassRole` AccessDenied on load submission | Lambda execution role cannot pass `NeptuneLoadFromS3Role` to Neptune. | Add `iam:PassRole` on the `NeptuneLoadFromS3Role` ARN to the Lambda execution role inline policy. |
| `TimeoutError` in Lambda | Load job exceeded `MAX_POLL_TIMEOUT_SECONDS` (default 300s) for large files. | Increase `MAX_POLL_TIMEOUT_SECONDS`. For very large files, use an async pattern: store `loadId` in DynamoDB and poll via a separate scheduled Lambda. |
