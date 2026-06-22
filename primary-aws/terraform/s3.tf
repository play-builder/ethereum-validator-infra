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
