from apps.shared.services.tracing.mail_payload import sanitize_mail_trace_payload


def _mail_result():
    return {
        "emails": [
            {
                "subject": "confidential subject",
                "body_text": "confidential body",
                "processing_ref": "opaque-processing-ref",
            }
        ],
        "total_count": 1,
        "folder": "INBOX",
        "processing_ref": "opaque-processing-ref",
    }


def test_mail_node_output_is_reduced_to_non_content_summary():
    sanitized = sanitize_mail_trace_payload(
        node_type="mailNode",
        payload_kind="output",
        value=_mail_result(),
    )

    assert sanitized == {
        "mail_content_redacted": True,
        "total_count": 1,
        "folder": "INBOX",
    }
    assert "confidential" not in str(sanitized)
    assert "opaque-processing-ref" not in str(sanitized)


def test_embedded_mail_result_is_redacted_from_downstream_node_input():
    sanitized = sanitize_mail_trace_payload(
        node_type="llmNode",
        payload_kind="input",
        value={"mail-source": _mail_result(), "question": "safe prompt"},
    )

    assert sanitized["question"] == "safe prompt"
    assert sanitized["mail-source"]["mail_content_redacted"] is True
    assert "confidential" not in str(sanitized)


def test_gmail_effect_input_never_persists_reply_body_or_upstream_content():
    sanitized = sanitize_mail_trace_payload(
        node_type="gmailDraftNode",
        payload_kind="input",
        value={
            "mail-source": _mail_result(),
            "llm": {"result": "confidential generated reply"},
        },
    )

    assert sanitized == {"mail_content_redacted": True, "input_node_count": 2}


def test_unrelated_payload_without_mail_shape_is_preserved():
    payload = {"result": "ordinary output"}
    assert (
        sanitize_mail_trace_payload(
            node_type="templateNode",
            payload_kind="output",
            value=payload,
        )
        == payload
    )


def test_mail_sensitive_lineage_redacts_transformed_plain_string_output():
    sanitized = sanitize_mail_trace_payload(
        node_type="templateNode",
        payload_kind="output",
        value={"text": "transformed confidential mail body"},
        mail_sensitive_lineage=True,
    )

    assert sanitized == {
        "mail_content_redacted": True,
        "payload_field_count": 1,
    }
    assert "confidential" not in str(sanitized)
