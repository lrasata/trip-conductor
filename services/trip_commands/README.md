# trip-commands

`POST /trips`: the command side of CQRS. It checks a booking request and starts the booking saga. It owns no data: the saga's execution and the services' tables are the write model.

| | |
|---|---|
| **Triggered by** | `POST /trips` on the `trips` REST API (API key required) |
| **Talks to** | Step Functions (`StartExecution` on the booking saga) |
| **Publishes** | nothing: the saga publishes `TripRequested` |
| **Owns** | nothing |

## What it does

```http
POST /v1/trips
x-api-key: <key>
Idempotency-Key: 6f1c0a52-booking-1
Content-Type: application/json

{ "customer_id": "cus_demo", "flight": {...}, "hotel": {...}, "payment_token": "tok_visa" }
```

The body is the one in [`scripts/requests/`](../../scripts/requests/). The answer is `202 Accepted`:

```json
{ "trip_id": "trp_5d0b...", "location": "/v1/trips/trp_5d0b..." }
```

with the same path in the `Location` header. Follow the booking there once the read side is deployed: for a moment it may answer `404`, because the read model is updated asynchronously.

**Idempotency.** The trip ID is `trp_` + a hash of the customer ID and the `Idempotency-Key`, and it is the saga's execution name. Step Functions refuses a second execution with the same name, so a retried request returns the same trip and never books twice. A repeat with a different body also gets the first trip back: a booking can't be changed by resending it.

**Validation.** Every error is reported at once (`400`, `validation_failed`, with `details`). Only the known fields are copied into the saga's input; anything else in the body is dropped.

The payment token is passed to the saga and nowhere else: never logged, never answered.

## Code

| File | |
|---|---|
| `handler.py` | The AWS call: `StartExecution`, and the HTTP answer |
| `request.py` | What a valid request looks like and the trip ID it gets. No AWS calls |

## Run the tests

```bash
pip install -r services/trip_commands/requirements-dev.txt
pytest services/trip_commands
```
