import sys
import json
import boto3
import requests
import pandas as pd
from io import StringIO
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql import SparkSession, Row
from pyspark.sql.functions import col, lit, when, isnan, isnull, expr, udf, pandas_udf
from pyspark.sql.types import IntegerType, StringType, StructType
import pyspark.sql.functions as F

# 初始化 Spark 和 Glue 上下文
args = getResolvedOptions(sys.argv, [
    'JOB_NAME', 
    'source_bucket', 
    'source_key', 
    'destination_bucket', 
    'destination_file', 
    'secret_name',
    'connection_name',
    'slack_webhook'
])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

# 从参数获取源和目标信息
source_bucket = args['source_bucket']
source_key = args['source_key']
destination_bucket = args['destination_bucket']
destination_file = args['destination_file']
secret_name = args['secret_name']
connection_name = args['connection_name']
slack_webhook = args['slack_webhook']

# 获取数据库连接凭证
secrets_client = boto3.client('secretsmanager')
secret_response = secrets_client.get_secret_value(SecretId=secret_name)
db_credentials = json.loads(secret_response['SecretString'])

# 定义数据库连接参数
db_name = db_credentials.get('db_name')
table_name = db_credentials.get('table_name')
print(f"数据库 {db_name}，表 {table_name}")

# 1. 从S3读取JSON数据（作为主数据源）
s3_client = boto3.client('s3')
try:
    s3_response = s3_client.get_object(Bucket=source_bucket, Key=source_key)
    json_content = s3_response['Body'].read().decode('utf-8')
    
    # 2. 解析JSON并保留原始顺序
    # 3. 使用pandas读取JSON
    s3_df_pd = pd.read_json(StringIO(json_content), orient='records')
    
    # 保留原始S3数据（未初始化额外列之前）
    original_s3_data = s3_df_pd.copy()
    
    # 获取原始S3列顺序（仅包括在JSON中实际存在的列）
    original_s3_columns = list(s3_df_pd.columns)
    print(f"S3 原始数据列: {original_s3_columns}")
    print(f"S3 数据行数: {s3_df_pd.shape[0]}")
    
    # 转换为Spark DataFrame（用于后续处理）
    s3_df = spark.createDataFrame(s3_df_pd)
    
except Exception as e:
    error_message = f"读取S3数据失败: {str(e)}"
    print(error_message)
    requests.post(slack_webhook, json={"text": f"ETL任务失败: {error_message}"})
    sys.exit(1)

# 4. 从RDS读取数据
try:
    maria_df = glueContext.create_dynamic_frame.from_options(
        # JDBC连接maria的时候是使用的mysql
        connection_type="mysql",
        connection_options={
            "connectionName": connection_name,
            "dbtable": table_name,
            "database": db_name,
            "useConnectionProperties": "true"
        }
    ).toDF()
    
    # 将RDS数据转换为pandas DataFrame
    rds_pd_df = maria_df.toPandas()
    
    print(f"RDS 数据列: {maria_df.columns}")
    print(f"RDS 数据行数: {maria_df.count()}")
    
except Exception as e:
    error_message = f"读取RDS数据失败: {str(e)}"
    print(error_message)
    requests.post(slack_webhook, json={"text": f"ETL任务失败: {error_message}"})
    sys.exit(1)

# 5. 根据id列进行匹配
# 确保两个DataFrame都有id列
if 'id' not in s3_df.columns or 'id' not in maria_df.columns:
    error_message = "S3数据或RDS数据中缺少id列"
    print(error_message)
    requests.post(slack_webhook, json={"text": f"ETL任务失败: {error_message}"})
    sys.exit(1)

# 获取所有可能的列（RDS和S3的所有列并集）
all_possible_columns = set(original_s3_columns) | set(rds_pd_df.columns)
print(f"所有可能的列: {all_possible_columns}")

# 正确处理每个记录
result_rows = []

