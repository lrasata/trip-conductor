"""Give a customer an email address, for the notification service.

    python scripts/add_contact.py cus_demo you@example.com

Writes customer-contacts, which stands in for a customer service. While SES is in its
sandbox, the address must be a verified identity: use the ALERT_EMAIL you deployed with.

Uses the AWS CLI, so it runs with your own credentials: they need dynamodb:PutItem on the
table. The address is not printed.
"""

import argparse
import json
import re
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("customer_id")
    parser.add_argument("email")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--region", default="eu-central-1")
    args = parser.parse_args()

    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", args.customer_id):
        parser.error("customer_id: 1-64 letters, digits, - or _")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", args.email):
        parser.error("email is not a valid address")

    table = f"trip-conductor-{args.env}-customer-contacts"
    item = {"customer_id": {"S": args.customer_id}, "email": {"S": args.email}}
    subprocess.run(
        ["aws", "dynamodb", "put-item", "--table-name", table, "--item", json.dumps(item), "--region", args.region],
        check=True,
    )
    print(f"{args.customer_id} now has an email address in {table}.")


if __name__ == "__main__":
    main()
