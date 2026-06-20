terraform {
  required_version = ">= 1.10.0" # P1-01: S3 native lock(use_lockfile)은 1.10+ 전용. 1.9 분기 폐지
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = local.common_tags }
}

# A2: 복구용 KMS는 주노드와 "다른" 리전 — 시나리오 B(리전 불능)에서 wrap-A 생존
provider "aws" {
  alias  = "recovery"
  region = var.kms_recovery_region
  default_tags { tags = local.common_tags }
}
