variable "instance_type" {
  description = "Hoodi 기본값. mainnet 전환 시 storage/메모리 재산정(part-12)."
  type        = string
  default     = "m7i.2xlarge"
}

variable "ami_id" {
  description = "Ubuntu 24.04 LTS amd64 AMI. 빈 값이면 SSM public parameter로 최신 조회."
  type        = string
  default     = ""
}

variable "key_pair_name" {
  description = "CloudFormation CI bootstrap이 local ED25519 public key를 import해 만든 exact EC2 key pair 이름. 개인키는 Terraform과 AWS 밖의 operator workstation에만 보관한다."
  type        = string
}

variable "chaindata_volume_gb" {
  description = "EL+CL chaindata EBS 크기. Hoodi 초기 600GB 권장, mainnet은 별도 산정."
  type        = number
  default     = 600
}

variable "chaindata_iops" {
  type    = number
  default = 6000
}

variable "chaindata_throughput_mbps" {
  type    = number
  default = 500
}

variable "validator_state_volume_gb" {
  description = "R4: slashing protection/VC 상태 전용 EBS. 작고 독립적으로."
  type        = number
  default     = 20
}
