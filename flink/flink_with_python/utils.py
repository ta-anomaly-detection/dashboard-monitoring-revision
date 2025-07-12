from datetime import datetime
import logging

def clean_ts(ts_str):
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', ''))  # strip Z
        return dt.strftime('%Y-%m-%d %H:%M:%S')  # ClickHouse suka format ini
    except Exception as e:
        logging.error(f"Failed to clean ts: {ts_str} | Error: {e}")
        return None
