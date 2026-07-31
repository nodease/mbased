import logging

from apps.shared.services.mail_observability import MailProcessingObservability


def setup_function():
    MailProcessingObservability.reset_local_counters()


def test_mail_metrics_use_only_bounded_event_and_outcome_labels(caplog):
    caplog.set_level(logging.INFO)
    MailProcessingObservability.record("draft", "succeeded")
    MailProcessingObservability.record("raw-message-id", "raw-provider-detail")

    assert MailProcessingObservability.get_local_counter("draft", "succeeded") == 1
    assert MailProcessingObservability.get_local_counter("unknown", "unknown") == 1
    assert "mail.processing_transition" in caplog.text
    assert "raw-message-id" not in caplog.text
    assert "raw-provider-detail" not in caplog.text
