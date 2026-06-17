locals {
  # KMS alias의 기준이 되는 한 곳 — 실 alias와 문서 호환 alias가 같은 local에서 나온다.
  kms_alias_primary = "alias/eth-staking-${var.network}-keystore"
  kms_alias_docs    = "alias/eth-validator-keys-${var.network}"

  # Permission Set 역할의 실제 ARN:
  #   arn:aws:iam::<acct>:role/aws-reserved/sso.amazonaws.com/[<region>/]AWSReservedSSO_<이름>_<해시>
  # 해시는 재프로비저닝마다 바뀌고 리전 세그먼트 유무도 인스턴스에 따라 다르므로 와일드카드로 덮는다.
  kms_seal_operator_role_arn_pattern   = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_${var.sso_operator_permission_sets[0]}_*"
  kms_unseal_operator_role_arn_pattern = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_${var.sso_operator_permission_sets[1]}_*"
}
