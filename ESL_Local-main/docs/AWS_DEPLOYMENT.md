# AWS Deployment Guide - ESA End-to-End Pipeline

> **Status:** Admin IAM Role assumed. S3 bucket already created.
> **Graph DB:** Neo4j (Aura Free or self-managed EC2). No Neptune required.
> **Region:** `us-east-1` (update if different).

---

## Architecture Overview

```
S3 Upload Trigger             Lambda Function              Output
----------------------------------------------------------------------------
data/centree/*.ttl    ->   esa-ontology-ingest       ->   ontology/centree/*.nq
data/records/*.json   ->   esa-abox-generation       ->   knowledge/abox/*.nq
data/documents/*      ->   esa-document-index        ->   Bedrock embeddings
knowledge/abox/*.nq   ->   esa-graph-load            ->   Neo4j Graph DB
```

## Cloud Mapping

| Capability | Local POC | AWS target |
|---|---|---|
| Object storage | `local_bucket/` | Amazon S3 |
| Compute/orchestration | Python scripts | Separate AWS Lambda functions |
| Ontology/RDF | PyOxigraph | PyOxigraph packaged in Lambda |
| SHACL | pySHACL | Lambda/container |
| Document extraction | pypdf/python-docx | Lambda/container |
| Embeddings / LLM | SentenceTransformers | Bedrock Titan Embeddings |
| Vector search | ChromaDB | Amazon OpenSearch (future) |
| Entity resolution | Rules + RapidFuzz | Lambda |
| Graph | Neo4j Community local | **Neo4j Aura / self-managed EC2** |

## Lambda Boundaries

1. `esa-ontology-ingest` - read TTL from S3; parse PyOxigraph; write N-Quads to S3.
2. `esa-abox-generation` - read JSON records; run entity resolution; build A-Box RDF; write .nq to S3.
3. `esa-document-index` - extract documents from S3; chunk; embed via Bedrock; index vectors.
4. `esa-graph-load` - triggered by .nq landing in S3; download to /tmp; load into Neo4j via Cypher.

---

## Prerequisites

- AWS CLI installed and configured (`aws configure`) with Admin credentials.
- **Docker Desktop** installed and running.
- Python 3.11 installed locally.
- S3 bucket already created.
- Neo4j URI, username, and password ready.

**Replace these placeholders throughout all commands below:**
- `YOUR_S3_BUCKET` -> your actual S3 bucket name
- `YOUR_AWS_ACCOUNT_ID` -> your 12-digit AWS account ID
- `YOUR_NEO4J_URI` -> e.g. `neo4j+s://abc123.databases.neo4j.io` (Aura) or `bolt://1.2.3.4:7687`
- `YOUR_NEO4J_PASSWORD` -> your Neo4j password

Get your account ID:
```cmd
aws sts get-caller-identity --query Account --output text
```

---

## Why We Use Docker Containers for Lambda

Because this pipeline uses heavy machine learning libraries (`sentence-transformers`, `chromadb`, `torch`), the raw size of the dependencies exceeds 2.4 GB. Standard AWS Lambda ZIP files have a strict 250 MB limit. 
By deploying our Lambda as a **Docker Container Image**, we bypass the ZIP limits entirely (containers allow up to 10 GB), ensuring everything runs smoothly!

---

## Step 1 - Create Lambda IAM Execution Role

