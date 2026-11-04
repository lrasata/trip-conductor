# Commands and events

Two kinds of messages, kept deliberately apart:

* **Commands** ask one service to do something. The saga sends them over that service's SQS queue, with a Step Functions task token. They are named in the imperative (`ReserveFlight`) and have exactly one receiver. The service answers by calling `SendTaskSuccess` or `SendTaskFailure` with the token.
* **Events** state a fact that already happened. They are published to the `trip-events` EventBridge bus, named in the past tense (`FlightReserved`), and have any number of receivers, including none. Every event is archived.

Both use the [envelope](envelope.schema.json). `correlation_id` is always the trip ID.

## Commands

| Command | Receiver | `data` | Success output | Failure errors |
|---|---|---|---|---|
| `ReserveFlight` | flight | `offer_id`, `passengers` | `reservation_id`, `hold_expires_at`, `amount`, `currency` | `FlightUnavailable`, `VendorUnavailable` |
| `ConfirmFlight` | flight | `reservation_id` | `booking_reference` | `VendorUnavailable` |
| `ReleaseFlight` | flight | `reservation_id` | `{}` | `VendorUnavailable` |
| `ReserveHotel` | hotel | `offer_id`, `check_in`, `check_out`, `guests` | `reservation_id`, `hold_expires_at`, `amount`, `currency` | `HotelUnavailable`, `VendorUnavailable` |
| `ConfirmHotel` | hotel | `reservation_id` | `booking_reference` | `VendorUnavailable` |
| `ReleaseHotel` | hotel | `reservation_id` | `{}` | `VendorUnavailable` |
| `ChargePayment` | payment | `payment_token`, `amount`, `currency` | `payment_id` | `PaymentDeclined`, `VendorUnavailable` |

The command envelope adds one field, `task_token`, outside `data`. It is never logged.

`VendorUnavailable` covers both "the vendor failed" and "the circuit breaker is open". The saga retries it with backoff; the other errors are business answers and go straight to compensation.

Releasing an already released or already expired hold succeeds: compensations must be idempotent.

## Events

| Event | Producer | `data` |
|---|---|---|
| `TripRequested` | saga | `customer_id`, `flight_offer_id`, `hotel_offer_id`, `requested_at` |
| `FlightReserved` | flight | `reservation_id`, `hold_expires_at`, `amount`, `currency` |
| `FlightConfirmed` | flight | `reservation_id`, `booking_reference` |
| `FlightReleased` | flight | `reservation_id`, `reason` |
| `HotelReserved` | hotel | `reservation_id`, `hold_expires_at`, `amount`, `currency` |
| `HotelConfirmed` | hotel | `reservation_id`, `booking_reference` |
| `HotelReleased` | hotel | `reservation_id`, `reason` |
| `PaymentCharged` | payment | `payment_id`, `amount`, `currency` |
| `PaymentDeclined` | payment | `decline_code` |
| `TripConfirmed` | saga | `customer_id`, `flight_booking_reference`, `hotel_booking_reference`, `total_amount`, `currency` |
| `TripCancelled` | saga | `customer_id`, `reason`, `failed_step` |
| `TripNeedsAttention` | saga | `failed_step`, `error` |
| `CircuitOpened` | any service | `vendor`, `failure_count`, `opened_at` |
| `CircuitClosed` | any service | `vendor`, `closed_at` |

`CircuitOpened` and `CircuitClosed` are about a vendor, not a trip: their `correlation_id` is `vendor:<name>`. The projector's rule excludes them, so they never reach `trip-view`.

On EventBridge the envelope is the event's `detail`, `detail-type` is the `message_type`, and `source` is `trip-conductor.<producer>`. Rules match on `detail-type`.

## Versioning

Same rules as Apply Relay: within a version, only add optional fields, and consumers tolerate their absence. Anything else is a new `message_version`, and consumers reject versions they don't know. The projector is the strictest consumer because it also reads *old* events during a replay.

Per-event JSON Schemas are added next to this file as each service is built, and contract tests validate published messages against them.
