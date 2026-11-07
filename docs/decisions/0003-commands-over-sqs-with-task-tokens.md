# 0003: Send commands to services over SQS with task tokens

**Status:** accepted · 2026-10-28

## Context

The saga must ask each service to do something and wait for the answer. That wait can be long when the service's vendor is slow or the service itself is down.

## Options

* **Invoke the service Lambda directly from the state machine** (`lambda:invoke`). Simplest: synchronous, the answer is the Lambda's return value. But the saga is coupled to the Lambda being up and to its deployment, there is no buffer, and throttling turns into saga errors.
* **SQS + task token** (`sqs:sendMessage.waitForTaskToken`). The saga puts a command in the service's queue with a token and pauses. The service processes it whenever it can and calls `SendTaskSuccess` / `SendTaskFailure`. The queue buffers bursts and outages and has its own retries and DLQ.
* **EventBridge + task token.** Same callback idea, but commands have exactly one receiver, so the routing EventBridge offers buys nothing, and it would still need SQS behind it for buffering.

## Decision

SQS with task tokens: one command queue per service. It keeps what made Apply Relay robust (buffering, per-service DLQs, a service can be down without losing work) and adds the saga's control over the flow.

## Consequences

* Every task has a `TimeoutSeconds`, so a service that never answers fails the step instead of pausing the saga forever. Vendor calls are capped at 3 s, far below it, so services send no heartbeats.
* A service may answer twice (SQS redelivery). The second `SendTaskSuccess` gets `TaskTimedOut` / `InvalidToken` from Step Functions, which the service ignores.
* The task token is a credential for one step: it's never logged and never put in an event.
* Each service's IAM role gets `states:SendTaskSuccess` and `SendTaskFailure` on the booking state machine only.