*(Since you've already done this, you can safely skip this step!)*

```cmd
aws iam create-role ^
  --role-name EsaLambdaExecutionRole ^
  --assume-role-policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"lambda.amazonaws.com\"},\"Action\":\"sts:AssumeRole\"}]}"

aws iam attach-role-policy --role-name EsaLambdaExecutionRole --policy-arn arn:aws:iam::aws:policy/AmazonS3FullAccess
aws iam attach-role-policy --role-name EsaLambdaExecutionRole --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam attach-role-policy --role-name EsaLambdaExecutionRole --policy-arn arn:aws:iam::aws:policy/AmazonBedrockFullAccess

aws iam get-role --role-name EsaLambdaExecutionRole --query "Role.Arn" --output text
```

---

## Step 2 - Create an Amazon ECR Repository

Amazon ECR (Elastic Container Registry) is where we will store our Docker image for Lambda.

```cmd
aws ecr create-repository --repository-name esa-pipeline-repo --image-scanning-configuration scanOnPush=true --image-tag-mutability MUTABLE
```

---

## Step 3 - Build and Push the Docker Image to AWS ECR

Ensure Docker Desktop is running, then execute these commands from the project root (`ESL_Local-main\`):

```cmd
:: 1. Authenticate Docker with your AWS account
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com

:: 2. Build the Docker image (this may take a few minutes to download the ML libraries)
docker build -t esa-pipeline-repo .

:: 3. Tag the image for your ECR repository
docker tag esa-pipeline-repo:latest YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest

:: 4. Push the image to AWS ECR
docker push YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest
```

---

## Step 4 - Create the 4 Lambda Functions

Instead of providing a zip file, we provide the `ImageUri` of the container we just pushed. Notice we override the `CMD` for each function using the `--image-config` parameter so the exact same Docker image is reused efficiently for all 4 endpoints!

### Function 1: Ontology Ingestion
```cmd
aws lambda create-function ^
  --function-name esa-ontology-ingest ^
  --package-type Image ^
  --role arn:aws:iam::YOUR_AWS_ACCOUNT_ID:role/EsaLambdaExecutionRole ^
  --code ImageUri=YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest ^
  --image-config Command="[\"src.esa.lambda_handlers.ontology_handler\"]" ^
  --timeout 300 ^
  --memory-size 1024 ^
  --environment "Variables={S3_BUCKET_NAME=YOUR_S3_BUCKET,AWS_EXECUTION_ENV=true,AWS_REGION=us-east-1,RDF_OUTPUT_KEY=ontology/centree/ontology.nq}"
```

### Function 2: A-Box RDF Generation
```cmd
aws lambda create-function ^
  --function-name esa-abox-generation ^
  --package-type Image ^
  --role arn:aws:iam::YOUR_AWS_ACCOUNT_ID:role/EsaLambdaExecutionRole ^
  --code ImageUri=YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest ^
  --image-config Command="[\"src.esa.lambda_handlers.abox_handler\"]" ^
  --timeout 300 ^
  --memory-size 1024 ^
  --environment "Variables={S3_BUCKET_NAME=YOUR_S3_BUCKET,AWS_EXECUTION_ENV=true,AWS_REGION=us-east-1,STRUCTURED_DATA_PATH=/tmp/records,RDF_OUTPUT_KEY=knowledge/abox/instances.nq}"
```

### Function 3: Document Processing & Embeddings
```cmd
aws lambda create-function ^
  --function-name esa-document-index ^
  --package-type Image ^
  --role arn:aws:iam::YOUR_AWS_ACCOUNT_ID:role/EsaLambdaExecutionRole ^
  --code ImageUri=YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest ^
  --image-config Command="[\"src.esa.lambda_handlers.document_index_handler\"]" ^
  --timeout 300 ^
  --memory-size 2048 ^
  --environment "Variables={S3_BUCKET_NAME=YOUR_S3_BUCKET,AWS_EXECUTION_ENV=true,AWS_REGION=us-east-1,EMBEDDING_PROVIDER=bedrock,LLM_PROVIDER=bedrock,DOCS_DIR=/tmp/documents}"
```

### Function 4: Neo4j Graph Load
```cmd
aws lambda create-function ^
  --function-name esa-graph-load ^
  --package-type Image ^
  --role arn:aws:iam::YOUR_AWS_ACCOUNT_ID:role/EsaLambdaExecutionRole ^
  --code ImageUri=YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest ^
  --image-config Command="[\"src.esa.lambda_handlers.graph_load_handler\"]" ^
  --timeout 300 ^
  --memory-size 1024 ^
  --environment "Variables={S3_BUCKET_NAME=YOUR_S3_BUCKET,AWS_EXECUTION_ENV=true,AWS_REGION=us-east-1,RDF_OUTPUT_KEY=knowledge/abox/instances.nq,NEO4J_URI=YOUR_NEO4J_URI,NEO4J_USER=neo4j,NEO4J_PASSWORD=YOUR_NEO4J_PASSWORD,NEO4J_DATABASE=neo4j}"
```

---

## Step 5 - Grant S3 Permission to Invoke the Lambdas

```cmd
aws lambda add-permission --function-name esa-ontology-ingest --principal s3.amazonaws.com --statement-id allow-s3-invoke --action lambda:InvokeFunction --source-arn arn:aws:s3:::YOUR_S3_BUCKET --source-account YOUR_AWS_ACCOUNT_ID

aws lambda add-permission --function-name esa-abox-generation --principal s3.amazonaws.com --statement-id allow-s3-invoke --action lambda:InvokeFunction --source-arn arn:aws:s3:::YOUR_S3_BUCKET --source-account YOUR_AWS_ACCOUNT_ID

aws lambda add-permission --function-name esa-document-index --principal s3.amazonaws.com --statement-id allow-s3-invoke --action lambda:InvokeFunction --source-arn arn:aws:s3:::YOUR_S3_BUCKET --source-account YOUR_AWS_ACCOUNT_ID

aws lambda add-permission --function-name esa-graph-load --principal s3.amazonaws.com --statement-id allow-s3-invoke --action lambda:InvokeFunction --source-arn arn:aws:s3:::YOUR_S3_BUCKET --source-account YOUR_AWS_ACCOUNT_ID
```

---

## Step 6 - Configure S3 Event Triggers

Create `notification.json` in the project root (replace `YOUR_AWS_ACCOUNT_ID`):

```json
{
  "LambdaFunctionConfigurations": [
    {
      "Id": "OntologyTrigger",
      "LambdaFunctionArn": "arn:aws:lambda:us-east-1:YOUR_AWS_ACCOUNT_ID:function:esa-ontology-ingest",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": { "Key": { "FilterRules": [
        { "Name": "prefix", "Value": "data/centree/" },
        { "Name": "suffix", "Value": ".ttl" }
      ]}}
    },
    {
      "Id": "RecordsTrigger",
      "LambdaFunctionArn": "arn:aws:lambda:us-east-1:YOUR_AWS_ACCOUNT_ID:function:esa-abox-generation",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": { "Key": { "FilterRules": [
        { "Name": "prefix", "Value": "data/records/" },
        { "Name": "suffix", "Value": ".json" }
      ]}}
    },
    {
      "Id": "DocumentsTrigger",
      "LambdaFunctionArn": "arn:aws:lambda:us-east-1:YOUR_AWS_ACCOUNT_ID:function:esa-document-index",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": { "Key": { "FilterRules": [
        { "Name": "prefix", "Value": "data/documents/" }
      ]}}
    },
    {
      "Id": "NeoGraphLoadTrigger",
      "LambdaFunctionArn": "arn:aws:lambda:us-east-1:YOUR_AWS_ACCOUNT_ID:function:esa-graph-load",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": { "Key": { "FilterRules": [
        { "Name": "prefix", "Value": "knowledge/abox/" },
        { "Name": "suffix", "Value": ".nq" }
      ]}}
    }
  ]
}
```

Apply the triggers:

```cmd
aws s3api put-bucket-notification-configuration ^
  --bucket YOUR_S3_BUCKET ^
  --notification-configuration file://notification.json
