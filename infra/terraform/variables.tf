variable "kubeconfig_path" {
  description = "Path to the kubeconfig written by k3d."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Kubeconfig context of the local k3d cluster."
  type        = string
  default     = "k3d-centinela"
}

variable "postgres_user" {
  description = "Database user for the demo shop."
  type        = string
  default     = "shop"
}

variable "postgres_db" {
  description = "Database name for the demo shop."
  type        = string
  default     = "shop"
}
