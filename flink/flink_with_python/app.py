import requests
import json
import logging
import time
from datetime import datetime

from pyflink.common import WatermarkStrategy, Row
from pyflink.common.serialization import SimpleStringSchema
from pyflink.common.typeinfo import Types
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.datastream.connectors.kafka import KafkaOffsetsInitializer, KafkaSource
from pyflink.datastream.connectors.jdbc import JdbcSink, JdbcConnectionOptions, JdbcExecutionOptions
from pyflink.datastream.functions import MapFunction
from pyflink.table import StreamTableEnvironment
from pyflink.table.expressions import col

from config import (
    KAFKA_BROKER,
    KAFKA_TOPIC,
    CLICKHOUSE_HOST,
    CLICKHOUSE_PORT,
    CLICKHOUSE_DB,
    CLICKHOUSE_TABLE,
    CLICKHOUSE_USER,
    CLICKHOUSE_PASSWORD,
    print_configuration
)
from udfs import (
    register_udfs,
    parse_log,
)

from utils import clean_ts

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)

row_type = Types.ROW_NAMED(
    field_names=["ip", "time", "method", "responseTime", "url",
                 "param", "protocol", "responseCode", "responseByte", "userAgent", "processing_start_time"],
    field_types=[Types.STRING(), Types.STRING(), Types.STRING(), Types.STRING(), Types.STRING(),
                 Types.STRING(), Types.STRING(), Types.INT(), Types.INT(), Types.STRING(), Types.FLOAT()]
)

class AddProcessingTime(MapFunction):
    def map(self, row):
        try:
            start_time_ms = float(row['start_time']) if row['start_time'] else 0.0
            processing_time_ms = time.time() * 1000 - start_time_ms

            return Row(
                row['ip'] or "",
                clean_ts(row['time']) or "",
                row['method'] or "",
                float(row['responseTime']) if row['responseTime'] else 0.0,
                row['url'] or "",
                row['param'] or "",
                row['protocol'] or "",
                int(row['responseCode']) if row['responseCode'] else 0,
                int(row['responseByte']) if row['responseByte'] else 0,
                row['userAgent'] or "",
                processing_time_ms
            )
        except Exception as e:
            logging.error(f"Failed to add processing time: {e}")
            logging.error(f"Row content: {row}")
            return Row("", "", "", 0.0, "", "", "", 0, 0, "", 0.0)


def kafka_sink_example():
    env = StreamExecutionEnvironment.get_execution_environment()

    env.add_jars("file:///jars/flink-sql-connector-kafka-3.0.1-1.18.jar")
    env.add_jars("file:///jars/flink-connector-jdbc-3.1.2-1.17.jar")
    env.add_jars("file:///jars/clickhouse-jdbc-0.4.6-all.jar")
    # env.add_jars("file:///jars/flink-metrics-prometheus-1.18.1.jar")

    print_configuration()

    t_env = StreamTableEnvironment.create(env)

    register_udfs(t_env)

    kafka_source = KafkaSource.builder() \
        .set_bootstrap_servers(KAFKA_BROKER) \
        .set_topics(KAFKA_TOPIC) \
        .set_group_id("flink_group") \
        .set_starting_offsets(KafkaOffsetsInitializer.earliest()) \
        .set_value_only_deserializer(SimpleStringSchema()) \
        .build()

    print("Kafka source created successfully")

    ds = env.from_source(
        kafka_source, WatermarkStrategy.no_watermarks(), "Kafka Source")

    log_table = t_env.from_data_stream(ds, col("line"))
    
    parsed_table = log_table.select(
        parse_log(col("line")).alias("log_map")
    ).select(
        col("log_map")['ip'].alias("ip"),
        col("log_map")['time'].alias("time"),
        col("log_map")['method'].alias("method"),
        col("log_map")['responseTime'].alias("responseTime"),
        col("log_map")['url'].alias("url"),
        col("log_map")['param'].alias("param"),
        col("log_map")['protocol'].alias("protocol"),
        col("log_map")['responseCode'].alias("responseCode"),
        col("log_map")['responseByte'].alias("responseByte"),
        col("log_map")['user-agent'].alias("userAgent"),
        col("log_map")['start_time'].alias("start_time") 
    )
    
    result_stream = t_env.to_data_stream(parsed_table)
    
    # log parsed table result
    result_stream.print("Parsed Log Stream")

    timed_stream = result_stream.map(AddProcessingTime(), output_type=Types.ROW_NAMED(
        field_names=[
            "ip", "time", "method", "response_time", "url",
            "param", "protocol", "response_code", "response_byte", "user_agent", "processing_time_ms"
        ],
        field_types=[
            Types.STRING(), Types.STRING(), Types.STRING(), Types.FLOAT(),
            Types.STRING(), Types.STRING(), Types.STRING(),
            Types.INT(), Types.INT(), Types.STRING(), Types.FLOAT()
        ]
    ))

    clickhouse_row_type = Types.ROW_NAMED(
        field_names=[
            "ip", "time", "method", "response_time", "url",
            "param", "protocol", "response_code", "response_byte", "user_agent", "processing_time_ms"
        ],
        field_types=[
            Types.STRING(), Types.STRING(), Types.STRING(), Types.FLOAT(),
            Types.STRING(), Types.STRING(), Types.STRING(),
            Types.INT(), Types.INT(), Types.STRING(), Types.FLOAT()
        ]
    )

    sql = f"""
    INSERT INTO {CLICKHOUSE_DB}.{CLICKHOUSE_TABLE} (
        ip, time, method, response_time, url, param, protocol,
        response_code, response_byte, user_agent, processing_time_ms
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    connection_options = JdbcConnectionOptions.JdbcConnectionOptionsBuilder()\
        .with_url(f"jdbc:clickhouse://{CLICKHOUSE_HOST}:{CLICKHOUSE_PORT}/{CLICKHOUSE_DB}")\
        .with_driver_name("com.clickhouse.jdbc.ClickHouseDriver")\
        .with_user_name(CLICKHOUSE_USER)\
        .with_password(CLICKHOUSE_PASSWORD)\
        .build()

    execution_options = JdbcExecutionOptions.Builder() \
        .with_batch_interval_ms(1000) \
        .with_batch_size(1000) \
        .with_max_retries(3) \
        .build()

    jdbc_sink = JdbcSink.sink(
        sql,
        type_info=clickhouse_row_type,
        jdbc_execution_options=execution_options,
        jdbc_connection_options=connection_options
    )

    timed_stream.add_sink(jdbc_sink)

    timed_stream.print("Data for ClickHouse with Processing Time")

    env.execute("Kafka to ClickHouse JDBC Job")


if __name__ == "__main__":
    kafka_sink_example()
