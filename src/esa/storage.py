from pathlib import Path
import os

# ── Environment-aware routing ────────────────────────────────────────────────
# Set AWS_EXECUTION_ENV=true in Lambda environment variables to switch to S3.
_IN_AWS = "AWS_LAMBDA_FUNCTION_NAME" in os.environ or os.getenv("AWS_EXECUTION_ENV", "false").lower() in ("true", "1", "yes")


def write_artifact(key: str, content: bytes) -> str:
    """Write bytes to S3 (in AWS) or local_bucket/ (locally)."""
    if _IN_AWS:
        return write_s3(key, content)
    return write_local(key, content)


def read_artifact(key: str) -> bytes:
    """Read bytes from S3 (in AWS) or local_bucket/ (locally)."""
    if _IN_AWS:
        return read_s3(key)
    return read_local(key)


def write_local(key: str, content: bytes) -> str:
    base = Path(os.getenv("LOCAL_BUCKET_DIR", "local_bucket"))
    rel = Path(key)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("Unsafe object key")
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def read_local(key: str) -> bytes:
    base = Path(os.getenv("LOCAL_BUCKET_DIR", "local_bucket"))
    rel = Path(key)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("Unsafe object key")
    return (base / rel).read_bytes()


def write_s3(key: str, content: bytes) -> str:
    import boto3
    client = boto3.client("s3")
    bucket = os.environ["S3_BUCKET_NAME"]
    client.put_object(Bucket=bucket, Key=key, Body=content)
    return f"s3://{bucket}/{key}"


def read_s3(key: str) -> bytes:
    import boto3
    client = boto3.client("s3")
    obj = client.get_object(Bucket=os.environ["S3_BUCKET_NAME"], Key=key)
    return obj["Body"].read()
