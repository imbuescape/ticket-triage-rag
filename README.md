## What's built so far
- `app/models.py` - the normalized `Ticket` schema every source maps into
- `app/sources/zendesk.py` - Zendesk payload normalizer
- `app/sources/jira.py` - Jira payload normalizer (handles ADF rich-text parsing)
- `app/sources/jira_client.py` - real Jira REST API client (run locally, needs network)
- `app/main.py` - FastAPI gateway with `/webhooks/zendesk` and `/webhooks/jira`
- `tests/test_phase1.py` - proves both normalizers work correctly

## Run it
```
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python tests/test_phase1.py        # run the tests
./venv/bin/uvicorn app.main:app --reload      # run the actual server
```

Test the live server:
```
curl -X POST http://localhost:8000/webhooks/zendesk \
  -H "Content-Type: application/json" \
  -d @data/mock_zendesk_ticket.json
```

If something looks wrong (0 results, auth errors), run the diagnostic:
```
python -m app.sources.jira_debug
```

python -m scripts.seed_knowledge_base   # one-time: downloads model, embeds seed data
python -m scripts.query_demo            # test retrieval quality
```