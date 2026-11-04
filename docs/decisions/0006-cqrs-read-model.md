# 0006: A separate, rebuildable read model

**Status:** accepted · 2026-10-28

## Context

Customers ask "where is my booking?" and "what are my trips?". The answer spans data owned by four places: the saga's execution, and the flight, hotel and payment services' tables. No service may read another's table.

## Options

* **Query Step Functions** (`DescribeExecution`, `GetExecutionHistory`) from the API. Works for one trip, but the history API is throttled, paginated and not queryable by customer, and it ties the API's shape to the state machine's internals.
* **Have the query API call each service.** Synchronous coupling to every service just to show a page, which is what this design avoids everywhere else.
* **CQRS: a projector builds a read model from domain events.** One table shaped for the queries, updated asynchronously, rebuildable from the event archive.

## Decision

CQRS. `trip-commands` and `trip-queries` are separate Lambdas behind separate routes. The projector consumes every trip event and maintains `trip-view`; its rule excludes `CircuitOpened` and `CircuitClosed`, which are about a vendor and have no trip to update:

| Attribute | |
|---|---|
| `trip_id` (PK) | |
| `customer_id`, `created_at` | GSI for "my trips", newest first |
| `status` | `REQUESTED`, `CONFIRMED`, `CANCELLED`, `NEEDS_ATTENTION` |
| `legs` | per leg: `status`, `reservation_id`, `booking_reference`, `amount` |
| `timeline` | one entry per event: type, time |
| `applied_message_ids` | for idempotency |
| `cancellation_reason` | |

## Consequences

* **Eventual consistency.** Right after `POST /trips`, `GET` may be a `404` for a moment; the API documents polling the `Location`.
* **Out-of-order events.** Each leg is updated independently, the timeline is sorted by `occurred_at` when read, and the trip status can only move to a terminal status, never back from one. A `FlightReserved` arriving after `FlightReleased` doesn't resurrect the reservation.
* **Duplicates and replays.** Each update is a conditional write that also adds the `message_id` to `applied_message_ids`; if the ID is already there, the update is skipped.
* **Rebuild.** `scripts/rebuild_read_model.py` empties `trip-view` and starts an EventBridge replay of the archive, limited to the projector's rule so the notification service never receives old events. Requirement F9 is the test: the same `GET` responses before and after.
* The read model holds no customer email and no payment token: the notification service takes `customer_id` from `TripConfirmed` / `TripCancelled` and looks up the email in its own `customer-contacts` table.
