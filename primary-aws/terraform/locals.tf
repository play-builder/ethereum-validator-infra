locals {
  project = "eth-failover"

  common_tags = {
    Project = local.project
    Network = var.network
    Role    = "primary"
    Managed = "terraform"
  }
}
