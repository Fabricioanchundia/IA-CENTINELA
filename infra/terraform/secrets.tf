# Passwords are generated here and stored only in Kubernetes Secrets and in the local,
# git-ignored Terraform state. No human ever types or sees them.

resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "random_password" "grafana_admin" {
  length  = 24
  special = false
}

resource "kubernetes_secret_v1" "postgres" {
  metadata {
    name      = "postgres-credentials"
    namespace = kubernetes_namespace_v1.this["shop"].metadata[0].name
  }

  # Both naming styles: POSTGRES_* for the database image, PG* (libpq) for the services
  data = {
    POSTGRES_USER     = var.postgres_user
    POSTGRES_PASSWORD = random_password.postgres.result
    POSTGRES_DB       = var.postgres_db
    PGUSER            = var.postgres_user
    PGPASSWORD        = random_password.postgres.result
    PGDATABASE        = var.postgres_db
  }
}

resource "kubernetes_secret_v1" "grafana" {
  metadata {
    name      = "grafana-admin"
    namespace = kubernetes_namespace_v1.this["observability"].metadata[0].name
  }

  data = {
    GF_SECURITY_ADMIN_PASSWORD = random_password.grafana_admin.result
  }
}
