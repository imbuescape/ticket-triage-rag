"""
RUN THIS ON YOUR MACHINE (same as jira_client.py - needs real network access).

    venv/bin/python -m app.sources.jira_debug

This bypasses our normal client and hits Jira's API directly with maximum
visibility, so we can see exactly what's wrong instead of guessing.
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()


def main():
    base_url = os.environ["JIRA_BASE_URL"].rstrip("/")
    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]
    project_key = os.environ.get("JIRA_PROJECT_KEY", "<not set>")
    auth = (email, token)
    headers = {"Accept": "application/json"}

    print("=" * 60)
    print("STEP 1: Can we auth at all? (list all projects visible to this account)")
    print("=" * 60)
    resp = requests.get(f"{base_url}/rest/api/3/project/search", auth=auth, headers=headers, timeout=10)
    print("Status:", resp.status_code)
    if resp.status_code == 200:
        projects = resp.json().get("values", [])
        print(f"Found {len(projects)} project(s) visible to this account:")
        for p in projects:
            print(f"  - key={p['key']!r}  name={p['name']!r}  id={p['id']}")
    else:
        print("Auth or permissions problem. Response body:")
        print(resp.text[:1000])
        return  # no point continuing if step 1 fails

    print()
    print("=" * 60)
    print(f"STEP 2: Does project key {project_key!r} (from your .env) actually exist above?")
    print("=" * 60)
    found = any(p["key"] == project_key for p in projects)
    print("Match found:" , found)
    if not found:
        print(f"⚠️  Your JIRA_PROJECT_KEY={project_key!r} doesn't match any project you can see.")
        print("   Update your .env to one of the keys listed in Step 1, then rerun this script.")
        return

    print()
    print("=" * 60)
    print(f"STEP 3: Raw JQL search against project = {project_key}")
    print("=" * 60)
    resp = requests.get(
        f"{base_url}/rest/api/3/search/jql",
        params={"jql": f"project = {project_key}", "maxResults": 10},
        auth=auth,
        headers=headers,
        timeout=10,
    )
    print("Status:", resp.status_code)
    print("Raw response body:")
    print(json.dumps(resp.json(), indent=2)[:2000])


if __name__ == "__main__":
    main()