# 收集S3数据中的所有ID，用于后续查找未匹配的RDS记录
s3_ids = set(original_s3_data['id'].tolist())
print(f"S3 ID数量: {len(s3_ids)}")

# 逐行处理S3数据
for _, s3_row in original_s3_data.iterrows():
    s3_id = s3_row['id']
    
    # 查找对应的RDS行
    rds_match = rds_pd_df[rds_pd_df['id'] == s3_id]
    
    if not rds_match.empty:
        rds_row = rds_match.iloc[0]
        
        # 创建新行，以S3数据为基础
        new_row = {}
        
        # 遍历所有可能的列
        for col in all_possible_columns:
            # 检查原始S3数据中是否包含此列（非NaN值）
            s3_has_column = col in s3_row.index and pd.notna(s3_row[col])
            
            if s3_has_column:
                # 如果S3有此列，使用S3的值
                new_row[col] = s3_row[col]
            elif col in rds_row.index:
                # 如果S3没有但RDS有，使用RDS的值
                new_row[col] = rds_row[col]
            else:
                # 两者都没有，设为NaN
                new_row[col] = None
        
        result_rows.append(new_row)
    else:
        # 没有匹配的RDS记录，只使用S3数据
        new_row = {col: s3_row[col] if col in s3_row.index else None for col in all_possible_columns}
        result_rows.append(new_row)

# 创建最终的DataFrame
merged_pd_df = pd.DataFrame(result_rows)

# 确保列的顺序：首先是所有原始S3列，然后是其他列
all_columns = list(original_s3_columns) + [col for col in all_possible_columns if col not in original_s3_columns]
merged_pd_df = merged_pd_df[all_columns]

# 6. 找出RDS中有但S3中没有的记录
unmatched_rds_records = rds_pd_df[~rds_pd_df['id'].isin(s3_ids)]
print(f"未匹配RDS记录数量: {len(unmatched_rds_records)}")

# 如果有未匹配的记录，直接通过Slack API发送消息
if not unmatched_rds_records.empty:
    try:
        # 创建包含未匹配记录信息的消息
        unmatched_ids = unmatched_rds_records['id'].tolist()
        
        # 如果ID数量太多，可能会超过Slack消息长度限制，所以只显示前10个ID
        displayed_ids = unmatched_ids[:10]
        remaining_count = len(unmatched_ids) - len(displayed_ids)
        
        # 组织消息文本
        message_text = f"*发现{len(unmatched_rds_records)}条RDS记录在S3中没有匹配项*\n"
        message_text += f"数据库: {db_name}, 表: {table_name}\n"
        message_text += f"未匹配ID示例: {', '.join(map(str, displayed_ids))}"
        
        if remaining_count > 0:
            message_text += f" 等{remaining_count}条未显示"
        
        # 直接发送到Slack
        requests.post(
           slack_webhook,
            json={"text": message_text}
        )
        
        print(f"已将{len(unmatched_rds_records)}条未匹配记录信息发送到Slack")
        
    except Exception as e:
        error_message = f"发送未匹配记录到Slack失败: {str(e)}"
        print(error_message)
        requests.post(slack_webhook, json={"text": f"警告: {error_message}"})
        # 继续执行，不终止任务
# 7. 输出到CSV
try:
    # 将pandas DataFrame写入CSV字符串
    csv_buffer = StringIO()
    merged_pd_df.to_csv(csv_buffer, index=False)
    
    # 上传到S3
    s3_client.put_object(
        Body=csv_buffer.getvalue(),
        Bucket=destination_bucket,
        Key=destination_file
    )
    
    success_message = f"ETL任务成功: 已将{merged_pd_df.shape[0]}行数据写入到 {destination_bucket}/{destination_file}"
    print(success_message)
    requests.post(slack_webhook, json={"text": success_message})
    
except Exception as e:
    error_message = f"写入CSV失败: {str(e)}"
    print(error_message)
    requests.post(slack_webhook, json={"text": f"ETL任务失败: {error_message}"})
    sys.exit(1)

# 完成作业
job.commit()