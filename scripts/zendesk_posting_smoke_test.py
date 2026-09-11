"""
RUN THIS ON YOUR OWN MACHINE, with ZENDESK_SUBDOMAIN / ZENDESK_EMAIL /
ZENDESK_API_TOKEN set.

    python -m scripts.zendesk_posting_smoke_test --ticket-id 48213

THIS HAS REAL SIDE EFFECTS - it posts a real PUBLIC comment on a real
Zendesk ticket, visible to the requester (they may get a notification
email, depending on your trigger settings). Requires typing "yes" to
confirm before anything happens.

Pass a real ticket ID from your Zendesk instance - ideally a test/dummy
ticket you created yourself, not a real customer's live ticket.
"""

import argparse
import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(override=True)

from app.zendesk_action_sink import ZendeskActionSink
from app.models import Ticket
from datetime import datetime


def confirm(prompt: str) -> bool:
    return input(f"{prompt} [type 'yes' to proceed, anything else to skip]: ").strip().lower() == "yes"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticket-id", required=True, help="A real Zendesk ticket ID to post a test comment on")
    args = parser.parse_args()

    sink = ZendeskActionSink()
    ticket = Ticket(
        source="zendesk", source_id=args.ticket_id, subject="smoke test",
        description="smoke test", created_at=datetime.now(),
    )
    draft = ("This is an automated smoke test comment from zendesk_posting_smoke_test.py. "
             "Safe to ignore or delete.")

    print(f"Will post this PUBLIC comment on ticket {args.ticket_id}:")
    print(f"  {draft!r}")
    if confirm("Proceed?"):
        receipt = sink.post_customer_reply(ticket, draft)
        print(f"Result: {receipt}")
    else:
        print("Skipped.")


if __name__ == "__main__":
    main()
