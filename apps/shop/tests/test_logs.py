import json
import logging

from shop.logs import JsonFormatter, request_id_var


def test_json_log_line_has_service_and_request_id() -> None:
    token = request_id_var.set("req-1")
    try:
        record = logging.LogRecord("shop.x", logging.INFO, __file__, 1, "hello", None, None)
        record.fields = {"status": 200}
        line = json.loads(JsonFormatter("catalog").format(record))
    finally:
        request_id_var.reset(token)
    assert line["service"] == "catalog"
    assert line["request_id"] == "req-1"
    assert line["status"] == 200
    assert line["msg"] == "hello"
