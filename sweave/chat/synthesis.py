"""Synthesis prompt builder (M1.7 step 3).

After the orchestrator's first call returns, ChatLoop scans for child
delegations (those whose ``parent_task_id`` is the chat delegation's
id). When children exist, ChatLoop waits for them to terminate and
builds a synthesis prompt -- a server-composed message that lists
each child's result -- then sends it to the orchestrator for the
second turn (the synthesised answer).

The synthesis prompt is *server-built* (the runtime's job), not
LLM-composed. The token cap (default ~8K, configurable) is the
binding constraint; if children overflow the cap, the oldest are
truncated first.

Children with status ``failed`` are still included (with their error
text) -- the orchestrator needs to know the failure so the synthesis
can acknowledge it.

M2.1 step 6: join-set children carrying a pending ``review_request``
are surfaced per-child plus a "Pending Review Requests" section
naming the resolve-via-defer contract (explicit defer to the
reviewer hint; specialists never spawn reviewers).
"""

from __future__ import annotations

import logging
from typing import Iterable, List

from sweave.runtime.delegation_store import Delegation

logger = logging.getLogger(__name__)


def _approx_tokens(text: str) -> int:
    """Rough token estimate: words * 4/3 (the well-known tiktoken-ish
    heuristic). The synthesis prompt's token cap is approximate; we
    don't need a real tokenizer here -- a stable, fast estimate is
    good enough to keep the prompt bounded.
    """
    if not text:
        return 0
    return max(1, int(len(text.split()) * 4 / 3))


def truncate_to_tokens(text: str, token_cap: int) -> str:
    """Truncate *text* to approximately *token_cap* tokens.

    The estimator is the same rough heuristic; we cut on word
    boundaries to avoid splitting mid-token.
    """
    if _approx_tokens(text) <= token_cap:
        return text
    words = text.split()
    keep = max(0, int(token_cap * 3 / 4))
    return " ".join(words[:keep]) + " ... [truncated]"


#: Task-snippet width for the fire-and-forget handoff note — the
#: TurnDelegations snippet precedent (140 chars), pinned by test.
HANDOFF_TASK_SNIPPET_CHARS = 140

#: Statuses that count as "still running" for the handoff note.
HANDOFF_RUNNING_STATUSES = frozenset({"queued", "running"})


def fire_and_forget_handoff(skipped: Iterable[Delegation]) -> str | None:
    """Handoff note for an empty join set with running fire-and-forget
    children (M2.1 follow-up §A step 3, backend half).

    When the wait-set join is empty, the synthesis turn would
    otherwise see an empty result set that reads as a stall. The
    note names the still-running non-join children (agent + task
    snippet), states the settle-time delivery contract (results land
    in the Children lane; review arrivals raise ``needs_attention``),
    and bars promised follow-ups in this turn. Returns None when
    nothing is still running — settled fire-and-forget children sit
    in the Children lane with their results and need no handoff.
    """
    running = [c for c in skipped if c.status in HANDOFF_RUNNING_STATUSES]
    if not running:
        return None
    lines = [
        f"Fire-and-forget handoff: {len(running)} deferred "
        f"child{'ren' if len(running) != 1 else ''} still running "
        f"outside this turn's join set:",
    ]
    for c in running:
        task = (c.task or "").strip().replace("\n", " ")
        if len(task) > HANDOFF_TASK_SNIPPET_CHARS:
            task = task[:HANDOFF_TASK_SNIPPET_CHARS] + "…"
        lines.append(f"- {c.agent}: {task} (status={c.status})")
    lines.append(
        "They will land in the Children lane when they settle "
        "(settle-time delivery; review arrivals raise needs_attention) "
        "— do not promise their results or follow-up SHAs in this "
        "reply; close the turn on what is known."
    )
    return "\n".join(lines)


def build_synthesis_prompt(
    *,
    children: Iterable[Delegation],
    original_user_message: str,
    token_cap: int = 8_000,
) -> str:
    """Build the synthesis prompt the orchestrator will see on its
    second turn.

    The prompt is a structured list of children + their results,
    followed by the original user message. The orchestrator reads it
    and writes a final, user-facing answer that synthesises the
    children's outputs.

    Per-child shape: ``[child N] specialist=<name> task=<text> status=<done|failed>
    output=<text> error=<text or "(none)">``. Children are listed in
    completion order (oldest first); per-child output truncation is
    oldest-first when the total exceeds the cap.
    """
    children_list: List[Delegation] = sorted(
        list(children),
        key=lambda c: c.completed_at or c.updated_at,
    )
    if not children_list:
        return ""

    # Reserve a budget for the prompt framing + original user message.
    framing_overhead = 200  # lines like "## Child Results" + meta
    user_msg_tokens = _approx_tokens(original_user_message)
    available_for_children = max(
        0, token_cap - framing_overhead - user_msg_tokens
    )
    if available_for_children <= 0:
        available_for_children = max(token_cap // 2, 1000)

    # Per-child budget: even split (oldest-truncated on overflow).
    per_child_cap = max(200, available_for_children // len(children_list))

    lines: list[str] = [
        f"The following specialists completed work for the user's "
        f"request. Synthesise their results into a single, user-facing "
        f"answer. Stay concise; the user sees this verbatim.",
        "",
        f"## Child Results ({len(children_list)} specialist"
        f"{'s' if len(children_list) != 1 else ''})",
        "",
    ]
    for i, c in enumerate(children_list, 1):
        status = c.status or "unknown"
        output = c.output or ""
        error = c.error or "(none)"
        truncated = truncate_to_tokens(output, per_child_cap)
        lines.append(f"### Child {i}: {c.agent}")
        lines.append(f"- status: {status}")
        lines.append(f"- task: {c.task}")
        if status == "failed":
            lines.append(f"- error: {error}")
        else:
            lines.append(f"- output: {truncated}")
        request = getattr(c, "review_request", None)
        if isinstance(request, dict) and request:
            # M2.1 step 6: the wait-set settle surfaces pending
            # review-requests of join-set children (the request rides
            # to_dict, no new endpoint — the M2.0 detail-fold
            # precedent). The orchestrator resolves each request
            # explicitly; specialists never spawn reviewers
            # (one-authority rule).
            hint = request.get("reviewer_hint") or "reviewer"
            diff_ref = request.get("diff_ref") or {}
            branch = diff_ref.get("branch") or "(no branch)"
            pr_url = diff_ref.get("pr_url") or "(no PR)"
            lines.append(
                f"- review_requested: reviewer_hint={hint} "
                f"branch={branch} pr={pr_url}"
            )
        lines.append("")

    pending = [
        c for c in children_list
        if isinstance(getattr(c, "review_request", None), dict)
        and getattr(c, "review_request", None)
    ]
    if pending:
        names = ", ".join(
            f"{c.agent} (reviewer_hint="
            f"{(c.review_request or {}).get('reviewer_hint') or 'reviewer'})"
            for c in pending
        )
        lines.append("## Pending Review Requests")
        lines.append(
            f"The following join-set children finished into `review` "
            f"and request explicit review: {names}. To resolve, hand "
            f"each one to its reviewer via defer(target=<reviewer_hint>, "
            f"task=<what to review>, caller_delegation_id=<the review "
            f"child's delegation_id>), or batch the reviews now. "
            f"Promotion stays explicit (POST "
            f"/api/delegations/{{id}}/promote); never mark review work "
            f"done without a review."
        )
        lines.append("")

    lines.append("## Original User Message")
    lines.append(original_user_message)
    return "\n".join(lines)
