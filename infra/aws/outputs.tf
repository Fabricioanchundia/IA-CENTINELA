output "instance_id" {
  description = "Read the progress with: aws ec2 get-console-output --latest --instance-id <id>"
  value       = aws_instance.drill.id
}

output "region" {
  value = var.region
}
