# Jyotiveda reference cloud: AWS ap-south-1 (Mumbai) for Indian data residency (DPDP Act, 2023; CEA cyber guidelines).
# EKS for cells, MSK Serverless for the event backbone, ElastiCache Redis, S3 (data lake + model artefacts),
# KMS everywhere, private subnets only, VPN / Direct Connect to DISCOM SCADA-HES networks.
# TimescaleDB + PostGIS run on EKS via the CloudNativePG operator (timescaledb-ha image) with S3 backups.

terraform {
  required_version = ">= 1.8"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.70" }
  }
  backend "s3" {
    bucket         = "jyotiveda-tfstate-prod"
    key            = "platform/terraform.tfstate"
    region         = "ap-south-1"
    dynamodb_table = "jyotiveda-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = { project = "jyotiveda", env = var.env, owner = "platform" } }
}

data "aws_availability_zones" "azs" { state = "available" }

resource "aws_kms_key" "platform" {
  description             = "Jyotiveda platform encryption key"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name                 = "jyotiveda-${var.env}"
  cidr                 = "10.40.0.0/16"
  azs                  = slice(data.aws_availability_zones.azs.names, 0, 3)
  private_subnets      = ["10.40.0.0/19", "10.40.32.0/19", "10.40.64.0/19"]
  public_subnets       = ["10.40.96.0/22", "10.40.100.0/22", "10.40.104.0/22"]
  enable_nat_gateway   = true
  single_nat_gateway   = var.env != "prod"
  enable_flow_log      = true
  create_flow_log_cloudwatch_log_group = true
  create_flow_log_cloudwatch_iam_role  = true
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name                    = "jyotiveda-${var.env}"
  cluster_version                 = "1.31"
  vpc_id                          = module.vpc.vpc_id
  subnet_ids                      = module.vpc.private_subnets
  cluster_endpoint_public_access  = false
  enable_irsa                     = true
  cluster_encryption_config       = { resources = ["secrets"], provider_key_arn = aws_kms_key.platform.arn }

  eks_managed_node_groups = {
    cells = {
      instance_types = ["m7i.2xlarge"]
      min_size       = 3
      max_size       = 12
      desired_size   = 3
      labels         = { "jyotiveda.io/pool" = "cells" }
    }
    ml = {
      instance_types = ["g6.xlarge"] # Chronos-2 / GNN inference + nightly training
      min_size       = 0
      max_size       = 4
      desired_size   = 1
      labels         = { "jyotiveda.io/pool" = "ml" }
      taints         = [{ key = "nvidia.com/gpu", value = "true", effect = "NO_SCHEDULE" }]
    }
  }
}

resource "aws_msk_serverless_cluster" "events" {
  cluster_name = "jyotiveda-${var.env}"
  vpc_config {
    subnet_ids         = module.vpc.private_subnets
    security_group_ids = [aws_security_group.data.id]
  }
  client_authentication { sasl { iam { enabled = true } } }
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "jyotiveda-${var.env}"
  description                = "live state cache + rate limits"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = "cache.r7g.large"
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = aws_kms_key.platform.arn
  subnet_group_name          = aws_elasticache_subnet_group.redis.name
  security_group_ids         = [aws_security_group.data.id]
}

resource "aws_elasticache_subnet_group" "redis" {
  name       = "jyotiveda-${var.env}"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "data" {
  name   = "jyotiveda-data-${var.env}"
  vpc_id = module.vpc.vpc_id
  ingress {
    description     = "from EKS nodes only"
    from_port       = 0
    to_port         = 65535
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["10.40.0.0/16"]
  }
}

resource "aws_s3_bucket" "lake" {
  bucket = "jyotiveda-${var.env}-lake"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.platform.arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_secretsmanager_secret" "signing_key" {
  name       = "jyotiveda/${var.env}/dispatch_ed25519_pem"
  kms_key_id = aws_kms_key.platform.arn
}
