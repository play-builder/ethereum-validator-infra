variable "admin_cidrs" {
  description = "SSH 허용 CIDR 목록. canonical public IPv4 host route(/32)만 허용한다."
  type        = list(string)
  validation {
    condition = length(var.admin_cidrs) > 0 && alltrue([
      for cidr in var.admin_cidrs :
      can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}/32$", cidr)) && try(
        cidrhost(cidr, 0) == split("/", cidr)[0],
        false,
      )
    ])
    error_message = "admin_cidrs must contain only canonical IPv4 host routes with a /32 prefix."
  }
}

variable "backup_peer_public_ip" {
  description = "Standby EC2의 공인 IPv4 (WireGuard 51820/udp 허용 대상). null이면 해당 인바운드 규칙을 만들지 않는다."
  type        = string
  default     = null

  validation {
    condition = var.backup_peer_public_ip == null || (can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.backup_peer_public_ip)) && try(
      cidrhost("${var.backup_peer_public_ip}/32", 0) == var.backup_peer_public_ip,
      false,
    ))
    error_message = "backup_peer_public_ip must be null or one canonical plain IPv4 address without a CIDR suffix."
  }
}
