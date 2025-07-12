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

# TODO: Need to install packages via poetry
# from logprocessor import LogPreprocessor

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
    encode_method,
    encode_protocol,
    encode_status,
    extract_device,
    map_device,
    parse_latency,
    split_request,
    split_url,
    encode_country
)

from utils import clean_ts

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)


def call_api(row):
    try:
        response = requests.post(
            "http://web:8000/api/prediction/predict",  # real URL
            json={
                "size": row[0],
                "country": row[1],
                "method": row[2],
                "device": row[3],
                "status": row[4]
            },
            timeout=5
        )
        return f"✅ {response.status_code}: {response.text}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


class ParseJson(MapFunction):
    def map(self, value: str) -> Row:
        processing_start_time = time.time()
        try:
            outer_data = json.loads(value)

            if "log" in outer_data:
                data = json.loads(outer_data["log"])
            else:
                data = outer_data

            return Row(
                data.get("ip"),
                clean_ts(data.get("time")),
                data.get("method"),
                data.get("responseTime"),
                data.get("url"),
                data.get("param"),
                data.get("protocol"),
                int(data.get("responseCode", 0)),
                int(data.get("responseByte", 0)),
                data.get("userAgent"),
                processing_start_time  # Add processing start time
            )
        except Exception as e:
            logging.error(f"Failed to parse JSON: {e} | Raw message: {value}")
            logging.error(f"Message preview: {value[:200]}...")
            return Row(*[None] * 10, processing_start_time)  # Updated to include timing


row_type = Types.ROW_NAMED(
    field_names=["ip", "time", "method", "responseTime", "url",
                 "param", "protocol", "responseCode", "responseByte", "userAgent", "processing_start_time"],
    field_types=[Types.STRING(), Types.STRING(), Types.STRING(), Types.STRING(), Types.STRING(),
                 Types.STRING(), Types.STRING(), Types.INT(), Types.INT(), Types.STRING(), Types.FLOAT()]
)

pca_output_type = Types.ROW_NAMED(
    ["label_1", "label_2", "label_3"],
    [Types.FLOAT(), Types.FLOAT(), Types.FLOAT()]
)


class CombineProcessedWithRaw(MapFunction):
    def map(self, row):
        try:
            logging.info(f"Processing row: {row}")

            # Now the indices match exactly with the processed_table order
            return Row(
                row[0] or "",                     # ts
                row[1] or "",                     # remote_ip
                float(row[2]) if row[2] is not None else 0.0,  # latency_us
                row[3] or "",                     # host
                row[4] or "",                     # http_method
                # encoded_http_method
                int(row[5]) if row[5] is not None else 0,
                row[6] or "",                     # request_uri
                row[7][0] if row[7] and len(row[7]) > 0 else "",  # url_path
                row[7][1] if row[7] and len(row[7]) > 1 else "",  # url_query
                row[8] or "",                     # http_version
                # encoded_http_version
                int(row[9]) if row[9] is not None else 0,
                # response_status
                int(row[10]) if row[10] is not None else 0,
                int(row[11]) if row[11] is not None else 0,    # encoded_status
                int(row[12]) if row[12] is not None else 0,    # response_size
                row[13] or "",                    # user_agent
                row[14] or "",                    # device_family
                int(row[15]) if row[15] is not None else 0,    # encoded_device
                row[16] or "",                    # country
                # encoded_country
                int(row[17]) if row[17] is not None else 0,
                row[18] or "",                    # referrer
                row[19] or "",                    # request_id
                row[20] or "",                    # msg
                row[21] or ""                     # level
            )
        except Exception as e:
            logging.error(f"Failed to combine data: {e}")
            logging.error(f"Row content: {row}")
            logging.error(
                f"Row length: {len(row) if hasattr(row, '__len__') else 'N/A'}")
            # Return a valid Row with default values
            return Row(
                "", "", 0.0, "", "", 0, "", "", "", "",
                0, 0, 0, 0, "", "", 0, "", 0, "", "", "", ""
            )


class AddProcessingTime(MapFunction):
    def map(self, row):
        try:
            processing_end_time = time.time()
            processing_start_time = row[10] if row[10] is not None else processing_end_time
            processing_time_ms = (processing_end_time - processing_start_time) * 1000  # Convert to milliseconds
            
            # Create new row with processing time, excluding the start time
            return Row(
                row[0] or "",                     # ip
                row[1] or "",                     # time  
                row[2] or "",                     # method
                float(row[3]) if row[3] is not None else 0.0,  # response_time
                row[4] or "",                     # url
                row[5] or "",                     # param
                row[6] or "",                     # protocol
                int(row[7]) if row[7] is not None else 0,      # response_code
                int(row[8]) if row[8] is not None else 0,      # response_byte
                row[9] or "",                     # user_agent
                processing_time_ms                # processing_time_ms
            )
        except Exception as e:
            logging.error(f"Failed to add processing time: {e}")
            logging.error(f"Row content: {row}")
            # Return a valid Row with default values
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

    parsed_stream = ds.map(ParseJson(), output_type=row_type)

    # TODO: Uncomment to implement LogPreprocessor for ML
    # parsed_stream.map(LogPreprocessor(), output_type=pca_output_type)

    table = t_env.from_data_stream(parsed_stream).alias(
        "ip",
        "time",
        "method",
        "responseTime",
        "url",
        "param",
        "protocol",
        "responseCode",
        "responseByte",
        "userAgent",
        "processing_start_time"
    )

    processed_table = table.select(
        col("ip"),
        col("time"),
        col("method"),
        parse_latency(col("responseTime")).alias("response_time"),
        col("url"),
        col("param"),
        col("protocol"),
        col("responseCode"),
        col("responseByte"),
        col("userAgent"),
        col("processing_start_time")
    )

    result_stream = t_env.to_data_stream(processed_table)
    
    # Add processing time calculation
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
    
    # debug
    timed_stream.print("Processed Stream with Timing")

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

    # combined_stream = result_stream.map(
    #     CombineProcessedWithRaw(),
    #     output_type=clickhouse_row_type
    # )

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
