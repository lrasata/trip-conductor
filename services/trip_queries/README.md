# trip-queries

The query side of CQRS: `GET /trips/{trip_id}` and `GET /trips?customer_id=`. It reads `trip-view` and nothing else, so reads never load the booking flow.

| | |
|---|---|
| **Triggered by** | `GET /trips/{trip_id}` and `GET /trips` on the `trips` REST API (API key required) |
| **Talks to** | nothing |
| **Publishes** | nothing |
| **Owns** | nothing: it reads the projector's `trip-view` |

## What it does

`GET /v1/trips/{trip_id}` returns the trip's status, each leg and the timeline:

```json
{
  "trip_id": "trp_5d0b...", "customer_id": "cus_demo", "status": "CONFIRMED",
  "created_at": "2026-11-18T10:00:00.000Z", "updated_at": "2026-11-18T10:00:09.412Z",
  "total_amount": 61800, "currency": "EUR",
  "legs": {
    "flight":  { "status": "CONFIRMED", "reservation_id": "...", "booking_reference": "...", "amount": 25800, "currency": "EUR" },
    "hotel":   { "status": "CONFIRMED", ... },
    "payment": { "status": "CHARGED", "payment_id": "...", "amount": 61800, "currency": "EUR" }
  },
  "timeline": [ { "type": "TripRequested", "at": "..." }, ... ]
}
```

A cancelled trip has `"cancellation": { "reason": "HOTEL_UNAVAILABLE", "failed_step": "ReserveHotel" }`. Amounts are in cents.

`GET /v1/trips?customer_id=cus_demo` lists that customer's trips, newest first, 20 at a time (`limit` up to 50). When there are more, `next` holds a token: pass it back as `?next=` for the next page.

A trip booked a moment ago can be a `404` for a second or two: the view is updated asynchronously. Poll the `Location` that `POST /trips` returned. Answers carry `Cache-Control: no-store`.

The projector's bookkeeping (`applied_message_ids`, `version`) and why a trip needs attention (for operators) are not shown.

## Code

| File | |
|---|---|
| `handler.py` | The AWS calls: `GetItem`, `Query` on `by-customer`, and the HTTP answers |
| `views.py` | What is shown of a view, and the paging token. No AWS calls |

## Run the tests

```bash
pip install -r services/trip_queries/requirements-dev.txt
pytest services/trip_queries
```
