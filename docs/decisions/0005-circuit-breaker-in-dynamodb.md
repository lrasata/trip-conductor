# 0005: A shared circuit breaker with its state in DynamoDB

**Status:** accepted · 2026-10-28

## Context

Each service calls an external vendor over HTTP. When a vendor goes down, every booking keeps calling it, waits for a timeout, and fails: slow for customers, and more load on a vendor that is trying to recover. Retries in the saga make it worse. A circuit breaker stops the calls for a while after repeated failures.

## Options

* **Only retries with backoff in the saga.** Each booking backs off, but bookings don't learn from each other: the 50th booking still calls the dead vendor.
* **In-memory breaker per Lambda instance** (for example `pybreaker`). Zero latency, but a Lambda function runs as many instances that come and go; each one has to discover the outage on its own, and a new instance starts closed.
* **Breaker state in DynamoDB, shared by all instances.** One record per vendor: `state`, `failure_count`, `window_started_at`, `opened_at`. Conditional writes make the transitions safe under concurrency. Costs one read per call (on-demand, about a millisecond or two).
* **Breaker in the state machine** (AWS's published pattern: a `Choice` reads the breaker item before invoking the task). Visible in the execution, but the logic is spread through the ASL for every vendor step, and the service no longer protects itself.

## Decision

A small shared library, `libs/circuit_breaker`, used by each service around its vendor calls, with state in one `circuit-breakers` table keyed by vendor.

* Closed → Open after 5 failures within 60 s.
* Open → fail fast with `VendorUnavailable` for 30 s.
* After 30 s, one caller wins a conditional write to `HALF_OPEN` and makes the probe; everyone else keeps failing fast. Success closes the breaker, failure reopens it.
* Failures are timeouts (3 s), connection errors and 5xx. Business refusals are successes from the breaker's point of view.
* Thresholds come from environment variables, so the demo can use smaller numbers.

## Consequences

* `libs/` is the one place code is shared between services, an exception to Apply Relay's "each service is self-contained". `build_lambda.py` bundles it into each service's zip, and it has its own tests.
* The table is shared physically, but each service's IAM role is limited to its own vendor's item with `dynamodb:LeadingKeys`, so data ownership still holds.
* The library publishes `CircuitOpened` / `CircuitClosed` only from the caller whose conditional write made the transition, so each transition is announced once.
* If DynamoDB itself is unreachable, the breaker fails open (calls go through): losing protection is better than refusing every booking.
* The saga's retry with backoff (30, 60, 120 s) is aligned with the cool-down, so a retried booking usually reaches the vendor right after a probe succeeds.
