import pytest

from app.domain.services.agents.react import ReActAgent


def test_extract_step_payload_from_early_complete_list():
    parsed = [
        ["EARLY_COMPLETE"],
        {
            "success": True,
            "result": "已完成全部热点收集并生成报告。",
            "attachments": ["/home/ubuntu/hot_topics_report.md"],
        },
    ]

    payload = ReActAgent._extract_step_payload(parsed)

    assert payload["success"] is True
    assert payload["attachments"] == ["/home/ubuntu/hot_topics_report.md"]


def test_extract_step_payload_raises_for_invalid_shape():
    with pytest.raises(ValueError):
        ReActAgent._extract_step_payload([["EARLY_COMPLETE"]])
