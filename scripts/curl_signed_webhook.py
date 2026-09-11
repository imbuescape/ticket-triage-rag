"""
RUN THIS ON YOUR OWN MACHINE, with your uvicorn server already running
and ZENDESK_WEBHOOK_SECRET / JIRA_WEBHOOK_SECRET set in the SAME shell
this script runs in (it needs to compute the same signature your server
will check against).

    python -m scripts.curl_signed_webhook --source zendesk
    python -m scripts.curl_signed_webhook --source jira
    python -m scripts.curl_signed_webhook --source zendesk --file path/to/other.json
    python -m scripts.curl_signed_webhook --source zendesk --tamper   # sanity-check the 401 path

Prints the exact curl command (so you can see/copy/modify it), then runs
it and shows the response.

IMPORTANT: the signature is computed over the RAW BYTES OF THE FILE ON
DISK, read directly - not over a re-parsed-and-re-serialized version of
the JSON. curl -d @file sends the file's exact bytes; if this script
computed the signature over a different byte representation (even one
that's logically-equivalent JSON with different whitespace), the
signature would be valid for what THIS SCRIPT computed but wouldn't
match what curl actually transmits - a subtle way to make a real
signature verification bug look like a false failure.
"""

import argparse
import os
import sys
import subprocess
import shlex
from datetime import datetime, timezone
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

load_dotenv(override=True)

from app.webhook_auth import compute_zendesk_signature, compute_jira_signature

DEFAULTS = {
    "zendesk": {
        "file": "data/mock_zendesk_ticket.json",
        "url": "http://localhost:8000/webhooks/zendesk",
        "secret_env": "ZENDESK_WEBHOOK_SECRET",
    },
    "jira": {
        "file": "data/mock_jira_webhook_payload.json",
        "url": "http://localhost:8000/webhooks/jira",
        "secret_env": "JIRA_WEBHOOK_SECRET",
    },
}


def main():
    parser = argparse.ArgumentParser(description="Curl a webhook endpoint with a real, correctly-computed signature.")
    parser.add_argument("--source", choices=["zendesk", "jira"], required=True)
    parser.add_argument("--file", default=None, help="Payload file (defaults to the mock fixture for the chosen source)")
    parser.add_argument("--url", default=None, help="Target URL (defaults to localhost:8000)")
    parser.add_argument("--tamper", action="store_true",
                         help="Deliberately corrupt the signature - sanity-checks that your server actually returns 401")
    parser.add_argument("--dry-run", action="store_true", help="Print the curl command but don't execute it")
    args = parser.parse_args()

    defaults = DEFAULTS[args.source]
    payload_file = args.file or defaults["file"]
    url = args.url or defaults["url"]
    secret = os.environ[defaults["secret_env"]]  # fails loudly if you forgot to export it

    if not os.path.exists(payload_file):
        print(f"ERROR: payload file not found: {payload_file}")
        sys.exit(1)

    # Read the EXACT bytes curl will send - see module docstring for why
    # this must not be re-parsed/re-serialized JSON.
    with open(payload_file, "rb") as f:
        raw_body = f.read()

    headers = ["Content-Type: application/json"]

    if args.source == "zendesk":
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        signature = compute_zendesk_signature(raw_body, timestamp, secret)
        if args.tamper:
            signature = signature[:-4] + "XXXX"  # corrupt the last few chars
            print("--tamper set: corrupting the signature to verify the server rejects it (expect 401)\n")
        headers.append(f"X-Zendesk-Webhook-Signature: {signature}")
        headers.append(f"X-Zendesk-Webhook-Signature-Timestamp: {timestamp}")
    else:
        signature = compute_jira_signature(raw_body, secret)
        if args.tamper:
            signature = signature[:-4] + "XXXX"
            print("--tamper set: corrupting the signature to verify the server rejects it (expect 401)\n")
        headers.append(f"X-Hub-Signature: {signature}")

    curl_cmd = ["curl", "-i", "-X", "POST", url]
    for h in headers:
        curl_cmd += ["-H", h]
    curl_cmd += ["--data-binary", f"@{payload_file}"]

    print("=" * 70)
    print("CURL COMMAND (copy/paste-able)")
    print("=" * 70)
    print(" ".join(shlex.quote(part) for part in curl_cmd))
    print()

    if args.dry_run:
        print("(--dry-run set, not executing)")
        return

    print("=" * 70)
    print("EXECUTING")
    print("=" * 70)
    result = subprocess.run(curl_cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"curl exited with code {result.returncode}: {result.stderr}")


if __name__ == "__main__":
    main()