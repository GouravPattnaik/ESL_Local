import json
import os
import boto3
from .config import setting

def _s3_key_from_event(event: dict, fallback_env: str, fallback_default: str) -> str:
    """Extract the S3 object key from an S3 trigger event, or fall back to an env var."""
    try:
        return event["Records"][0]["s3"]["object"]["key"]
    except (KeyError, IndexError, TypeError):
        return setting(fallback_env, fallback_default)

def _s3_bucket_from_event(event: dict) -> str:
    """Extract the S3 bucket name from an S3 trigger event, or fall back to env var."""
    try:
        return event["Records"][0]["s3"]["bucket"]["name"]
    except (KeyError, IndexError, TypeError):
        return os.environ["S3_BUCKET_NAME"]


def ontology_handler(event, context):
    """Lambda: triggered by .ttl upload to S3. Downloads the Turtle file then runs ontology ingestion."""
    from .rdf import parse_tbox
    from .storage import write_artifact

    bucket = _s3_bucket_from_event(event)
    key    = _s3_key_from_event(event, "CENTREE_TTL_PATH", "data/centree/ontology.ttl")

    local_ttl = f"/tmp/{os.path.basename(key)}"
    boto3.client("s3").download_file(bucket, key, local_ttl)

    data, count = parse_tbox(local_ttl)
    out_key = setting("RDF_OUTPUT_KEY", "ontology/centree/ontology.nq")
    write_artifact(out_key, data)
    return {"statusCode": 200, "body": json.dumps({"status": "SUCCESS", "triple_count": count, "output_key": out_key})}


def abox_handler(event, context):
    """Lambda: triggered by JSON record uploads to S3. Downloads all three record files then builds A-Box RDF."""
    import json as _json
    from .rdf import build_abox
    from .entity_resolution import resolve_customers
    from .storage import write_artifact

    bucket = _s3_bucket_from_event(event)
    s3 = boto3.client("s3")

    def _download_json(s3_key: str) -> list:
        local = f"/tmp/{os.path.basename(s3_key)}"
        try:
            s3.download_file(bucket, s3_key, local)
            with open(local, encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            return []

    records_prefix = setting("STRUCTURED_DATA_PATH", "data/records")
    customers = _download_json(f"{records_prefix}/customers.json")
    accounts  = _download_json(f"{records_prefix}/accounts.json")
    loans     = _download_json(f"{records_prefix}/loans.json")

    er = resolve_customers(customers)
    rdf, triple_count = build_abox(customers, accounts, loans)

    out_key = setting("RDF_OUTPUT_KEY", "knowledge/abox/instances.nq")
    write_artifact(out_key, rdf)
    write_artifact("knowledge/entity_resolution/results.json", _json.dumps(er, indent=2).encode())
    return {"statusCode": 200, "body": json.dumps({"status": "SUCCESS", "triple_count": triple_count})}


def document_index_handler(event, context):
    """Lambda: triggered by document uploads. Downloads docs to /tmp then indexes into vector store."""
    from .documents import load_chunks
    from .vectorstore import index_chunks

    bucket = _s3_bucket_from_event(event)
    s3 = boto3.client("s3")

    # Download all objects under the docs prefix to /tmp
    prefix   = setting("DOCS_PREFIX", "data/documents/")
    tmp_dir  = "/tmp/documents"
    os.makedirs(tmp_dir, exist_ok=True)

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            fname = os.path.basename(obj["Key"])
            if fname:
                s3.download_file(bucket, obj["Key"], os.path.join(tmp_dir, fname))

    chunks = load_chunks(tmp_dir)
    count  = index_chunks(chunks)
    return {"statusCode": 200, "body": json.dumps({"status": "SUCCESS", "chunks_indexed": count})}

def graph_load_handler(event, context):
    """
    AWS Lambda handler for Neo4j ingestion.
    Triggers when N-Quads (.nq) artifacts are created in S3.
    """
    import boto3
    import os
    from .graph_neo4j import load_nquads_typed
    from .config import setting
    
    s3 = boto3.client("s3")
    bucket = event.get("Records", [{}])[0].get("s3", {}).get("bucket", {}).get("name", setting("S3_BUCKET_NAME"))
    key = event.get("Records", [{}])[0].get("s3", {}).get("object", {}).get("key", setting("RDF_OUTPUT_KEY", "knowledge/abox/instances.nq"))
    
    local_path = f"/tmp/{os.path.basename(key)}"
    s3.download_file(bucket, key, local_path)
    
    count = load_nquads_typed(local_path)
    return {"statusCode": 200, "body": json.dumps({"status": "SUCCESS", "nodes_merged": count})}

# AWS event adapters: download S3 object(s) to /tmp and route to the
# corresponding function. Configure separate Lambda entry points per function.
# Never let a Lambda's output prefix retrigger the same S3 event indefinitely.

