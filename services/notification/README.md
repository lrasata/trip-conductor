# Notification

Emails the customer when their trip ends. A reaction to the booking, not part of it: the saga doesn't know this service exists (publish-subscribe).

| | |
|---|---|
| **Listens to** | `TripConfirmed`, `TripCancelled` on the `trip-events` bus, through the `notification` rule and the `notification-events` queue |
| **Talks to** | SES |
| **Publishes** | nothing |
| **Owns** | `notifications` table (one item per trip), `customer-contacts` table (stands in for a customer service) |

## What it does

1. Records the trip's notification as `SENDING`, on condition that it wasn't recorded already. A trip ends once, so one email per trip.
2. Looks up the customer's email in `customer-contacts`. No address: records `NO_CONTACT` and stops, without retrying.
3. Sends the email with SES, then records `SENT` with the SES message ID.

A trip already `SENT` or `NO_CONTACT` is skipped, so a redelivered event never sends a second email. If the Lambda stops between sending and recording, the retry sends again: email is at least once, which is the safe side for "your trip is booked".

**Rebuilds don't reach it.** `scripts/rebuild_read_model.py` replays the archive to the projector's rule only, so old events never send old emails.

**The email** has the booking references and the amount, or the reason the trip was cancelled. It doesn't promise that the card wasn't charged: a charge that never got an answer may have gone through (see the payment service's Limits).

**Personal data.** The address comes from the service's own table, never from an event, and is never logged: errors are logged by type only, because an SES error can quote the address.

**SES sandbox.** A new SES account only sends to verified addresses. Terraform verifies `ALERT_EMAIL` as the sender, and the demo customer's contact is that same address (`scripts/add_contact.py cus_demo <ALERT_EMAIL>`).

## Code

| File | |
|---|---|
| `handler.py` | The AWS calls: SQS batch, DynamoDB, SES |
| `message.py` | The email's subject and text. No AWS calls |

## Run the tests

```bash
pip install -r services/notification/requirements-dev.txt
pytest services/notification
```
