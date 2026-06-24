output "staging_bucket" {
  value = var.enable_staging_bucket ? aws_s3_bucket.staging[0].bucket : null
}
