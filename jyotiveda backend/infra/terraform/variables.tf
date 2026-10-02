variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "env" {
  type    = string
  default = "prod"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.env)
    error_message = "env must be dev, staging or prod"
  }
}

output "cluster_name" { value = module.eks.cluster_name }
output "msk_arn" { value = aws_msk_serverless_cluster.events.arn }
output "redis_endpoint" { value = aws_elasticache_replication_group.redis.primary_endpoint_address }
output "lake_bucket" { value = aws_s3_bucket.lake.bucket }
