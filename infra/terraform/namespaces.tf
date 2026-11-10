# One namespace per concern. Quotas make sure no namespace (for example, a chaos
# experiment in Phase 9) can eat the whole laptop.

locals {
  namespaces = {
    shop = {
      purpose      = "demo-shop"
      limit_memory = "1536Mi" # 5 workloads (~800 Mi) + room for rolling updates
      pods         = 15
    }
    observability = {
      purpose      = "observability"
      limit_memory = "2Gi" # 4 workloads (~1408 Mi) + room for one rolling update (Grafana, 512 Mi)
      pods         = 10
    }
    centinela = {
      purpose      = "agent"
      limit_memory = "1Gi" # incident DB, collector, agent, executor, console (~800 Mi limits)
      pods         = 8
    }
  }
}

resource "kubernetes_namespace_v1" "this" {
  for_each = local.namespaces

  metadata {
    name = each.key
    labels = {
      "app.kubernetes.io/part-of"    = "centinela"
      "app.kubernetes.io/managed-by" = "terraform"
      "centinela.io/purpose"         = each.value.purpose
      # Reject privileged pods, host mounts, root users... (Pod Security Standards)
      "pod-security.kubernetes.io/enforce" = "restricted"
      "pod-security.kubernetes.io/warn"    = "restricted"
    }
  }
}

resource "kubernetes_resource_quota_v1" "this" {
  for_each = local.namespaces

  metadata {
    name      = "memory-and-pods"
    namespace = kubernetes_namespace_v1.this[each.key].metadata[0].name
  }

  spec {
    hard = {
      "limits.memory" = each.value.limit_memory
      "pods"          = each.value.pods
    }
  }
}

# Defaults for any container that forgets to declare resources (the quota requires them)
resource "kubernetes_limit_range_v1" "this" {
  for_each = local.namespaces

  metadata {
    name      = "container-defaults"
    namespace = kubernetes_namespace_v1.this[each.key].metadata[0].name
  }

  spec {
    limit {
      type = "Container"
      default = {
        memory = "128Mi"
        cpu    = "500m"
      }
      default_request = {
        memory = "64Mi"
        cpu    = "50m"
      }
    }
  }
}
