"""
Run: venv/bin/python tests/test_llm_judge.py

Tests the prompt-building, response-parsing, AND retry logic WITHOUT a
real Groq API call, by injecting a fake client - same dependency-
injection pattern used throughout this project.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.llm_judge import TriageJudge, TriageDecision
from app.models import Ticket
from datetime import datetime
from groq import RateLimitError
import json


# --- Fakes that mimic the real groq SDK's response shape ---
# response.choices[0].message.tool_calls[0].function.{name,arguments}
# NOTE: .arguments is a JSON STRING here, matching the real SDK - this
# is the detail that's easy to get wrong when faking it.

class FakeFunction:
    def __init__(self, arguments_dict):
        self.name = "submit_triage_decision"
        self.arguments = json.dumps(arguments_dict)


class FakeToolCall:
    def __init__(self, arguments_dict):
        self.function = FakeFunction(arguments_dict)


class FakeMessage:
    def __init__(self, arguments_dict):
        self.tool_calls = [FakeToolCall(arguments_dict)]


class FakeChoice:
    def __init__(self, arguments_dict):
        self.message = FakeMessage(arguments_dict)


class FakeGroqResponse:
    def __init__(self, arguments_dict):
        self.choices = [FakeChoice(arguments_dict)]


class FakeCompletionsAPI:
    def __init__(self, canned_input: dict):
        self._canned_input = canned_input
        self.last_call_kwargs = None
        self.call_count = 0

    def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        self.call_count += 1
        return FakeGroqResponse(self._canned_input)


class FakeGroqClient:
    def __init__(self, canned_input: dict):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletionsAPI(canned_input)


class FlakyThenSuccessCompletionsAPI:
    """Raises RateLimitError N times, then succeeds - proves retry works."""

    def __init__(self, canned_input: dict, fail_times: int):
        self._canned_input = canned_input
        self._fail_times = fail_times
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        if self.call_count <= self._fail_times:
            # groq's RateLimitError needs (message, response, body) in
            # real usage, but for a unit test we just need something of
            # the right TYPE for retry_if_exception_type to catch.
            err = RateLimitError.__new__(RateLimitError)
            raise err
        return FakeGroqResponse(self._canned_input)


class FlakyGroqClient:
    def __init__(self, canned_input: dict, fail_times: int):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FlakyThenSuccessCompletionsAPI(canned_input, fail_times)


def run():
    ticket = Ticket(
        source="zendesk",
        source_id="48213",
        subject="Export to CSV button does nothing",
        description="I click 'Export to CSV' on the reports page and nothing happens.",
        requester_email="jane.doe@customerco.com",
        created_at=datetime.now(),
    )

    matches = [
        {"ticket_id": "ZD-100", "subject": "CSV export button unresponsive",
         "resolution": "Known bug in report cache. Fix: clear browser cache.",
         "distance": 0.05},
        {"ticket_id": "ZD-106", "subject": "Export contacts list to CSV unresponsive",
         "resolution": "Separate contacts-module permissions bug.", "distance": 0.35},
    ]

    print("=" * 60)
    print("TEST 1: Judge correctly parses a canned auto_resolve decision")
    print("=" * 60)

    canned = {
        "matched_ticket_id": "ZD-100",
        "confidence": 92,
        "reasoning": "Same root cause: report cache bug on the reports export button.",
        "recommended_action": "auto_resolve",
        "customer_facing_draft": "This is a known issue - please clear your browser cache.",
    }
    fake_client = FakeGroqClient(canned)
    judge = TriageJudge(groq_client=fake_client)

    decision = judge.judge(ticket, matches)
    print(decision.model_dump_json(indent=2))

    assert isinstance(decision, TriageDecision)
    assert decision.matched_ticket_id == "ZD-100"
    assert decision.confidence == 92
    assert decision.recommended_action == "auto_resolve"
    print("\n✅ Parsing works correctly (including JSON-string arguments field)")

    print("\n" + "=" * 60)
    print("TEST 2: Prompt actually includes the distance-is-a-heuristic warning")
    print("=" * 60)
    sent_prompt = fake_client.chat.completions.last_call_kwargs["messages"][0]["content"]
    assert "NOT proof" in sent_prompt
    assert "ZD-100" in sent_prompt
    assert "ZD-106" in sent_prompt
    assert "0.0500" in sent_prompt or "0.05" in sent_prompt
    print("✅ Prompt correctly includes both candidates, their distances, and the warning")

    print("\n" + "=" * 60)
    print("TEST 3: Judge correctly parses an escalate_to_human decision (no draft)")
    print("=" * 60)
    canned_escalate = {
        "matched_ticket_id": None,
        "confidence": 20,
        "reasoning": "Both candidates share surface wording but no candidate's root cause matches.",
        "recommended_action": "escalate_to_human",
        "customer_facing_draft": None,
    }
    fake_client_2 = FakeGroqClient(canned_escalate)
    judge_2 = TriageJudge(groq_client=fake_client_2)
    decision_2 = judge_2.judge(ticket, matches)
    print(decision_2.model_dump_json(indent=2))

    assert decision_2.matched_ticket_id is None
    assert decision_2.recommended_action == "escalate_to_human"
    assert decision_2.customer_facing_draft is None
    print("\n✅ Null/escalation case parses correctly")

    print("\n" + "=" * 60)
    print("TEST 4: Retry logic actually retries on RateLimitError, then succeeds")
    print("=" * 60)
    flaky_client = FlakyGroqClient(canned, fail_times=3)
    judge_3 = TriageJudge(groq_client=flaky_client)
    decision_3 = judge_3.judge(ticket, matches)
    print(f"Calls made before success: {flaky_client.chat.completions.call_count}")
    assert flaky_client.chat.completions.call_count == 4  # 3 failures + 1 success
    assert decision_3.matched_ticket_id == "ZD-100"
    print("✅ Retried through 3 rate limit errors and succeeded on the 4th attempt")

    print("\n" + "=" * 60)
    print("TEST 5: Retry gives up after 5 attempts and re-raises")
    print("=" * 60)
    always_flaky_client = FlakyGroqClient(canned, fail_times=999)
    judge_4 = TriageJudge(groq_client=always_flaky_client)
    try:
        judge_4.judge(ticket, matches)
        raise AssertionError("Expected RateLimitError to be raised after exhausting retries")
    except RateLimitError:
        print(f"Calls made before giving up: {always_flaky_client.chat.completions.call_count}")
        assert always_flaky_client.chat.completions.call_count == 5
        print("✅ Correctly gave up after 5 attempts and re-raised the error")

    print("\n" + "=" * 60)
    print("ALL LLM JUDGE TESTS PASSED (no API key or network needed)")
    print("=" * 60)
