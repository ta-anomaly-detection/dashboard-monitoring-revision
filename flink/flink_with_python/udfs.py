import re
import json
from datetime import datetime
import time
from pyflink.table.udf import udf

@udf(result_type='MAP<STRING, STRING>')
def parse_log(json_line: str):
    try:
        start_time = time.time() * 1000
        
        # First, parse the outer JSON
        outer_data = json.loads(json_line)
        
        # Extract the "log" field which contains another JSON string
        log_json_str = outer_data.get("log", "")
        
        if log_json_str:
            # Parse the inner JSON to get the "msg" field
            log_data = json.loads(log_json_str)
            log_line = log_data.get("msg", "")
        else:
            # Fallback: treat the input as direct log line
            log_line = json_line
        
        # Parse the log line using regex
        pattern = re.compile(
            r'(?P<ip>\S+) - - \[(?P<datetime>[^\]]+)\] time:(?P<response_time>[\d.]+) s "(?P<method>[A-Z]+) (?P<url>\S+) (?P<protocol>[^"]+)" (?P<status>\d{3}) (?P<size>\d+) "[^"]*" "(?P<user_agent>[^"]+)"'
        )
        match = pattern.match(log_line)
        if match:
            gd = match.groupdict()
            dt = datetime.strptime(gd['datetime'], '%d/%b/%Y:%H:%M:%S %z')
            url = gd['url']

            if '?' in url:
                path, param = url.split('?', 1)
            else:
                path, param = url, '-'

            return {
                'ip': gd['ip'],
                'time': dt.strftime('%Y-%m-%d %H:%M:%S'),
                'method': gd['method'],
                'url': path,
                'param': param,
                'protocol': gd['protocol'],
                'responseTime': gd['response_time'],
                'responseCode': gd['status'],
                'responseByte': gd['size'],
                'user-agent': gd['user_agent'],
                'start_time': str(start_time),
            }
        else:
            return {}
    except Exception as e:
        # If parsing fails, return empty map
        return {}

def register_udfs(t_env):
    """Register all UDFs with the given table environment."""
    t_env.create_temporary_system_function("parse_log", parse_log)