```

---

## Step 7 - Upload Sample Data & Test the Full Flow

```cmd
:: Trigger Ontology Lambda
aws s3 cp data\centree\ontology.ttl s3://YOUR_S3_BUCKET/data/centree/ontology.ttl

:: Trigger A-Box Lambda (auto-triggers Graph Load after writing .nq)
aws s3 cp data\records\customers.json s3://YOUR_S3_BUCKET/data/records/customers.json
aws s3 cp data\records\accounts.json  s3://YOUR_S3_BUCKET/data/records/accounts.json
aws s3 cp data\records\loans.json     s3://YOUR_S3_BUCKET/data/records/loans.json

:: Trigger Document Lambda
aws s3 cp data\documents\ s3://YOUR_S3_BUCKET/data/documents/ --recursive
```

---

## Step 8 - Monitor in CloudWatch

```cmd
aws logs tail /aws/lambda/esa-ontology-ingest --follow
aws logs tail /aws/lambda/esa-abox-generation --follow
aws logs tail /aws/lambda/esa-document-index  --follow
aws logs tail /aws/lambda/esa-graph-load      --follow
```

---

## Step 9 - Redeploy After Code Changes

```cmd
:: 1. Rebuild the Docker image
docker build -t esa-pipeline-repo .

:: 2. Tag and Push the new image
docker tag esa-pipeline-repo:latest YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest
docker push YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest

:: 3. Update all four functions to use the new image
aws lambda update-function-code --function-name esa-ontology-ingest --image-uri YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest
aws lambda update-function-code --function-name esa-abox-generation  --image-uri YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest
aws lambda update-function-code --function-name esa-document-index   --image-uri YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest
aws lambda update-function-code --function-name esa-graph-load       --image-uri YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest
```

---

## Environment Variable Reference

| Variable | Lambda | Value |
|---|---|---|
| `S3_BUCKET_NAME` | All | Your S3 bucket name |
| `AWS_EXECUTION_ENV` | All | `true` |
| `AWS_REGION` | All | `us-east-1` |
| `RDF_OUTPUT_KEY` | ontology, abox, graph-load | `knowledge/abox/instances.nq` |
| `STRUCTURED_DATA_PATH` | abox | `/tmp/records` |
| `DOCS_DIR` | document-index | `/tmp/documents` |
| `EMBEDDING_PROVIDER` | document-index | `bedrock` |
| `LLM_PROVIDER` | document-index | `bedrock` |
| `NEO4J_URI` | graph-load | `neo4j+s://...` or `bolt://...` |
| `NEO4J_USER` | graph-load | `neo4j` |
| `NEO4J_PASSWORD` | graph-load | your password |
| `NEO4J_DATABASE` | graph-load | `neo4j` |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `AccessDenied` on S3 | Verify `EsaLambdaExecutionRole` has `AmazonS3FullAccess` |
| Neo4j `ServiceUnavailable` | Check URI is correct; if EC2-hosted open port 7687 in its Security Group |
| Lambda timeout | `aws lambda update-function-configuration --function-name esa-graph-load --timeout 600` |
| S3 trigger not firing | Verify Lambda resource policy (Step 5) and that S3 prefix matches the key path exactly |
| Bedrock `AccessDeniedException` | Enable the model in Bedrock Console -> Model Access |
| ECR `no basic auth credentials` | Ensure Docker Desktop is running and you ran the `aws ecr get-login-password` command (Step 3). |
