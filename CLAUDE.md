# Trip Conductor

Orchestrated trip booking on AWS: a Step Functions Standard state machine (the saga) sends commands to the flight, hotel and payment services over SQS with task tokens, compensates in reverse on failure, and publishes domain events to an EventBridge bus that feeds a CQRS read model and notifications. Vendor calls go through a DynamoDB-backed circuit breaker. Python 3.13 on Lambda, Terraform, GitHub Actions with OIDC. Sibling project of `../apply-relay` (choreography) and follows its conventions unless noted here.

Status: the booking transaction is built (vendor simulator, circuit breaker, flight, hotel and payment services, the full saga with confirmation and `NeedsAttention`); the API (`POST /trips`) comes next, then the projector, queries and notifications. Start with `README.md`, `docs/requirements.md`, `docs/events/catalog.md`, `docs/decisions/` and `infra/README.md`.

## Commands

Tests run one folder at a time, each with its own `pyproject.toml`. CI lists them in the matrix of `ci.yml`.

```bash
pip install -r services/flight/requirements-dev.txt
pytest services/flight        # libs/event_envelope, libs/circuit_breaker, services/vendor_simulator, services/flight, services/hotel, services/payment, statemachine
```

Terraform:

```bash
python scripts/build_lambda.py --all       # before any plan: Terraform zips services/<service>/build/package
terraform fmt -recursive infra             # CI fails on unformatted files
terraform -chdir=infra/envs/dev validate
terraform -chdir=infra/envs/dev plan -var-file=dev.tfvars
```

## Conventions

- **Messages**: commands and events both use `docs/events/envelope.schema.json`. Commands are imperative, one receiver, over SQS with `task_token`. Events are past tense, on the `trip-events` bus as `detail`. `correlation_id` is the trip ID and must be in every log line.
- **Never log or publish the task token, the payment token or customer emails.**
- **Saga**: the ASL lives in `statemachine/`. Every task has `TimeoutSeconds`. Only `VendorUnavailable` is retried before the pivot (payment); business errors go straight to compensation. After the pivot, steps are retried, never compensated. Compensations are idempotent and retried.
- **Services** answer a command with `SendTaskSuccess` / `SendTaskFailure` (error name = the catalog's error), then publish their event. A redelivered command returns the stored result; `TaskTimedOut` / `InvalidToken` on answering is ignored.
- **Circuit breaker**: vendor HTTP calls go only through `libs/circuit_breaker`. Business refusals are not failures.
- **Projector**: idempotent on `message_id`, tolerant of out-of-order and replayed events; statuses never move back from a terminal one.
- **Data ownership**: a service reads and writes only its own tables (the breaker table is scoped per vendor with `dynamodb:LeadingKeys`). Don't call one service from another.
- **Code split, logs, tests, IAM, schema changes, comments**: as in `../apply-relay/CLAUDE.md`. Resource names are `trip-conductor-<env>-...`.

## Don'ts

- Don't run `terraform apply` / `destroy` or delete AWS resources; changes reach AWS through GitHub Actions.
- Don't commit `*.tfvars` (except `example.tfvars`), `backend.hcl` or `.env`.
- The user works in PowerShell: quote arguments containing a dot after `=`.
