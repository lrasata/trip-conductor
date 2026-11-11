# Vendor simulator

Stands in for the outside systems, and can be told to fail or slow down, one at a time. It's the airline, the hotel provider and the payment provider.

| | |
|---|---|
| **Listens to** | HTTP: `POST /{airline,hotel}/holds`, `DELETE /{airline,hotel}/holds/{reference}`, `POST /{airline,hotel}/holds/{reference}/confirm`, `POST /payment/charges` |
| **Publishes** | nothing |
| **Owns** | chaos settings (SSM parameter `/trip-conductor/<env>/chaos/<vendor>`) |

## What it does

It keeps no state. A hold's ID comes from the caller's `Idempotency-Key`, so asking twice gives the same hold, and a release always succeeds. Offers are in `airline.py`: `fl_lis_weekend` and `fl_lis_evening` have seats, and `fl_sold_out` doesn't. And in `hotel.py`: `ht_lis_centre` (up to 4 guests) and `ht_lis_riverside` (up to 2) have rooms, priced per night, and `ht_sold_out` doesn't.

The payment provider decides by test token: `tok_visa` and `tok_mastercard` are charged, `tok_declined` and `tok_insufficient_funds` are declined. Confirming a hold always succeeds.

`scripts/chaos.py` sets a vendor's failure rate (answers `503`) and added latency, optionally only for some operations (`hold`, `release`, `confirm`, `charge`). The simulator reads them at most every 5 s.

## Run the tests

```bash
pip install -r services/vendor_simulator/requirements-dev.txt
pytest services/vendor_simulator
```
