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

# ---------------------------------------------------------------------------------------
# Incident history (Phase 5): owner credentials for the collector and a read-only
# password for Grafana. The collector creates the `grafana_reader` role with it.
# ---------------------------------------------------------------------------------------
resource "random_password" "incidents_db" {
  length  = 32
  special = false
}

resource "random_password" "incidents_reader" {
  length  = 32
  special = false
}

resource "kubernetes_secret_v1" "incidents_db" {
  metadata {
    name      = "incidents-db-credentials"
    namespace = kubernetes_namespace_v1.this["centinela"].metadata[0].name
  }

  data = {
    POSTGRES_USER                = "centinela"
    POSTGRES_PASSWORD            = random_password.incidents_db.result
    POSTGRES_DB                  = "centinela"
    PGUSER                       = "centinela"
    PGPASSWORD                   = random_password.incidents_db.result
    PGDATABASE                   = "centinela"
    INCIDENTS_DB_READER_PASSWORD = random_password.incidents_reader.result
  }
}

resource "kubernetes_secret_v1" "incidents_reader" {
  metadata {
    name      = "incidents-db-reader"
    namespace = kubernetes_namespace_v1.this["observability"].metadata[0].name
  }

  data = {
    INCIDENTS_DB_READER_PASSWORD = random_password.incidents_reader.result
  }
}
