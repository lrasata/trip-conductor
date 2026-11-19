# Infrastructure

All AWS resources are defined in Terraform (>= 1.10, AWS provider 5.x). State lives in S3 with a DynamoDB lock table.

```
infra/
├── bootstrap/   # one-time: S3 bucket + DynamoDB lock table for Terraform state
├── ci/          # one-time: the CI roles and the permissions boundary
├── envs/dev/    # the dev environment
└── modules/
    ├── command_queue/   # SQS queue + DLQ + alarms, per service the saga commands
    └── event_queue/     # EventBridge rule → SQS queue + DLQ + alarms, per service that reacts to events
```

## What the foundation creates

| | Resources |
|---|---|
| Events | `trip-conductor-dev-trip-events` EventBridge bus, and an archive of everything on it, kept forever |
| Alerts | `trip-conductor-dev-ops-alerts` SNS topic → your email |
| Cost guardrail | an account-wide monthly AWS budget (default $5) that emails you at 80% forecast and 100% actual |

**Expected cost:** effectively $0 while idle.

Services are added one at a time, and each gets its own `<service>.tf` gated by `local.deploy`.

## What the services add

| | Resources |
|---|---|
| Vendor simulator | `trip-conductor-dev-vendor-simulator` Lambda behind the public `trip-conductor-dev-vendors` HTTP API (20 requests/s, burst 40). Holds no data. Its failure settings are one SSM parameter per vendor: `/trip-conductor/dev/chaos/airline`, `.../hotel`, `.../payment` |
| Flight service | `trip-conductor-dev-flight` Lambda, `trip-conductor-dev-flight-commands` queue with a DLQ and alarms, `trip-conductor-dev-flight-reservations` table |
| Hotel service | The same for rooms: `trip-conductor-dev-hotel`, `trip-conductor-dev-hotel-commands`, `trip-conductor-dev-hotel-reservations` |
| Payment service | The same for charges: `trip-conductor-dev-payment`, `trip-conductor-dev-payment-commands`, `trip-conductor-dev-payments` |
| Circuit breakers | `trip-conductor-dev-circuit-breakers` table, one item per vendor |
| Saga | `trip-conductor-dev-booking-saga` state machine, with alarms on failed and timed-out executions. A cancelled trip is a normal end, so it fires no alarm; `NeedsAttention` does |
| Trips API | `trip-conductor-dev-trips` REST API (stage `v1`) with one API key and a usage plan (10 requests/s, burst 20, 1000 a day). `POST /trips` → `trip-conductor-dev-trip-commands` Lambda → the saga |
| Read model | `trip-conductor-dev-projector` rule on the bus → `trip-conductor-dev-projector-events` queue (DLQ, alarms) → `trip-conductor-dev-projector` Lambda → `trip-conductor-dev-trip-view` table. `GET /trips` and `GET /trips/{trip_id}` → `trip-conductor-dev-trip-queries` Lambda, which only reads that table |

### Try it

Send a request from [`scripts/requests/`](../scripts/requests/) to the API. You need its URL (`terraform output trips_api_url`, or API Gateway → `trip-conductor-dev-trips` → Stages → `v1`) and the API key's value (API Gateway → API keys → `trip-conductor-dev-trips` → Show). In PowerShell:

```powershell
$api = "https://<id>.execute-api.eu-central-1.amazonaws.com/v1"
$headers = @{ "x-api-key" = "<key>"; "Idempotency-Key" = "demo-001" }
Invoke-RestMethod -Method Post -Uri "$api/trips" -Headers $headers -ContentType "application/json" -InFile scripts/requests/trip-available.json
```

The answer holds the trip ID, which is also the name of the saga's execution in the Step Functions console. Follow the booking with the `location` it returns:

```powershell
$trip = Invoke-RestMethod -Method Post -Uri "$api/trips" -Headers $headers -ContentType "application/json" -InFile scripts/requests/trip-available.json
Invoke-RestMethod -Uri "$api/trips/$($trip.trip_id)" -Headers $headers | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri "$api/trips?customer_id=cus_demo" -Headers $headers | ConvertTo-Json -Depth 5
```

Right after the `POST` the trip can be a `404` for a second or two: the read model is updated asynchronously. Use a new `Idempotency-Key` for each booking: the same key returns the trip it booked before.

You can still start the state machine by hand: in the Step Functions console choose **Start execution**, give it a new name (`trp_demo_001`, ...) and paste one of the same files as input.

| Test | Input | Expected |
|---|---|---|
| Happy path (D1) | `trip-available.json` | Reserve flight, reserve hotel, charge 618.00 EUR, confirm both in parallel, **TripConfirmed**. Both reservations are `CONFIRMED`, the payment `CHARGED`. `GET /trips/{id}` shows `CONFIRMED` and a timeline of 7 events |
| Flight sold out | `flight-sold-out.json` | **TripCancelled**, `FLIGHT_UNAVAILABLE`; the hotel is never asked |
| Hotel sold out (D2) | `hotel-sold-out.json` | `ReleaseFlight`, then **TripCancelled**, `HOTEL_UNAVAILABLE` |
| Card declined (D3) | `card-declined.json` | `ReleaseHotel`, then `ReleaseFlight`, then **TripCancelled**, `PAYMENT_DECLINED`. Both reservations are `RELEASED`, the payment `DECLINED` |
| Airline down (D4) | `chaos/airline` set to `{"failure_rate": 1, "latency_ms": 0}`, then `trip-available.json` | Retries after 5, 10 and 20 s, then `VENDOR_UNAVAILABLE`. Two runs started together open the `airline` breaker |
| Vendor back | Parameter back to `{"failure_rate": 0, "latency_ms": 0}`, wait 30 s, then `trip-available.json` | The breaker closes |
| Vendor slow (D5) | `{"failure_rate": 0, "latency_ms": 8000}` | Calls time out after 3 s and count as failures |
| Confirmation fails after payment (D6) | `chaos/hotel` set to `{"failure_rate": 1, "latency_ms": 0, "operations": ["confirm"]}`, then `trip-available.json` | Reserved and charged, `ConfirmHotel` retried, then **NeedsAttention**: the execution fails and the alarm emails you. Nothing is released or refunded |
| Same request twice (D7) | `trip-available.json` sent twice with the same `Idempotency-Key` | Both answers carry the same `trip_id`, and the console shows one execution. A new key starts a new booking |

