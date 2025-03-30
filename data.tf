# 读取配置文件
locals {
  # 读取configs.json
  configs_raw = jsondecode(file("configs.json"))
  configs = local.configs_raw.configs

  # 读取common_settings.json
  common_settings = jsondecode(file("common_settings.json"))
  
  # Glue 设置
  glue_settings = local.common_settings.Glue
  
  # 连接设置
  connection_settings = local.common_settings.connection

  # 为每个配置生成连接URL
  connection_urls = {
    for key, value in local.configs : key => {
      url = "jdbc:${value.Database.dbtype}://${value.Database.endpoint}:${value.Database.port}/${value.SecretsManager.db_name}"
    }
  }

  # 为每个配置生成S3路径
  s3_paths = {
    for key, value in local.configs : key => {
      source = {
        bucket = value.S3.source_bucket
        key    = value.S3.source_key
      }
      destination = {
        bucket = value.S3.destination_bucket
        file   = value.S3.destination_file
      }
    }
  }
  
  # 为每个配置获取Slack Webhook URL
  slack_webhooks = {
    for key, value in local.configs : key => {
      webhook_url = try(value.slack.slack_webhook, "")
    }
  }
}

# 输出配置以供检查
output "available_configs" {
  value = keys(local.configs)
  description = "Available configuration keys"
}

output "glue_settings" {
  value = local.glue_settings
  description = "Glue common settings"
}

output "connection_settings" {
  value = local.connection_settings
  description = "Connection common settings"
}

output "slack_webhooks" {
  value = local.slack_webhooks
  description = "Slack webhook URLs for each configuration"
  sensitive = true
} 