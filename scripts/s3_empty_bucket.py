"""Vacía un bucket S3 versionado (todas las versiones y delete markers) y lo borra.

Uso: python scripts/s3_empty_bucket.py <bucket>
Lo llama `terraform destroy` (módulo storage). Solo usa AWS CLI, sin boto3.
"""
import json
import subprocess
import sys


def aws(*args: str) -> dict:
    out = subprocess.run(["aws", *args, "--output", "json"], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else {}


def main(bucket: str) -> None:
    deleted = 0
    while True:
        page = aws("s3api", "list-object-versions", "--bucket", bucket, "--max-items", "1000")
        objs = [{"Key": o["Key"], "VersionId": o["VersionId"]}
                for o in (page.get("Versions") or []) + (page.get("DeleteMarkers") or [])]
        if not objs:
            break
        aws("s3api", "delete-objects", "--bucket", bucket,
            "--delete", json.dumps({"Objects": objs, "Quiet": True}))
        deleted += len(objs)
    aws("s3api", "delete-bucket", "--bucket", bucket)
    print(f"bucket {bucket} eliminado ({deleted} versiones borradas)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: s3_empty_bucket.py <bucket>")
    main(sys.argv[1])
