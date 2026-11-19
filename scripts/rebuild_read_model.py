"""Rebuild trip-view from the event archive (requirement F9, ADR 0006).

    python scripts/rebuild_read_model.py              # asks before emptying the table
    python scripts/rebuild_read_model.py --yes

1. Empty trip-view.
2. Replay the whole archive of the trip-events bus, to the projector's rule only: the
   notification service never sees old events again, so nobody gets an email twice.
3. Wait for the replay to finish.

The projector is idempotent and order-independent, so the view comes out the same as before.
Events published during the rebuild are fine too: whichever copy arrives second is skipped.
`GET /trips/...` answers 404 until the replay reaches that trip.

Runs with your own AWS credentials (boto3): they need dynamodb:Scan and BatchWriteItem on
trip-view, and events:DescribeArchive, StartReplay and DescribeReplay.
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import boto3


def names(env: str, region: str, account: str) -> dict:
    prefix = f"trip-conductor-{env}"
    bus = f"{prefix}-trip-events"
    return {
        "table": f"{prefix}-trip-view",
        "archive": bus,
        "bus_arn": f"arn:aws:events:{region}:{account}:event-bus/{bus}",
        "rule_arn": f"arn:aws:events:{region}:{account}:rule/{bus}/{prefix}-projector",
    }


def empty(table) -> int:
    deleted = 0
    scan = {"ProjectionExpression": "trip_id"}
    with table.batch_writer() as batch:
        while True:
            page = table.scan(**scan)
            for item in page["Items"]:
                batch.delete_item(Key={"trip_id": item["trip_id"]})
                deleted += 1
            if "LastEvaluatedKey" not in page:
                return deleted
            scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def replay(events, resources: dict) -> str:
    archive = events.describe_archive(ArchiveName=resources["archive"])
    name = f"rebuild-trip-view-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    events.start_replay(
        ReplayName=name,
        Description="Rebuild trip-view: the projector's rule only.",
        EventSourceArn=archive["ArchiveArn"],
        EventStartTime=archive["CreationTime"],
        EventEndTime=datetime.now(timezone.utc),
        Destination={"Arn": resources["bus_arn"], "FilterArns": [resources["rule_arn"]]},
    )
    return name


def wait(events, name: str) -> str:
    while True:
        state = events.describe_replay(ReplayName=name)
        if state["State"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return state["State"] + (f": {state['StateReason']}" if state.get("StateReason") else "")
        print(f"  {state['State']}, replayed up to {state.get('EventLastReplayedTime', '-')}")
        time.sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--region", default="eu-central-1")
    parser.add_argument("--yes", action="store_true", help="don't ask before emptying trip-view")
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    resources = names(args.env, args.region, account)

    if not args.yes:
        answer = input(f"Empty {resources['table']} in account {account} and replay the archive into it? [y/N] ")
        if answer.strip().lower() != "y":
            sys.exit("Nothing done.")

    deleted = empty(session.resource("dynamodb").Table(resources["table"]))
    print(f"Emptied {resources['table']}: {deleted} trips deleted.")

    events = session.client("events")
    name = replay(events, resources)
    print(f"Replay {name} started, to the projector's rule only.")
    outcome = wait(events, name)
    print(f"Replay {outcome}.")
    print("The projector's queue may still hold the last events for a few seconds.")
    if not outcome.startswith("COMPLETED"):
        sys.exit(1)


if __name__ == "__main__":
    main()
