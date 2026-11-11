# Hotel service

Holds and releases rooms with the hotel provider, on the saga's command.

| | |
|---|---|
| **Listens to** | `ReserveHotel`, `ConfirmHotel`, `ReleaseHotel` on the `hotel-commands` queue |
| **Publishes** | `HotelReserved`, `HotelConfirmed`, `HotelReleased`, `CircuitOpened`, `CircuitClosed` |
| **Owns** | `hotel-reservations` table, the `hotel` item in `circuit-breakers` |

## What it does

The same as the [flight service](../flight/README.md), for rooms: each command is answered with its task token, a redelivered command is answered from the stored reservation, and only final results are stored.

`ReleaseHotel` works from the trip ID, even without a reservation, and a late `ReserveHotel` gives back the hold it placed.

It's a copy of the flight service rather than a shared library, so each service can change on its own. Only the envelope and the circuit breaker are shared.

## Code

| File | |
|---|---|
| `handler.py` | The AWS calls: SQS batch, DynamoDB, task tokens, events |
| `reservation.py` | What a reservation looks like and how it answers a command. No AWS calls |
| `vendor.py` | HTTP calls to the hotel provider. Only ever called through the circuit breaker |

## Run the tests

```bash
pip install -r services/hotel/requirements-dev.txt
pytest services/hotel
```
