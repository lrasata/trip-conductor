# Flight service

Holds and releases seats with the airline, on the saga's command.

| | |
|---|---|
| **Listens to** | `ReserveFlight`, `ReleaseFlight` on the `flight-commands` queue. `ConfirmFlight` comes with the pivot |
| **Publishes** | `FlightReserved`, `FlightReleased`, `CircuitOpened`, `CircuitClosed` |
| **Owns** | `flight-reservations` table, the `airline` item in `circuit-breakers` |

## What it does

Each command is answered with its task token. A redelivered command is answered from the stored reservation, without calling the airline again. Only final results are stored: `VendorUnavailable` isn't, so the saga's next retry really tries the airline.

`ReleaseFlight` works from the trip ID, even without a reservation: if `ReserveFlight` timed out, a hold may exist that the saga never heard about. It marks the trip as released first, so a late `ReserveFlight` gives back the hold it placed.

## Code

| File | |
|---|---|
| `handler.py` | The AWS calls: SQS batch, DynamoDB, task tokens, events |
| `reservation.py` | What a reservation looks like and how it answers a command. No AWS calls |
| `vendor.py` | HTTP calls to the airline. Only ever called through the circuit breaker |

## Run the tests

```bash
pip install -r services/flight/requirements-dev.txt
pytest services/flight
```
