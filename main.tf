# AWS Secrets Manager for RDS credentials
resource "aws_secretsmanager_secret" "rds_secrets" {
  for_each = local.configs
  name     = "rds-secret-${each.key}"
}

resource "aws_secretsmanager_secret_version" "rds_secret_versions" {
  for_each = local.configs
  secret_id = aws_secretsmanager_secret.rds_secrets[each.key].id
  secret_string = jsonencode(each.value["SecretsManager"])
}

# 数据库连接
resource "aws_glue_connection" "mariadb_connection" {
  for_each = local.configs
  name     = "connection-${each.key}"
  
  connection_properties = {
    JDBC_CONNECTION_URL     = local.connection_urls[each.key].url
    JDBC_DRIVER_CLASS_NAME  = local.connection_settings.jdbc_driver.class_name
    JDBC_DRIVER_JAR_URI     = local.connection_settings.jdbc_driver.jar_uri
    SECRET_ID               = aws_secretsmanager_secret.rds_secrets[each.key].name
  }
  
  connection_type = "JDBC"
  
  physical_connection_requirements {
    availability_zone      = local.connection_settings.availability_zone
    security_group_id_list = [local.connection_settings.security_group_id]
    subnet_id             = local.connection_settings.subnet_id
  }
}

# Glue 作业定义
resource "aws_glue_job" "gule_test_job" {
  for_each          = local.configs
  name              = "job-${each.key}"
  role_arn          = "arn:aws:iam::566601428909:role/onewonder-glue-test"
  glue_version      = local.glue_settings.glue_version
  worker_type       = local.glue_settings.worker_type
  number_of_workers = local.glue_settings.number_of_workers
  timeout           = local.glue_settings.timeout_minutes

  command {
    name            = "glueetl"
    script_location = "s3://mariabd-old/scripts/gule_test_job.py"
    python_version  = "3"
  }
  
  default_arguments = {
    "--enable-glue-datacatalog" = "true"
    "--job-language"            = "python"
    "--TempDir"                 = "s3://maria-new/temp/"
    "--spark-event-logs-path"   = "s3://maria-new/sparkHistoryLogs/"
    # S3 configurations from configs.json
    "--source_bucket"           = local.s3_paths[each.key].source.bucket
    "--source_key"              = local.s3_paths[each.key].source.key
    "--destination_bucket"      = local.s3_paths[each.key].destination.bucket
    "--destination_file"        = local.s3_paths[each.key].destination.file
    # Add secret reference
    "--secret_name"             = aws_secretsmanager_secret.rds_secrets[each.key].name
    # connection_name
    "--connection_name"         = aws_glue_connection.mariadb_connection[each.key].name
    # Slack webhook URL
    "--slack_webhook"           = local.slack_webhooks[each.key].webhook_url
  }
  
  connections = [aws_glue_connection.mariadb_connection[each.key].name]
  
  execution_property {
    max_concurrent_runs = 1
  }
}
