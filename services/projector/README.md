# Projector

Builds `trip-view`, the read model behind `GET /trips` ([ADR 0006](../../docs/decisions/0006-cqrs-read-model.md)). It's the only writer of that table, and it can rebuild it from the event archive at any time.

| | |
|---|---|
| **Listens to** | every trip event on the `trip-events` bus, through the `projector` rule and the `projector-events` queue. Not `CircuitOpened` / `CircuitClosed`, which have no trip |
| **Publishes** | nothing |
| **Owns** | `trip-view` table (one item per trip), with the `by-customer` index for "my trips" |

## What it does

For each event it reads the trip's view, applies the event and writes the view back, on condition that nobody wrote it in between (`version`). If someone did, it starts again from the new view.

Events arrive at least once, in any order, and all over again during a rebuild. So:

| Rule | Why |
|---|---|
| Each applied `message_id` is recorded; one already there is skipped | Redeliveries and replays change nothing |
| The trip's status goes from `REQUESTED` to one terminal status (`CONFIRMED`, `CANCELLED`, `NEEDS_ATTENTION`) and never leaves it | A late event can't reopen a finished trip |
| Each leg (`flight`, `hotel`, `payment`) has its own status, which only moves forward | A `FlightReserved` arriving after `FlightReleased` doesn't resurrect the hold |
| Fields are filled in, never overwritten | A late event can't replace a value |
| The timeline is sorted by `occurred_at` | It reads in the order things happened, not the order they arrived |

Together they make the view the same whatever order the events come in: the tests check every order of a cancelled trip. That's what makes a rebuild give the same answers (requirement F9).

An event version it doesn't know fails that message, which goes to the DLQ after 3 attempts: skipping it would leave a wrong view.

The view holds no customer email and no payment token: no event carries them.

## Code

| File | |
|---|---|
| `handler.py` | The AWS calls: SQS batch, read and conditional write of the view |
| `projection.py` | How one event changes a view. No AWS calls |

## Run the tests

```bash
pip install -r services/projector/requirements-dev.txt
pytest services/projector
```
