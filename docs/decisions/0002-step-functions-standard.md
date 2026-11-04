# 0002: Step Functions Standard workflows as the orchestrator

**Status:** accepted · 2026-10-28

## Context

The orchestrator (ADR 0001) must keep each booking's state across steps that can take minutes (retries with backoff, a vendor that answers slowly), survive crashes, retry and catch errors per step, and show what happened to one booking.

## Options

* **Step Functions Standard.** Durable state per execution, up to a year long, exactly-once step execution, `Retry` / `Catch` per state, native SQS and EventBridge integrations, task tokens for callbacks, a visual execution history kept for 90 days. Billed per state transition (about $25 per million).
* **Step Functions Express.** Much cheaper at high volume, but at most 5 minutes, at-least-once, no task tokens (`.waitForTaskToken`), and history only in CloudWatch Logs.
* **A hand-written orchestrator Lambda + a saga state table.** Full control, but I'd be rewriting durable timers, retries, timeouts and a state store, with my own bugs.
* **Temporal / Camunda.** Powerful workflow engines, but a cluster or a paid cloud to run, and outside "serverless on AWS".

## Decision

Step Functions Standard. A booking is a low-volume, long-ish, must-be-correct workflow, which is exactly what Standard is priced and built for. The execution name is the trip ID.

## Consequences

* Step Functions refuses a second execution with the same name (for 90 days), which gives `POST /trips` idempotency without a table.
* The saga is defined in ASL (JSON) under `statemachine/`, with `${...}` placeholders filled by Terraform. It's tested by running the state machine against Step Functions Local in CI, with mocked service integrations for each scenario in `docs/requirements.md`.
* A booking is about 15 to 25 state transitions: fractions of a cent.
* Changing the definition while bookings run is safe: running executions keep the version they started with.
