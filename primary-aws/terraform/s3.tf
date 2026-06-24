# R8: 암호문(이중 봉투) 전달용 스테이징 bucket — 평문·mnemonic·비밀번호는 절대 이 경로로 다니지 않는다.
resource "aws_s3_bucket" "staging" {
  count         = var.enable_staging_bucket ? 1 : 0
  bucket        = "${local.project}-${var.network}-staging-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.allow_protected_destroy
}

resource "aws_s3_bucket_public_access_block" "staging" {
  count                   = var.enable_staging_bucket ? 1 : 0
  bucket                  = aws_s3_bucket.staging[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "staging" {
  count  = var.enable_staging_bucket ? 1 : 0
  bucket = aws_s3_bucket.staging[0].id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "staging" {
  count  = var.enable_staging_bucket ? 1 : 0
  bucket = aws_s3_bucket.staging[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.keystore.arn
    }
    bucket_key_enabled = true
  }
}

data "aws_iam_policy_document" "staging" {
  count = var.enable_staging_bucket ? 1 : 0

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.staging[0].arn, "${aws_s3_bucket.staging[0].arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid           = "DenyWritesOutsideStagedPrefix"
    effect        = "Deny"
    actions       = ["s3:PutObject"]
    not_resources = ["${aws_s3_bucket.staging[0].arn}/staged/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }

  statement {
    sid       = "DenyStagedWritesWithoutSseKms"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.staging[0].arn}/staged/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }

  statement {
    sid       = "DenyStagedWritesWithWrongKmsKey"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.staging[0].arn}/staged/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnNotEqualsIfExists"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [aws_kms_key.keystore.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "staging" {
  count  = var.enable_staging_bucket ? 1 : 0
  bucket = aws_s3_bucket.staging[0].id
  policy = data.aws_iam_policy_document.staging[0].json
}
