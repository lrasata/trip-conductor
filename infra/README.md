# Infrastructure

All AWS resources are defined in Terraform (>= 1.10, AWS provider 5.x). State lives in S3 with a DynamoDB lock table.

```
infra/
├── bootstrap/   # one-time: S3 bucket + DynamoDB lock table for Terraform state
├── ci/          # one-time: the CI roles and the permissions boundary
└── envs/dev/    # the dev environment
```

## What the foundation creates

| | Resources |
|---|---|
| Events | `trip-conductor-dev-trip-events` EventBridge bus, and an archive of everything on it, kept forever |
| Alerts | `trip-conductor-dev-ops-alerts` SNS topic → your email |
| Cost guardrail | an account-wide monthly AWS budget (default $5) that emails you at 80% forecast and 100% actual |

**Expected cost:** effectively $0 while idle.

Services are added one at a time, and each gets its own `<service>.tf` gated by `local.deploy`.

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
