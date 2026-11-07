"""Tell the vendor simulator to misbehave.

    python scripts/chaos.py airline --failure-rate 1      # the airline is down
    python scripts/chaos.py airline --latency-ms 8000     # the airline is slow
    python scripts/chaos.py airline                       # back to normal

Settings not given go back to normal. They reach the simulator within a few seconds. Uses the
AWS CLI, so it runs with your own credentials: they need ssm:PutParameter on the parameter.
"""

import argparse
import json
import subprocess


def parameter_name(env: str, vendor: str) -> str:
    return f"/trip-conductor/{env}/chaos/{vendor}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vendor", choices=["airline"])
    parser.add_argument("--failure-rate", type=float, default=0, help="share of calls that fail, from 0 to 1")
    parser.add_argument("--latency-ms", type=int, default=0, help="delay added to every call")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--region", default="eu-central-1")
    args = parser.parse_args()

    if not 0 <= args.failure_rate <= 1:
        parser.error("--failure-rate must be between 0 and 1")

    settings = json.dumps({"failure_rate": args.failure_rate, "latency_ms": args.latency_ms})
    subprocess.run(
        [
            "aws", "ssm", "put-parameter",
            "--region", args.region,
            "--name", parameter_name(args.env, args.vendor),
            "--type", "String",
            "--overwrite",
            "--value", settings,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    print(f"{args.vendor} ({args.env}): {settings}")


if __name__ == "__main__":
    main()
