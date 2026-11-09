output "namespaces" {
  description = "Namespaces created for Centinela."
  value       = [for ns in kubernetes_namespace_v1.this : ns.metadata[0].name]
}

output "agent_service_account" {
  description = "Identity the agent will use inside the cluster."
  value       = "${local.agent_ns}/${kubernetes_service_account_v1.agent.metadata[0].name}"
}

output "grafana_admin_password" {
  description = "Only needed to edit dashboards: terraform output -raw grafana_admin_password"
  value       = random_password.grafana_admin.result
  sensitive   = true
}
