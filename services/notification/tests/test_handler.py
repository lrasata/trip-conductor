import logging

from conftest import CUSTOMER_EMAIL, SENDER, sqs_event, trip_event

from notification_service import message
from notification_service.handler import handler


def run(*envelopes):
    return handler(sqs_event(*envelopes), None)


def stored(aws, trip_id="trp_123"):
    return aws["notifications"].get_item(Key={"trip_id": trip_id}).get("Item")


def sent_email(aws):
    return aws["ses"].send_email.call_args.kwargs


def test_a_confirmed_trip_is_emailed(aws):
    assert run(trip_event()) == {"batchItemFailures": []}

    email = sent_email(aws)
    assert email["Source"] == SENDER
    assert email["Destination"] == {"ToAddresses": [CUSTOMER_EMAIL]}
    assert email["Message"]["Subject"]["Data"] == "Your trip is booked"
    body = email["Message"]["Body"]["Text"]["Data"]
    assert "FL-ABC" in body and "HT-XYZ" in body and "618.00 EUR" in body
    assert stored(aws)["status"] == "SENT"
    assert stored(aws)["ses_message_id"] == "ses-1"


def test_a_cancelled_trip_is_emailed_with_its_reason(aws):
    run(trip_event("TripCancelled"))

    body = sent_email(aws)["Message"]["Body"]["Text"]["Data"]
    assert sent_email(aws)["Message"]["Subject"]["Data"] == "Your trip could not be booked"
    assert "no room left" in body


def test_a_redelivered_event_sends_one_email(aws):
    envelope = trip_event()

    run(envelope)
    run(envelope)
    run(trip_event())  # even another event about the same trip: a trip ends once

    assert aws["ses"].send_email.call_count == 1


def test_a_failed_send_is_retried_and_then_sent(aws):
    aws["ses"].send_email.side_effect = [RuntimeError("ses down"), {"MessageId": "ses-2"}]
    envelope = trip_event()

    assert run(envelope) == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert stored(aws)["status"] == "SENDING"

    assert run(envelope) == {"batchItemFailures": []}
    assert stored(aws)["status"] == "SENT"
    assert aws["ses"].send_email.call_count == 2


def test_no_contact_means_no_email_and_no_retry(aws):
    result = run(trip_event(customer_id="cus_unknown"))

    assert result == {"batchItemFailures": []}
    aws["ses"].send_email.assert_not_called()
    assert stored(aws)["status"] == "NO_CONTACT"


def test_other_events_and_versions_fail_only_that_message(aws):
    result = run(trip_event("TripNeedsAttention"), trip_event(version=2, trip_id="trp_2"), trip_event(trip_id="trp_3"))

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-0"}, {"itemIdentifier": "msg-1"}]}
    assert aws["ses"].send_email.call_count == 1


def test_the_customer_email_is_never_logged(aws, caplog):
    caplog.set_level(logging.INFO)
    aws["ses"].send_email.side_effect = [RuntimeError(f"rejected {CUSTOMER_EMAIL}"), {"MessageId": "ses-2"}, {"MessageId": "ses-3"}]

    run(trip_event())
    run(trip_event())
    run(trip_event("TripCancelled", trip_id="trp_2"))

    ours = [record.getMessage() for record in caplog.records if record.name == "root"]
    assert len(ours) == 3
    assert not any(CUSTOMER_EMAIL in line for line in ours)


def test_money_is_written_from_cents():
    assert message._money(61800, "EUR") == "618.00 EUR"
    assert message._money(5, "EUR") == "0.05 EUR"


def test_an_unknown_reason_still_reads_well():
    email = message.cancelled("trp_1", {"reason": "SOMETHING_NEW"})

    assert "something went wrong" in email.text
