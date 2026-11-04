# 0001: Orchestrate the booking transaction

**Status:** accepted · 2026-10-28

## Context

Booking a trip touches three services (flight, hotel, payment) that each own their data. The steps must succeed together: a held seat without a hotel, or a charge without bookings, is a bug the customer notices. No database transaction spans the services, so consistency comes from a saga: local steps, each with a compensating action.

A saga can be coordinated two ways. Apply Relay used choreography, which fit a pipeline where each step only adds information and nothing is ever undone.

## Options

* **Choreography.** Each service listens for the previous step's event and publishes its own. On failure it publishes a failure event that the earlier services listen to in order to undo their work. There is no central component, but the flow and its failure paths are spread across every service's subscriptions. With 3 forward steps and 2 compensations, that's already 8 event-to-handler links to keep consistent.
* **Orchestration.** One component sends each service a command, waits for the answer, and decides the next step or the compensations. The flow, including every failure path, is written in one place and visible per booking.

## Decision

Orchestration for the booking transaction. Events remain for what *reacts* to the outcome (read model, notifications), where choreography's loose coupling is an advantage.

## Consequences

* The orchestrator knows every service's commands; adding a step means changing the state machine. That's acceptable for a transaction whose shape changes rarely and must be reviewed when it does.
* The orchestrator is a central dependency. Using a managed one (ADR 0002) keeps it from becoming a service to operate.
* Services stay unaware of each other: flight never hears about hotel. They only answer commands and publish facts.
* Debugging a booking is opening one execution instead of correlating logs from five services.
