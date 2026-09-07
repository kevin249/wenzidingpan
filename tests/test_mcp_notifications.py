from types import SimpleNamespace

from stockwidget.mcp_notifications import McpNotification, _notifications, _tool_payload


def test_tool_payload_prefers_structured_content():
    result = SimpleNamespace(
        is_error=False,
        structured_content={"resource_uri": "gupiao://notifications/alice"},
        content=[],
    )
    assert _tool_payload(result) == {"resource_uri": "gupiao://notifications/alice"}


def test_tool_payload_falls_back_to_json_text():
    block = SimpleNamespace(text='{"resource_uri": "gupiao://notifications/alice"}')
    result = SimpleNamespace(isError=False, structuredContent=None, content=[block])
    assert _tool_payload(result)["resource_uri"].endswith("/alice")


def test_notifications_ignore_invalid_rows_and_normalize_text():
    payload = {
        "notifications": [
            {"event_id": "event-12", "title": " 提醒 ", "body": " 正文 ", "event_type": "market.alert"},
            {"event_id": "", "title": "忽略"},
        ]
    }

    assert _notifications(payload) == [
        McpNotification(event_id="event-12", title="提醒", body="正文", event_type="market.alert")
    ]
