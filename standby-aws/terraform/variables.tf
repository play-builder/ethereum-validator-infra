variable "network" {
  description = "Ethereum network for the standby node."
  type        = string
  default     = "hoodi"

  validation {
    condition     = var.network == "hoodi"
    error_message = "Course 1 standby is Hoodi only."
  }
}

variable "region" {
  description = "AWS Standby region. Must differ from the Primary AWS_REGION."
  type        = string
  default     = "eu-central-1"
}

variable "key_pair_name" {
  description = "Regional EC2 key pair name imported by this Terraform root."
  type        = string
}

variable "node_ssh_public_key" {
  description = "Same public Ed25519 key used by the CH01 bootstrap; public material only."
  type        = string

  validation {
    condition     = can(regex("^ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?$", var.node_ssh_public_key))
    error_message = "node_ssh_public_key must be one OpenSSH ssh-ed25519 public-key line."
  }
}

variable "node_instance_profile_name" {
  description = "Existing workload-account instance profile created by the CH01 bootstrap."
  type        = string
  default     = "eth-failover-hoodi-node"
}

variable "admin_cidrs" {
  description = "Approved management IPv4 addresses, each as a canonical /32."
  type        = list(string)

  validation {
    condition = length(var.admin_cidrs) > 0 && alltrue([
      for cidr in var.admin_cidrs :
      can(cidrhost(cidr, 0)) && endswith(cidr, "/32") && cidr != "0.0.0.0/0"
    ])
    error_message = "admin_cidrs must contain approved IPv4 /32 values only."
  }
}

variable "backup_peer_public_ip" {
  description = "Primary EC2 public IPv4 allowed to reach Standby WireGuard."
  type        = string
  default     = null
  nullable    = true
}

variable "instance_type" {
  description = "Standby EC2 instance type."
  type        = string
  default     = "r7i.2xlarge"
}

variable "vpc_cidr" {
  type    = string
  default = "10.52.0.0/16"
}

variable "public_subnet_cidr" {
  type    = string
  default = "10.52.10.0/24"
}

variable "chain_volume_size_gib" {
  type    = number
  default = 2048
}

variable "validator_volume_size_gib" {
  type    = number
  default = 32
}

variable "allow_protected_destroy" {
  description = "Controls EC2 termination protection only. Data EBS lifecycle remains protected."
  type        = bool
  default     = false
}
