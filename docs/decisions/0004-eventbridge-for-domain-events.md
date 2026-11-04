# 0004: EventBridge for domain events

**Status:** accepted · 2026-10-28

## Context

Services and the saga publish facts (`FlightReserved`, `TripConfirmed`, ...) that the projector, the notification service and alarms consume. The read model must be rebuildable from history (CQRS, ADR 0006). Apply Relay used SNS + SQS fan-out and listed EventBridge as the alternative to try.

## Options

* **SNS + SQS**, as in Apply Relay. Proven, cheap, but no history: once delivered, an event is gone. Rebuilding a read model would need a separate event store.
* **EventBridge custom bus, with SQS behind each consumer.** Rules match on any field of the event, the state machine can `PutEvents` natively without a Lambda, and an **archive** keeps every event for replay into the bus.
* **Kinesis Data Streams.** Ordered and replayable, but provisioned shards, consumer checkpointing, and more than this volume needs.
* **A DynamoDB event store + Streams.** Real event sourcing, but then the services would write events instead of state, which is a bigger change than CQRS needs here.

## Decision

One custom bus, `trip-events`, with an archive of everything. Each consumer gets a rule and its own SQS queue with a DLQ, so consumers keep the isolation they had in Apply Relay.

## Consequences

* Replay re-sends archived events to the bus with their original content; consumers must be idempotent on `message_id` and accept old events. A replay can be limited to chosen rules, so a rebuild targets only the projector's rule and other consumers never see it.
* Replay is not ordered, and neither is normal delivery. The projector is designed for that (ADR 0006).
* Rules can filter on `detail` fields, which replaces SNS filter policies.
* EventBridge adds a little latency (usually well under a second) compared with SNS; fine for a read model and emails.
* Archive storage is billed per GB; at this volume, negligible.
