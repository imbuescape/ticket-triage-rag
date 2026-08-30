"""
RUN THIS ON YOUR OWN MACHINE, NOT IN THIS CHAT'S SANDBOX.

This sandbox's outbound network is restricted to a small allowlist
(pypi, npm, github, etc) and cannot reach your Jira demo instance at
*.atlassian.net. That's a deliberate security boundary of this
environment, not a bug in the code.

Setup on your machine:
    pip install requests python-dotenv
    export JIRA_BASE_URL="https://yoursite.atlassian.net"
    export JIRA_EMAIL="you@example.com"
    export JIRA_API_TOKEN="..."   # generate at id.atlassian.com/manage-profile/security/api-tokens
    export JIRA_PROJECT_KEY="ENG"  # or whatever your demo project key is

Then:
    python -m app.sources.jira_client
"""

import os
import requests
from dotenv import load_dotenv
from app.sources.jira import normalize_jira_payload

load_dotenv()  # reads .env in the current working directory and populates
# os.environ automatically - this is the missing piece. No more manual
# `export $(cat .env | xargs)` needed; just have a .env file present.


def fetch_recent_issues(max_results: int = 5) -> list[dict]:
    base_url = os.environ["JIRA_BASE_URL"].rstrip("/")
    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]
    project_key = os.environ["JIRA_PROJECT_KEY"]

    resp = requests.get(
        f"{base_url}/rest/api/3/search/jql",
        params={
            "jql": f"project = {project_key} ORDER BY created DESC",
            "maxResults": max_results,
            "fields": "key,summary,description,reporter,created",
        },
        auth=(email, token),
        headers={"Accept": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["issues"]


if __name__ == "__main__":
    issues = fetch_recent_issues()
    print(f"Fetched {len(issues)} issues from Jira.\n")

    for issue in issues:
        ticket = normalize_jira_payload(issue)
        print(f"--- {ticket.source_id} ---")
        print(f"Subject: {ticket.subject}")
        print(f"Description (first 200 chars): {ticket.description[:200]}")
        print(f"Requester: {ticket.requester_email}")
        print()
