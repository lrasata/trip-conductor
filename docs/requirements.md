# Requirements

## Goal

A customer books a trip (one flight + one hotel, paid in one charge). Either the whole trip is booked, or nothing is held and nothing is charged. The customer can see where their booking stands at any time.

## Functional

| ID | Requirement |
|---|---|
| F1 | `POST /trips` accepts a customer ID, a flight (offer ID, passengers), a hotel (offer ID, check-in, check-out, guests), a payment token and an `Idempotency-Key` header. It answers `202` with the trip ID and a `Location` header. |
| F2 | The same customer + `Idempotency-Key` always returns the same trip and never starts a second booking. |
| F3 | The booking reserves the flight, then the hotel, then charges the payment, then confirms both reservations. |
| F4 | If a step before the payment fails, every reservation already made is released, and the trip ends `CANCELLED` with a reason (`FLIGHT_UNAVAILABLE`, `HOTEL_UNAVAILABLE`, `PAYMENT_DECLINED`, `VENDOR_UNAVAILABLE`, `TIMEOUT`). |
| F5 | Once the payment succeeds, the booking only moves forward: confirmations are retried. If they still fail, the trip ends `NEEDS_ATTENTION` and an operator is alerted. No automatic refund. |
| F6 | `GET /trips/{trip_id}` returns the trip's status, the status of each leg and a timeline of what happened. |
| F7 | `GET /trips?customer_id=` lists a customer's trips, newest first. |
| F8 | The customer gets an email when the trip is confirmed or cancelled. |
| F9 | The read model can be deleted and rebuilt from the event archive, with the same result. |

## Non-functional

| ID | Requirement |
|---|---|
| N1 | A vendor that is down must not be called by every booking: after 5 failures within 60 s, calls fail fast for 30 s, then one probe call decides whether to resume. |
| N2 | No booking hangs: every saga step has a timeout. A full booking either finishes or is cancelled within 15 minutes, the lifetime of a hold. |
| N3 | Every message can be delivered twice without double reservations, double charges or double emails. |
| N4 | A failing message is retried 3 times, then parked in a DLQ with an alarm. |
| N5 | One trip ID traces the whole booking: execution name, `correlation_id` in every event and log line, X-Ray trace. |
| N6 | No card data and no customer emails in logs or events. The payment token is the only payment value that leaves the API, and it's passed only to the payment service. |
| N7 | Serverless only: close to zero cost when idle. |
| N8 | Everything is Terraform, deployed by GitHub Actions with OIDC. |

## Demo scenarios

Each scenario is reproducible with `scripts/chaos.py` and a request in `scripts/requests/`.

| # | Scenario | Expected |
|---|---|---|
| D1 | Happy path | `CONFIRMED`, email sent, timeline shows 7 events |
| D2 | Hotel offer sold out | Flight released, `CANCELLED / HOTEL_UNAVAILABLE` |
| D3 | Card declined (`tok_declined`) | Hotel then flight released, `CANCELLED / PAYMENT_DECLINED` |
| D4 | Airline down (`chaos.py airline --failure-rate 1`) | Breaker opens after 5 failures, later bookings fail fast, `CircuitOpened` alarm. After `--failure-rate 0`, a probe closes the breaker. |
| D5 | Airline slow (`--latency-ms 8000`) | Calls time out, count as failures, same as D4 |
| D6 | Hotel confirmation fails after payment (`chaos.py hotel --failure-rate 1 --operations confirm`) | Retried; after the budget, `NEEDS_ATTENTION` + alarm |
| D7 | Same `POST` sent twice | One execution, same trip ID returned twice |
| D8 | Rebuild the read model | Same `GET` responses before and after |

## Out of scope

Real vendor integrations, real payments, authentication of customers (the API uses an API key), search and pricing of offers (offer IDs come from the vendor simulator's catalog), partial bookings (flight only), changes to a confirmed trip.
