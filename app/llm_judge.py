"""
Phase 4: the reasoning step that raw vector distance can't do.

Distance answers "how similar are these two texts, geometrically" - it
cannot answer "does this past resolution's REASONING actually apply to
this new problem." That second question needs something that can read
and reason about content, not just measure it. That's what this file adds.

Provider: Groq (OpenAI-compatible API, fast + cheap open-weight models).
Default model is openai/gpt-oss-120b - per Groq's docs, ALL Groq-hosted
models support tool use, so swapping to llama-3.3-70b-versatile or
qwen/qwen3-32b is a one-line env var change (GROQ_JUDGE_MODEL), not a
code change.

Design choices worth noticing:

1. Structured output via TOOL CALLING (OpenAI-style function schema),
   not "please respond in JSON." Forcing a tool call means the API
   layer validates the shape, instead of you parsing free text that
   might be wrapped in markdown fences or malformed.

2. TriageJudge takes a `groq_client` in its constructor (dependency
   injection - same pattern as Embedder and ResolvedTicketKB). Tests
   inject a fake client that returns a canned response - no network,
   no API key needed to verify the plumbing.

3. Retry with exponential backoff on RateLimitError specifically (not
   all errors - a malformed request retrying 5 times just wastes time
   and obscures the real bug). Groq's free/low tiers have real rate
   limits you'll hit under load, unlike Anthropic's typically higher
   ceiling - this is a genuine operational difference between providers,
   not just a copy-paste concern.

4. The prompt explicitly warns the model that retrieval distance is a
   HEURISTIC, not proof - addressing the "vague ticket matched another
   vague ticket" failure mode observed during Phase 2/3 testing.
"""

import os
import json
from pydantic import BaseModel
from typing import Literal
from groq import Groq, RateLimitError
from tenacity import retry, retry_if_exception_type, wait_exponential, stop_after_attempt

from app.models import Ticket
from dotenv import load_dotenv
load_dotenv()

JUDGE_MODEL = os.environ.get("GROQ_JUDGE_MODEL", "openai/gpt-oss-120b")
# Other tool-use-capable options on Groq as of this writing:
#   qwen/qwen3-32b
#   llama-3.3-70b-versatile   <- solid fallback if gpt-oss has capacity issues


class TriageDecision(BaseModel):
    matched_ticket_id: str | None  # None if no candidate genuinely applies
    confidence: int  # 0-100, the model's own calibrated estimate
    reasoning: str  # why - this is what makes the decision auditable
    recommended_action: Literal["auto_resolve", "escalate_to_human"]
    customer_facing_draft: str | None  # only populated if auto_resolve


_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit_triage_decision",
        "description": "Submit a structured triage decision for this ticket.",
        "parameters": {
            "type": "object",
            "properties": {
                "matched_ticket_id": {
                    "type": ["string", "null"],
                    "description": "ID of the past ticket whose resolution genuinely "
                                    "applies here, or null if none of the candidates "
                                    "actually address the same root cause.",
                },
                "confidence": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Your calibrated confidence that matched_ticket_id's "
                                    "resolution correctly resolves the new ticket. Base "
                                    "this on whether the ROOT CAUSE matches, not on how "
                                    "similar the wording looks.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "1-3 sentences: why this match does or doesn't hold up. "
                                    "Explicitly note if a high-similarity candidate was "
                                    "rejected because the actual problem differs.",
                },
                "recommended_action": {
                    "type": "string",
                    "enum": ["auto_resolve", "escalate_to_human"],
                },
                "customer_facing_draft": {
                    "type": ["string", "null"],
                    "description": "A customer-facing response draft, only if "
                                    "recommended_action is auto_resolve. Otherwise null.",
                },
            },
            "required": ["matched_ticket_id", "confidence", "reasoning",
                          "recommended_action", "customer_facing_draft"],
        },
    },
}


def _build_prompt(ticket: Ticket, matches: list[dict]) -> str:
    candidates_block = "\n\n".join(
        f"Candidate {i+1} (ID: {m['ticket_id']}, retrieval_distance: {m['distance']:.4f}):\n"
        f"  Subject: {m['subject']}\n"
        f"  Resolution: {m['resolution']}"
        for i, m in enumerate(matches)
    )

    return f"""You are triaging a new support/bug ticket against a knowledge base
of past resolved tickets.

NEW TICKET (source: {ticket.source}, id: {ticket.source_id}):
  Subject: {ticket.subject}
  Description: {ticket.description}

CANDIDATE PAST RESOLUTIONS (retrieved by vector similarity, ranked by
distance - LOWER distance means more similar WORDING, but similarity in
wording is NOT proof the underlying problem is the same. A low-distance
candidate can still be the wrong match if it addresses a different root
cause. A vague new ticket can spuriously match another vague past ticket
even when there's no shared technical content - watch for this.):

{candidates_block}

Decide: does any candidate's resolution genuinely apply to the new
ticket's actual root cause? If yes, which one, and how confident are
you? If none genuinely apply - including if the closest match only
shares surface wording or vagueness rather than substance - say so and
recommend escalation instead of forcing a match.

Call submit_triage_decision with your answer."""


# Retry ONLY on rate limits - a malformed request or auth error retrying
# 5 times with backoff just wastes ~2 minutes before failing anyway.
_groq_retry = retry(
    retry=retry_if_exception_type(RateLimitError),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)


class TriageJudge:
    def __init__(self, groq_client: Groq | None = None, model: str = JUDGE_MODEL):
        # Real usage: leave groq_client=None, it builds a real client
        # (reads GROQ_API_KEY from env automatically).
        # Tests: pass in a fake client - see tests/test_llm_judge.py.
        self._client = groq_client or Groq()
        self._model = model

    @_groq_retry
    def _call_groq(self, prompt: str):
        return self._client.chat.completions.create(
            model=self._model,
            max_tokens=1024,
            temperature=0,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "function", "function": {"name": "submit_triage_decision"}},
            messages=[{"role": "user", "content": prompt}],
        )

    def judge(self, ticket: Ticket, matches: list[dict]) -> TriageDecision:
        prompt = _build_prompt(ticket, matches)
        response = self._call_groq(prompt)

        tool_call = response.choices[0].message.tool_calls[0]
        # Groq (like OpenAI) returns arguments as a JSON STRING, not a
        # dict - this is a real difference from Anthropic's tool_use.input,
        # which arrives already parsed. Easy to miss, easy bug if you forget.
        arguments = json.loads(tool_call.function.arguments)
        return TriageDecision(**arguments)