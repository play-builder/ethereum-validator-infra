output "kms_keystore_key_arn" {
  value = aws_kms_key.keystore.arn
}
output "kms_keystore_aliases" {
  description = "R1: 두 alias 모두 같은 키를 가리킨다"
  value       = [aws_kms_alias.keystore_primary.name, aws_kms_alias.keystore_docs_compat.name]
}
output "kms_recovery_key_arn" {
  description = "A2 wrap-A (타리전). RB-04에서 사용"
  value       = aws_kms_key.recovery.arn
}
