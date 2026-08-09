output "instance_id" { value = aws_instance.standby.id }
output "public_ip" { value = aws_eip.standby.public_ip }
output "private_ip" { value = aws_instance.standby.private_ip }
output "security_group_id" { value = aws_security_group.node.id }
output "chain_volume_id" { value = aws_ebs_volume.chain.id }
output "validator_volume_id" { value = aws_ebs_volume.validator.id }
