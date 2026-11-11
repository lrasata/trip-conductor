# Vendor simulator

Stands in for the outside systems, and can be told to fail or slow down, one at a time. For now it's the airline and the hotel provider.

| | |
|---|---|
| **Listens to** | HTTP: `POST /airline/holds`, `DELETE /airline/holds/{reference}`, `POST /hotel/holds`, `DELETE /hotel/holds/{reference}` |
| **Publishes** | nothing |
| **Owns** | chaos settings (SSM parameter `/trip-conductor/<env>/chaos/<vendor>`) |

## What it does

It keeps no state. A hold's ID comes from the caller's `Idempotency-Key`, so asking twice gives the same hold, and a release always succeeds. Offers are in `airline.py`: `fl_lis_weekend` and `fl_lis_evening` have seats, and `fl_sold_out` doesn't. And in `hotel.py`: `ht_lis_centre` (up to 4 guests) and `ht_lis_riverside` (up to 2) have rooms, priced per night, and `ht_sold_out` doesn't.

`scripts/chaos.py` sets a vendor's failure rate (answers `503`) and added latency. The simulator reads them at most every 5 s.

## Run the tests

```bash
pip install -r services/vendor_simulator/requirements-dev.txt
pytest services/vendor_simulator
```