Change a parameter in the console (Systems Manager → Parameter Store → Edit) or with `python scripts/chaos.py hotel --failure-rate 1 --operations confirm`, which needs the AWS CLI and credentials allowed `ssm:PutParameter` on `/trip-conductor/*`. The simulator reads it within 5 s. `operations` limits the failures to some calls: `hold`, `release`, `confirm`, `charge`.

Events aren't visible on the bus itself: the services log `event published` for each one, the saga's are steps in its graph, and the projector logs each one it applies (`/aws/lambda/trip-conductor-dev-projector`).

## Deploy it yourself

Prerequisites: Terraform >= 1.10, AWS CLI v2, and credentials for your account (`aws sts get-caller-identity` should work). Default region is `eu-central-1` (Frankfurt).

The commands below are for bash. In PowerShell, quote any argument that has a dot after `=`, e.g. `terraform init "-backend-config=backend.hcl"` and `terraform plan "-var-file=dev.tfvars"`. Unquoted, PowerShell splits it at the dot and Terraform fails with "Too many command line arguments".

**1. Create the state bucket and lock table (once).**

```bash
cd infra/bootstrap
terraform init
terraform apply            # note the state_bucket and lock_table outputs
```

**2. Configure the dev environment.**

```bash
cd ../envs/dev
cp backend.hcl.example backend.hcl     # put the bucket and lock_table names from step 1 in it
cp example.tfvars dev.tfvars           # put your email in it
```

**3. Create the CI roles (once).**

```bash
cd ../../ci
cp example.tfvars ci.tfvars                       # already holds this repo's owner/name and OIDC subject
terraform init -backend-config=../envs/dev/backend.hcl
terraform apply -var-file=ci.tfvars
```

If the account has no GitHub OIDC provider yet, set `create_oidc_provider = true` in `ci.tfvars`.

## CI/CD with GitHub Actions

| Workflow | When | What |
|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) | every pull request | tests (Python 3.13), `terraform fmt` and `validate` for all three stacks, then a `terraform plan` for dev shown in the run summary |
| [`deploy.yml`](../.github/workflows/deploy.yml) | push to `main` | the same CI, then build, plan and apply to dev in the GitHub environment `dev` |
| [`destroy.yml`](../.github/workflows/destroy.yml) | manual | removes the services, or all of dev; asks you to type `destroy dev` |

There are no AWS keys in GitHub. Jobs exchange GitHub's OIDC token for 1-hour credentials, and [`ci/`](ci/) defines who can get which:

| Role | Who can assume it | Can do |
|---|---|---|
| `trip-conductor-github-plan` | pull requests and `main` of this repo | read-only (`ReadOnlyAccess`), minus customer tables and saga execution data; read/write the state lock |
| `trip-conductor-github-deploy` | only jobs running in the GitHub environment `dev` | create and change `trip-conductor-*` resources only; cannot touch the CI roles or delete the state bucket |

Every role CI creates must carry the `trip-conductor-service-boundary` permissions boundary, so even a malicious change to a service's IAM policy in a pull request can't grant more than the services need.

Until the repository variables below exist, the AWS jobs are skipped and CI just runs the tests and checks.

If the deploy fails with `Not authorized to perform sts:AssumeRoleWithWebIdentity`, compare `github_subject_prefix` in `ci.tfvars` with `sub_claim_prefix` from `https://api.github.com/repos/<owner>/<name>/actions/oidc/customization/sub`, and apply again.

In GitHub, under *Settings → Secrets and variables → Actions → Variables*, add:

| Variable | Value |
|---|---|
| `AWS_PLAN_ROLE_ARN` | `terraform output -raw plan_role_arn` |
| `AWS_DEPLOY_ROLE_ARN` | `terraform output -raw deploy_role_arn` |
| `LAMBDA_PERMISSIONS_BOUNDARY_ARN` | `terraform output -raw lambda_permissions_boundary_arn` |
| `TF_STATE_BUCKET` | the bucket from `infra/bootstrap` |
| `ALERT_EMAIL` | your email |
| `AWS_REGION` | optional, defaults to `eu-central-1` |

Finally create the environment `dev` under *Settings → Environments*. Adding yourself as a required reviewer there makes every deploy wait for your approval.

After the first deploy, AWS sends `ALERT_EMAIL` a "Subscription Confirmation" for the alerts topic. Click the link once.

## Tear it down

Run the Destroy workflow. The state bucket has `prevent_destroy` so it isn't deleted by accident. Remove that line first if you really want to delete it.
