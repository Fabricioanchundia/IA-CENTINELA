# Security and cost choices (ADR-0012):
# - no inbound rule at all, no SSH key, no instance role: nothing on the internet can reach it,
#   and it holds no AWS credentials. Results are read from the EC2 console output.
# - IMDSv2 only, encrypted disk deleted with the instance.
# - the instance schedules its own power-off at boot and "shutdown" means "terminate":
#   even if nobody runs `terraform destroy`, it costs at most max_minutes of compute.

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "drill" {
  name_prefix = "centinela-drill-"
  description = "Centinela cloud drill: outbound only (packages, images, model), no inbound"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "Downloads: OS packages, container images, the model, the repository"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_instance" "drill" {
  ami                                  = data.aws_ami.ubuntu.id
  instance_type                        = var.instance_type
  subnet_id                            = sort(data.aws_subnets.default.ids)[0]
  vpc_security_group_ids               = [aws_security_group.drill.id]
  associate_public_ip_address          = true # egress only: there is no NAT in the default VPC
  instance_initiated_shutdown_behavior = "terminate"

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_size           = 40
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/drill.sh.tftpl", {
    git_ref     = var.git_ref
    model       = var.model
    max_minutes = var.max_minutes
  })
  user_data_replace_on_change = true

  tags = {
    Name = "centinela-drill"
  }
}
