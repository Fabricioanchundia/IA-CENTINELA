variable "region" {
  description = "AWS region (the same one as the other free-plan project)."
  type        = string
  default     = "us-east-2"
}

variable "instance_type" {
  description = "Free-plan eligible, 2 vCPU and 8 GiB: enough for k3s, the stack and a small model."
  type        = string
  default     = "m7i-flex.large"
}

variable "git_ref" {
  description = "Branch or tag of the public repository to test."
  type        = string
  default     = "main"

  validation {
    condition     = can(regex("^[A-Za-z0-9._/-]{1,100}$", var.git_ref))
    error_message = "git_ref must be a plain branch or tag name."
  }
}

variable "model" {
  description = "Ollama model for the agent, on CPU (there is no GPU)."
  type        = string
  default     = "qwen3:1.7b"
}

variable "max_minutes" {
  description = "Hard limit: the instance powers off and terminates itself after this long."
  type        = number
  default     = 120

  validation {
    condition     = var.max_minutes >= 30 && var.max_minutes <= 180
    error_message = "max_minutes must be between 30 and 180."
  }
}
