resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  bucket_name = "${var.bucket_prefix}-${random_id.suffix.hex}"
  bucket_id   = var.create_with_cli ? data.aws_s3_bucket.this[0].id : aws_s3_bucket.this[0].id
  bucket_arn  = var.create_with_cli ? data.aws_s3_bucket.this[0].arn : aws_s3_bucket.this[0].arn
}

# Recurso nativo (fuera de Learner Lab). En Learner Lab una SCP deniega
# s3:GetBucketObjectLockConfiguration, que el proveedor AWS lee siempre tras crear
# el bucket, así que allí se crea con AWS CLI (create_with_cli = true).
resource "aws_s3_bucket" "this" {
  count         = var.create_with_cli ? 0 : 1
  bucket        = local.bucket_name
  force_destroy = true
}

resource "terraform_data" "bucket" {
  count = var.create_with_cli ? 1 : 0
  input = { name = local.bucket_name, region = var.region }

  # Idempotente: si el bucket ya existe (y es nuestro) no hace nada. Funciona en sh y en cmd.
  provisioner "local-exec" {
    command = "aws s3api head-bucket --bucket ${self.input.name} || aws s3api create-bucket --bucket ${self.input.name} --region ${self.input.region}${self.input.region == "us-east-1" ? "" : " --create-bucket-configuration LocationConstraint=${self.input.region}"}"
  }

  # Vacía (versiones incluidas, ver scripts/s3_empty_bucket.py) y borra el bucket al destruir.
  provisioner "local-exec" {
    when       = destroy
    on_failure = continue
    command    = "python ${path.module}/../../../../scripts/s3_empty_bucket.py ${self.input.name}"
  }
}

data "aws_s3_bucket" "this" {
  count      = var.create_with_cli ? 1 : 0
  bucket     = local.bucket_name
  depends_on = [terraform_data.bucket]
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = local.bucket_id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = local.bucket_id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = local.bucket_id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket     = local.bucket_id
  depends_on = [aws_s3_bucket_versioning.this]

  dynamic "rule" {
    for_each = ["exports/", "processed/"]
    content {
      id     = "expire-${trimsuffix(rule.value, "/")}"
      status = "Enabled"
      filter {
        prefix = rule.value
      }
      expiration {
        days = var.expire_days
      }
      noncurrent_version_expiration {
        noncurrent_days = 7
      }
    }
  }

  rule {
    id     = "cleanup-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
