# Cloud drill (Phase 10, ADR-0012): one short-lived EC2 instance that deploys Centinela on k3s,
# runs the end-to-end test and terminates itself. Separate state from infra/terraform (which
# configures the Kubernetes cluster itself).
terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.72"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "centinela"
      Purpose   = "cloud-drill"
      ManagedBy = "terraform"
    }
  }
}
