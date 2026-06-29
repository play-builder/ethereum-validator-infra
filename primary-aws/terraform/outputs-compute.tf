output "node_public_ip" {
  value = aws_eip.node.public_ip
}
output "instance_id" {
  value = aws_instance.node.id
}
output "validator_state_volume_id" {
  description = "RB-01 F3-①b: 복구 인스턴스 attach 대상"
  value       = aws_ebs_volume.validator_state.id
}
