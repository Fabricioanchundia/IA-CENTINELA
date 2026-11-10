# Least privilege: every component gets its own ServiceAccount with only what it needs.

locals {
  shop_ns  = kubernetes_namespace_v1.this["shop"].metadata[0].name
  obs_ns   = kubernetes_namespace_v1.this["observability"].metadata[0].name
  agent_ns = kubernetes_namespace_v1.this["centinela"].metadata[0].name
}

# ---------------------------------------------------------------------------------------
# Prometheus: discover and scrape shop pods, read container metrics from the kubelet
# ---------------------------------------------------------------------------------------
resource "kubernetes_service_account_v1" "prometheus" {
  metadata {
    name      = "prometheus"
    namespace = local.obs_ns
  }
}

resource "kubernetes_role_v1" "prometheus_shop" {
  metadata {
    name      = "prometheus-discovery"
    namespace = local.shop_ns
  }

  rule {
    api_groups = [""]
    resources  = ["pods"]
    verbs      = ["get", "list", "watch"]
  }
}

resource "kubernetes_role_binding_v1" "prometheus_shop" {
  metadata {
    name      = "prometheus-discovery"
    namespace = local.shop_ns
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.prometheus_shop.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.prometheus.metadata[0].name
    namespace = local.obs_ns
  }
}

# Node-level read access for container metrics (cAdvisor). Only `nodes/metrics`,
# never `nodes/proxy`, which would also allow exec into any pod through the kubelet.
resource "kubernetes_cluster_role_v1" "prometheus_nodes" {
  metadata {
    name = "centinela-prometheus-nodes"
  }

  rule {
    api_groups = [""]
    resources  = ["nodes"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = [""]
    resources  = ["nodes/metrics"]
    verbs      = ["get"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "prometheus_nodes" {
  metadata {
    name = "centinela-prometheus-nodes"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.prometheus_nodes.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.prometheus.metadata[0].name
    namespace = local.obs_ns
  }
}

# ---------------------------------------------------------------------------------------
# Alloy: read shop pod logs through the Kubernetes API (no host mounts needed)
# ---------------------------------------------------------------------------------------
resource "kubernetes_service_account_v1" "alloy" {
  metadata {
    name      = "alloy"
    namespace = local.obs_ns
  }
}

resource "kubernetes_role_v1" "alloy_shop" {
  metadata {
    name      = "alloy-logs"
    namespace = local.shop_ns
  }

  rule {
    api_groups = [""]
    resources  = ["pods", "pods/log"]
    verbs      = ["get", "list", "watch"]
  }
}

resource "kubernetes_role_binding_v1" "alloy_shop" {
  metadata {
    name      = "alloy-logs"
    namespace = local.shop_ns
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.alloy_shop.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.alloy.metadata[0].name
    namespace = local.obs_ns
  }
}

# ---------------------------------------------------------------------------------------
# Centinela agent: READ-ONLY on the shop for now (Phase 6 only diagnoses).
# Write actions (restart, rollback) get a separate, narrow role in Phase 7.
# No access to Secrets, ever.
# ---------------------------------------------------------------------------------------
resource "kubernetes_service_account_v1" "agent" {
  metadata {
    name      = "centinela-agent"
    namespace = local.agent_ns
  }
}

resource "kubernetes_role_v1" "agent_reader" {
  metadata {
    name      = "centinela-reader"
    namespace = local.shop_ns
  }

  rule {
    api_groups = [""]
    resources  = ["pods", "pods/log", "services", "endpoints", "events"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments", "replicasets", "statefulsets"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = ["metrics.k8s.io"]
    resources  = ["pods"]
    verbs      = ["get", "list"]
  }
}

resource "kubernetes_role_binding_v1" "agent_reader" {
  metadata {
    name      = "centinela-reader"
    namespace = local.shop_ns
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.agent_reader.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.agent.metadata[0].name
    namespace = local.agent_ns
  }
}

# ---------------------------------------------------------------------------------------
# Centinela executor (Phase 7): the ONLY identity that can change the shop, and only:
#   - get + patch, on four NAMED stateless deployments (resourceNames), and their /scale
# No create, no delete, no exec, no Secrets, no PostgreSQL, nothing outside `shop`.
# It runs only actions a human approved (src/centinela/actions/executor.py).
# ---------------------------------------------------------------------------------------
locals {
  executor_targets = ["catalog", "orders", "payments", "redis"] # = actions/policy.py TARGETS
}

resource "kubernetes_service_account_v1" "executor" {
  metadata {
    name      = "centinela-executor"
    namespace = local.agent_ns
  }
}

resource "kubernetes_role_v1" "executor" {
  metadata {
    name      = "centinela-executor"
    namespace = local.shop_ns
  }

  rule {
    api_groups     = ["apps"]
    resources      = ["deployments", "deployments/scale"]
    resource_names = local.executor_targets
    verbs          = ["get", "patch"]
  }
}

resource "kubernetes_role_binding_v1" "executor" {
  metadata {
    name      = "centinela-executor"
    namespace = local.shop_ns
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.executor.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.executor.metadata[0].name
    namespace = local.agent_ns
  }
}
