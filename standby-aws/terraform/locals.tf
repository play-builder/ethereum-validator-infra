locals {
  name = "eth-${var.network}-standby"
  common_tags = {
    Project = "eth-failover"
    Network = var.network
    Role    = "standby"
    Managed = "terraform"
  }
}
