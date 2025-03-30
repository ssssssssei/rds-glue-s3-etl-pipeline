provider "aws" {
  region = "ap-northeast-1"
}

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "5.53.0"
    }
  }
  required_version = ">= 1.6.6"

  backend "s3" {
    bucket  = "onewonder-tfstate"
    region  = "ap-northeast-1"
    key     = "rds-glue-s3-etl-pipeline/terraform.tfstate"
    encrypt = true
  }
}