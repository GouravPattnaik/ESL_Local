import json
from .pipeline import run_ontology, run_abox, run_documents

def ontology_handler(event, context):
    return {"statusCode":200, "body":json.dumps(run_ontology())}
def abox_handler(event, context):
    return {"statusCode":200, "body":json.dumps(run_abox())}
def document_index_handler(event, context):
    return {"statusCode":200, "body":json.dumps(run_documents())}

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

