"""Delegation routes (the v2 async task contract).

M1.prep step 6 wires the :class:`JobRunner` and the v2 async submit. The
synchronous ``POST /api/tasks`` endpoint keeps working for now and is
marked deprecated in OpenAPI; it will be removed once the UI migrates
(M1.4+).

M1.1 step 2: DelegationStores are per-project. The routers below
scan the known stores on read paths (``list``, ``get``) because
``delegation_id`` is unique across all projects and the read API is
global.

M1.1 step 4:
* ``GET /api/delegations`` gains filters: ``?project_name=&status=
  &parent_task_id=`` (M1.1 plan §4.4.1).
* ``POST /api/v2/tasks`` accepts ``parent_task_id`` and ``manifest``
  passthrough (M1.1 plan §4.4.2; generation of the manifest is M1.6).
* ``GET /api/subagent-runs`` + ``POST /api/subagent-runs`` +
  ``POST /api/subagent-runs/{id}/finish`` (M1.1 plan §4.4.3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sweave.runtime.delegation_store import Manifest, resolve_blocking
from sweave.runtime.subagent_store import SubAgentRun, SubAgentStatus
from sweave.web.deps import get_state
from sweave.web.state import AppState

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def _validate_harness_override(value: Optional[str]) -> Optional[str]:
    """Validate a per-task ``harness`` override (step 4).

    None/empty passes through (no override); a name that resolves
    in the harness registry passes through; anything else is a 400
    — a typo must fail fast at submit, never become a mystery
    default three layers down.
    """
    if not value:
        return None
    from sweave.harness.base import harness_registry

    if harness_registry.get(value) is None:
        known = ", ".join(sorted(harness_registry.list())) or "(none)"
        raise HTTPException(
            400, f"unknown harness {value!r} (known: {known})"
        )
    return value


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class EstimateIn(BaseModel):
    """Caller-supplied estimate for one task delegation (M2.0)."""

    model_config = {"extra": "ignore"}

    tokens: Optional[int] = Field(default=None, ge=0)
    seconds: Optional[float] = Field(default=None, ge=0)


class TaskSubmitV2(BaseModel):
    task: str
    agent: Optional[str] = None
    model: Optional[str] = None
    parent_session_id: Optional[str] = None
    project_name: Optional[str] = None
    # M1.1: deferral chain link (None = orchestrator-initiated).
    parent_task_id: Optional[str] = None
    # M1.1: optional specialist self-report; generation is M1.6 scope.
    manifest: Optional[Manifest] = None
    # M2.0: optional caller-supplied estimate (record only — no
    # enforcement, no calibration). Unknown keys are ignored (lenient:
    # a strict shape here would 422 whole submits on LLM-supplied
    # extras); known keys must be non-negative numbers or the submit
    # is a 422.
    estimate: Optional[EstimateIn] = None
    # M2.1: wait-set opt-in. True = this child joins the synthesis
    # join set; False = fire-and-forget into the Children lane.
    # None (omitted) = resolve at submit: a child of a chat-turn
    # delegation joins by default (2026-09-14 ruling — the
    # orchestrator defers because it needs the answer), everything
    # else stays fire-and-forget. See
    # ``sweave.runtime.delegation_store.resolve_blocking``.
    blocking: Optional[bool] = None
    # Step 4: per-task harness override (transient — resolved at
    # turn start, never persisted on the record). Unknown names are
    # a 400 (fail fast on operator typos, never silently default).
    harness: Optional[str] = None


class TaskSubmitV2Response(BaseModel):
    delegation_id: str
    task_id: str
    status: str
    agent: str
    model: str


class SubAgentRunStart(BaseModel):
    agent: str
    purpose: str = "custom"  # explore|investigate|custom
    parent_session_id: Optional[str] = None
    project_name: Optional[str] = None


class SubAgentRunFinish(BaseModel):
    """Terminal-state update for a SubAgentRun. ``status`` is restricted
    to ``done`` / ``failed`` (you cannot "finish" a run by setting it
    back to ``running`` — that would be a no-op transition).
    """
    status: Literal["done", "failed"]
    output_summary: str = ""


class EscalateRequest(BaseModel):
    """M1.9 step 3 (+ M1.11 kinds): ask_human / escalate request.

    * ``question`` -- the question (or escalation message) for the
      human / orchestrator (required).
    * ``options`` -- optional list of choices; when present the UI
      renders buttons, when absent a free-form text input.
    * ``kind`` -- ``question`` (orchestrator -> human, blocking) or
      ``escalation`` (specialist -> orchestrator, notice).
    * ``audience`` -- ``human`` | ``orchestrator`` (mirrors kind).
    """

    question: str
    options: list[str] | None = None
    kind: str = "question"
    audience: str = "human"


class AnswerRequest(BaseModel):
    """M1.9 step 3: the human's answer to an open escalation."""

    response: str


class SkipRequest(BaseModel):
    """M1.11: explicit human skip (opencode-Esc equivalent).

    ``confirmed`` must be true — the UI's system-issued "are you
    sure?" dialog sets it. Unconfirmed skips are rejected (409)
    so a fat-finger tap cannot silently drop a blocking question.
    """

    confirmed: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_stores(state: AppState) -> list:
    """Snapshot of the per-project stores known to the registry."""
    if state.delegation_stores is None:
        return []
    return state.delegation_stores.known_projects_stores()


async def _has_pending_escalation(state: AppState, delegation_id: str) -> bool:
    """True when the delegation still has a pending escalation record.

    M2.1 follow-up §A step 1: ``needs_attention`` now means "answer
    OR promote", so clearing the flag must check the OTHER source —
    promote keeps the flag while a question is pending, and
    answer/skip keep it while a review is unpromoted.
    """
    if state.escalation_store is None:
        return False
    try:
        rec = await state.escalation_store.get(delegation_id=delegation_id)
    except Exception:
        return False
    return rec is not None and rec.get("status") == "pending"


def _review_owes_promotion(rec: Any) -> bool:
    """True when the delegation still owes promotion (status review).

    Review Phase 1: the single rule behind every attention-clear
    site — the router answer/skip loops AND the production store
    flagger below. A question resolving (answer/skip/timeout) must
    not clear ``needs_attention`` while the review is unpromoted;
    only promote clears (unless a question is still pending, which
    is the promote path's own check).
    """
    return rec is not None and getattr(rec, "status", None) == "review"


def make_attention_flagger(state: AppState):
    """Build the production ``needs_attention`` flagger.

    Injected into the ``EscalationStore`` so EVERY creator (ask_human
    router, permission bridge, stall branch) fulfils the flag
    contract at the store boundary — and every resolver (answer,
    skip, force_timeout) clears through the SAME review-aware rule
    as the router loops. Without the review guard here, a
    store-level clear would wipe the flag on a review-owed
    delegation BEFORE the router's review-aware loop runs (the
    router breaks early without restoring it).
    """

    async def _flag(delegation_id: str, value: bool) -> None:
        for store in _all_stores(state):
            rec = store.get(delegation_id)
            if rec is None:
                continue
            if not value and _review_owes_promotion(rec):
                break
            try:
                await store.update(delegation_id, needs_attention=value)
            except Exception:
                pass
            break

    return _flag


def _filter_delegations(
    records: list, *,
    project_name: Optional[str],
    status: Optional[str],
    parent_task_id: Optional[str],
) -> list:
    """Apply M1.1 step 4 filters to a list of Delegation records.

    All filters are AND'd; None means "don't filter on this field".
    """
    out = records
    if project_name is not None:
        out = [r for r in out if r.project_name == project_name]
    if status is not None:
        out = [r for r in out if r.status == status]
    if parent_task_id is not None:
        out = [r for r in out if r.parent_task_id == parent_task_id]
    return out


# ---------------------------------------------------------------------------
# Submit (async, recommended)
# ---------------------------------------------------------------------------


@router.post("/api/v2/tasks", response_model=TaskSubmitV2Response)
async def submit_task_v2(
    request: TaskSubmitV2,
    state: AppState = Depends(get_state),
):
    """Submit a task asynchronously. Returns immediately with a delegation id.

    Status transitions are published on the WebSocket event bus as
    ``delegation.status_changed`` events. Poll ``GET /api/delegations/{id}``
    for the final state.
    """
    if state.job_runner is None:
        raise HTTPException(503, "JobRunner not initialised")

    # Resolve agent + model via the same path as the legacy /api/tasks.
    # When the user supplies an explicit ``agent``, capture the router's
    # *fallback* decision so the override log can record the discrepancy
    # (M1.2 step 3; see SpecialistResolver routing later when M1.7 ships).
    routed_agent: str | None = None
    routed_model: str | None = None
    if request.agent:
        from pathlib import Path as _P

        agent = request.agent
        # Model precedence (the documented M1.2/M1.4 chain):
        # task_override > specialist.current_model > default. The
        # specialist tier was missing here — chat-dispatched defers
        # always got the config default, silently shadowing the
        # user's per-specialist pick (incident 2026-09-13: paid-tier
        # specialist pick never reached the wire).
        model = request.model
        if model is None:
            from sweave.projects import project_manager

            active = project_manager.get_active_project()
            specialist_rec = state.ensure_specialist_resolver().resolve(
                agent, _P(active.path) if active else None
            )
            if specialist_rec is not None and specialist_rec.current_model:
                model = specialist_rec.public_model()
        if model is None:
            model = state.config_manager.resolve_model(agent)
        # Compute what the router would have picked (for the override log)
        try:
            decision = state.router.route(request.task)
            routed_agent = decision.agent
            routed_model = state.config_manager.resolve_model(decision.agent)
        except Exception:
            pass
    else:
        decision = state.router.route(request.task)
        agent = decision.agent
        model = request.model or decision.model
        routed_agent = agent
        routed_model = model

    # Project pin: explicit request wins, else fall back to the active
    # project so the runner can file the record correctly.
    project_name = request.project_name
    if project_name is None:
        project_name = state.active_project_name()

    # M1.6 step 2: deferral chain validation. When ``parent_task_id`` is
    # set, the new delegation is a child; the DelegationManager enforces
    # depth / loop / budget before we hand off to the JobRunner. The
    # chain rules raise specific ChainError subclasses; we map each to
    # the right HTTP code + a "rejected: <reason>" string so the MCP
    # ``defer`` tool can surface the actionable error verbatim.
    # ``parent`` stays None for top-level submits (the wait-set
    # resolver below treats that as non-chat).
    parent = None
    if request.parent_task_id:
        if state.delegation_manager is None:
            raise HTTPException(503, "delegation manager not initialised")
        # Look up the parent in the per-project stores. The parent's
        # agent name is what we check for loops; its chain_root_id
        # establishes which cache the new delegation lives under.
        from sweave.runtime.delegation_manager import (
            ChainError,
        )

        parent = None
        for store in _all_stores(state):
            parent = store.get(request.parent_task_id)
            if parent is not None:
                break
        if parent is None:
            # Plan ruling: parent is required for defer; a defer with
            # an unknown parent_task_id is a 404 (caller is using a stale
            # id, or the orchestrator's own delegation was deleted).
            raise HTTPException(
                404,
                f"parent delegation '{request.parent_task_id}' not found",
            )
        try:
            new_delegation = state.delegation_manager.validate(
                parent=parent,
                target=agent,
                task=request.task,
                reason=(
                    request.manifest.get("intent", "")
                    if isinstance(request.manifest, dict)
                    else ""
                ),
            )
        except ChainError as e:
            # Incident 2026-09-11 (backend re-dispatch rejected by loop
            # detection): rejections were invisible server-side — the
            # "rejected: ..." line returns to the LLM only, so the next
            # "why was my re-dispatch rejected" is unanswerable from
            # web.log. Log every chain rejection with its code, target
            # and chain root; the HTTP surface is unchanged.
            logger.warning(
                "submit_task_v2: chain %s rejected (parent=%s target=%s "
                "chain_root=%s): %s",
                getattr(e, "code", "chain_error"),
                request.parent_task_id,
                agent,
                parent.chain_root_id or parent.delegation_id,
                e,
            )
            raise HTTPException(409, f"rejected: {e}") from e
    else:
        new_delegation = None

    delegation = await state.job_runner.submit(
        agent=agent,
        task=request.task,
        model=model,
        parent_session_id=request.parent_session_id,
        project_name=project_name,
        parent_task_id=request.parent_task_id,
        manifest=request.manifest,
        depth=new_delegation.depth if new_delegation else 0,
        chain_root_id=new_delegation.chain_root_id if new_delegation else None,
        coordination_tokens=new_delegation.coordination_tokens if new_delegation else 0,
        # M2.0: record-only estimate (absent or all-null normalises to
        # None — "no estimate supplied").
        estimate=(
            request.estimate.model_dump(exclude_none=True) or None
            if request.estimate is not None
            else None
        ),
        # M2.1: wait-set flag. Omitted resolves by parent kind:
        # chat-turn children join the synthesis wait-set by default
        # (2026-09-14 ruling); everything else stays
        # fire-and-forget.
        blocking=resolve_blocking(request.blocking, parent),
        # Step 4: per-task harness override (transient). Unknown
        # names fail fast here so a typo never becomes a mystery
        # default three layers down.
        harness=_validate_harness_override(request.harness),
    )

    # M1.2 step 3: append an override log entry if the user supplied an
    # explicit ``agent`` that differs from the router's decision. The
    # log is observability for R6 dispatch training; never block the
    # submit on it.
    if request.agent and routed_agent and request.agent != routed_agent:
        from pathlib import Path

        from sweave.web.routers.specialists import record_override_if_differing

        proj_dir: Path | None = None
        if project_name is not None:
            from sweave.projects import project_manager

            proj = project_manager.get_project(project_name)
            if proj is not None:
                proj_dir = Path(proj.path)
        await record_override_if_differing(
            state=state,
            project_dir=proj_dir,
            session_id=request.parent_session_id,
            task=request.task,
            routed_agent=routed_agent or "",
            routed_model=routed_model,
            user_agent=agent,
        )

    # M1.12 amendment 2 (2026-09-10): Deferred-turn liveness beacon.
    # When the parent's turn spawned this child, we now know the
    # parent turn was ALIVE at this moment even if opencode sent us
    # NO stream bytes yet — a defer is the one observable we get
    # mid-turn (user ruling: "use the fact that a defer call happened").
    # The beacon rides on the parent's trace file (append-only,
    # schema-free), and JobRunner's turn-cap extension reads its
    # mtime; ALSO emitted as a WS event for the UI's wait indicators.
    if request.parent_task_id and parent is not None:
        try:
            from sweave.runtime.trace_log import TraceLog

            parent_trace = TraceLog(request.parent_task_id)
            parent_trace.append(
                "child_deferred",
                {
                    "child": delegation.delegation_id,
                    "agent": agent,
                    "model": model,
                },
            )
        except Exception as trace_err:  # noqa: BLE001
            logger.warning(
                "submit_task_v2: parent-trace beacon failed: %s", trace_err
            )

    # (child beacon done)
    return TaskSubmitV2Response(
        delegation_id=delegation.delegation_id,
        task_id=delegation.task_id,
        status=delegation.status,
        agent=delegation.agent,
        model=delegation.model,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("/api/delegations")
async def list_delegations(
    project_name: Optional[str] = None,
    status: Optional[str] = None,
    parent_task_id: Optional[str] = None,
    archived: Optional[str] = None,
    include_archived: bool = False,
    state: AppState = Depends(get_state),
):
    """List delegations, newest first. Optional filters:
    ``?project_name=`` / ``?status=`` / ``?parent_task_id=``.

    M1.13 cleanup (ruling 2026-09-11, ARCHIVE-not-delete):

    * ``?archived=`` selects the archive sub-state for the returned
      rows: ``false`` (default — archived records are hidden),
      ``true`` (only archived), ``all`` (everything). Rows each carry
      their ``archived`` bool + ``archived_at`` timestamp.
    * ``?include_archived=true`` adds ``archived_projects`` — compact
      per-project aggregate rows (total / by_status / by_kind /
      archived_at / last_created_at) so the Children tab can render
      the compact Archived group WITHOUT pulling every archived row.
      Rows sourced from live stores (``source: "store"``) are unioned
      with rows from the persisted index (``source: "index"``) for
      projects whose records are no longer reachable; store rows win.
    """
    mode = (archived or "false").strip().lower()
    if mode not in {"true", "false", "all"}:
        raise HTTPException(
            400, "archived must be one of: true, false, all"
        )
    records: list = []
    for store in _all_stores(state):
        records.extend(store.list())
    records = _filter_delegations(
        records,
        project_name=project_name,
        status=status,
        parent_task_id=parent_task_id,
    )
    if mode == "true":
        records = [r for r in records if r.archived]
    elif mode == "false":
        records = [r for r in records if not r.archived]
    records.sort(key=lambda d: d.created_at, reverse=True)
    response: dict = {"delegations": [d.to_dict() for d in records]}
    if include_archived:
        from sweave.runtime.delegation_archive import (
            archived_project_aggregates,
        )

        all_records: list = []
        for store in _all_stores(state):
            all_records.extend(store.list())
        if project_name is not None:
            all_records = [
                r for r in all_records if r.project_name == project_name
            ]
        response["archived_projects"] = archived_project_aggregates(
            all_records, index=getattr(state, "archive_index", None)
        )
    return response


@router.get("/api/delegations/{delegation_id}")
async def get_delegation(
    delegation_id: str, state: AppState = Depends(get_state)
):
    for store in _all_stores(state):
        rec = store.get(delegation_id)
        if rec is not None:
            return rec.to_dict()
    raise HTTPException(404, f"Delegation '{delegation_id}' not found")


@router.get("/api/delegations/{delegation_id}/detail")
async def get_delegation_detail(
    delegation_id: str, state: AppState = Depends(get_state)
):
    """M1.9 step 4: detail view (composed prompt + tool timeline +
    tokens + status timeline).

    The trace JSONL is the source of truth. A missing trace returns
    a minimal record (id + empty sections) -- never a 500. The UI
    detail view reads this endpoint and patches the sections in
    place (M1.8 no-rerender invariant; the same shape the
    ``sweave log`` CLI prints).

    M2.0: the record (estimate + created/completed stamps) is joined
    in for the ``estimate_vs_actual`` section; an id with no record
    (or no trace) still degrades to nulls, never a 500.

    M2.1: the record's ``review_request`` rides the same fold (echoed
    verbatim, None when absent) — the read side of the review
    seam, no new endpoint.

    M2.1-follow-up: the record's ``engine_session_id`` rides the
    same fold (per-delegation display + forensics).

    Review Phase 1 (follow-up spec B, subsumed): the record header
    (status/agent/task/output/error/stamps/blocking/attention) +
    the ``review_bundle`` pointer ride the same fold. Unknown id
    keeps the degrade contract (200 + nulls).

    USAGE_LEDGER Phase 0: the additive ``price`` key rides the same
    fold (record ``model`` + sidecar meta via the state's config
    manager; missing model/rates degrade to nulls, never a 500).
    """
    from sweave.web.detail_view import render_detail_view

    meta_entry: dict | None = None
    model_str: str | None = None
    try:
        model_str = record.model if record is not None else None
        cm = getattr(state, "config_manager", None)
        get_meta = getattr(cm, "get_model_meta", None) if cm else None
        if callable(get_meta) and model_str:
            from sweave.stats.pricing import strip_variant

            meta_entry = get_meta(strip_variant(model_str)) or None
    except Exception:  # noqa: BLE001 -- pricing inputs never fail detail
        meta_entry = None

    record = None
    for store in _all_stores(state):
        record = store.get(delegation_id)
        if record is not None:
            break
    return render_detail_view(
        delegation_id,
        trace_dir=state.traces_dir,
        estimate=record.estimate if record is not None else None,
        created_at=record.created_at if record is not None else None,
        completed_at=record.completed_at if record is not None else None,
        review_request=record.review_request if record is not None else None,
        engine_session_id=(
            record.engine_session_id if record is not None else None
        ),
        record=record.to_dict() if record is not None else None,
        review_bundle=record.review_bundle if record is not None else None,
        model=model_str,
        meta_entry=meta_entry,
    )


# ---------------------------------------------------------------------------
# Optional convenience: block-wait for a delegation to reach a terminal state.
# Subject to the timeout in the query string (default 30s, max 600s).
# ---------------------------------------------------------------------------


@router.post("/api/delegations/{delegation_id}/wait")
async def wait_for_delegation(
    delegation_id: str,
    timeout: float = 30.0,
    state: AppState = Depends(get_state),
):
    if state.job_runner is None:
        raise HTTPException(503, "JobRunner not initialised")
    timeout = max(0.0, min(timeout, 600.0))
    delegation = await state.job_runner.wait(delegation_id, timeout=timeout)
    if delegation is None:
        raise HTTPException(404, f"Delegation '{delegation_id}' not found")
    return delegation.to_dict()


# ---------------------------------------------------------------------------
# M1.4+M1.5 step 3: Human promotion (review -> done)
# ---------------------------------------------------------------------------
#
# A delegation that reaches ``review`` stays there until a human
# promotes it. R2's cross-review will call this same endpoint
# programmatically (the API is the automation seam). The plan
# (``docs/M1_4_5_PLAN.md`` step 3) extends the "human merges" rule
# to lifecycle promotion: the only path to ``done`` is this endpoint.
# ---------------------------------------------------------------------------


@router.post("/api/delegations/{delegation_id}/escalate")
async def escalate_delegation(
    delegation_id: str,
    request: "EscalateRequest",
    state: AppState = Depends(get_state),
):
    """M1.9 step 3 (+ M1.11 kinds): ``ask_human`` / ``escalate``.

    Persists the escalation (one JSON file per asking delegation;
    survives server restarts), publishes
    ``specialist.escalated`` on the WS bus, and flips the asking
    delegation's ``needs_attention`` flag. The Children audit
    surfaces it; blocking ``question`` records hold the chat turn
    open in ChatLoop until answered or skipped (no deadline).

    The MCP server calls this endpoint via ``ask_human`` (kind=
    question, audience=human) and ``escalate`` (kind=escalation,
    audience=orchestrator). The skip path is
    ``POST /api/delegations/{id}/skip {confirmed: true}``.
    """
    if state.escalation_store is None:
        raise HTTPException(503, "EscalationStore not initialised")
    # Verify the asking delegation exists in the global per-project
    # registry (the MCP stdio server is stateless, so a stale id
    # could mean the delegation was deleted or never existed).
    found = False
    for store in _all_stores(state):
        if store.get(delegation_id) is not None:
            found = True
            break
    if not found:
        raise HTTPException(
            404, f"Delegation '{delegation_id}' not found"
        )
    rec = await state.escalation_store.create(
        delegation_id=delegation_id,
        question=request.question,
        options=request.options,
        kind=request.kind or "question",
        audience=request.audience or "human",
    )
    # Flip the asking delegation's needs_attention flag. Best-effort:
    # the persistence is the EscalationStore; the flag is the
    # renderer's cheap read-side indicator.
    for store in _all_stores(state):
        d = store.get(delegation_id)
        if d is not None:
            try:
                await store.update(delegation_id, needs_attention=True)
            except Exception:
                pass
            break
    return {
        "escalation_id": rec["escalation_id"],
        "delegation_id": rec["delegation_id"],
        "deadline_at": rec["deadline_at"],
        "status": rec["status"],
    }


@router.post("/api/delegations/{delegation_id}/answer")
async def answer_delegation(
    delegation_id: str,
    request: "AnswerRequest",
    state: AppState = Depends(get_state),
):
    """M1.9 step 3: record the human's answer to an escalation.

    Returns the updated escalation (status=answered, response=<text>).
    Legacy timeout records (status=timeout, "no answer received")
    stay readable; new questions wait until answered or explicitly
    skipped (``POST …/skip``) rather than timing out.
    """
    if state.escalation_store is None:
        raise HTTPException(503, "EscalationStore not initialised")
    rec = await state.escalation_store.answer(
        delegation_id=delegation_id, response=request.response
    )
    if rec is None:
        raise HTTPException(
            404, f"escalation for delegation '{delegation_id}' not found"
        )
    # Clear the asking delegation's needs_attention flag — unless the
    # delegation still owes attention elsewhere (M2.1 follow-up §A
    # step 1: an unpromoted review keeps the flag; the answer only
    # resolves the question). Same rule as the store flagger
    # (``_review_owes_promotion``) — the store fires first, the loop
    # is defense in depth.
    for store in _all_stores(state):
        d = store.get(delegation_id)
        if d is None:
            continue
        if _review_owes_promotion(d):
            break
        try:
            await store.update(delegation_id, needs_attention=False)
        except Exception:
            pass
        break
    return rec


@router.post("/api/delegations/{delegation_id}/skip")
async def skip_delegation(
    delegation_id: str,
    request: "SkipRequest",
    state: AppState = Depends(get_state),
):
    """M1.11: explicit human skip of a blocking question.

    The opencode-Esc equivalent. ``confirmed`` must be true — the
    UI's system-issued "are you sure?" dialog sets it before
    calling. Unconfirmed calls are rejected (409) so a fat-finger
    tap cannot silently drop the question. Resolves with
    ``status=skipped`` and clears ``needs_attention``; the waiting
    turn proceeds with best judgment.
    """
    if state.escalation_store is None:
        raise HTTPException(503, "EscalationStore not initialised")
    if not request.confirmed:
        raise HTTPException(
            409, "skip requires confirmed=true (system confirm dialog)"
        )
    rec = await state.escalation_store.skip(delegation_id=delegation_id)
    if rec is None:
        raise HTTPException(
            404, f"escalation for delegation '{delegation_id}' not found"
        )
    # Same review-aware rule as answer: the skip resolves the
    # question, but an unpromoted review still owes promotion.
    for store in _all_stores(state):
        d = store.get(delegation_id)
        if d is None:
            continue
        if _review_owes_promotion(d):
            break
        try:
            await store.update(delegation_id, needs_attention=False)
        except Exception:
            pass
        break
    return rec


@router.get("/api/delegations/{delegation_id}/escalation")
async def get_escalation(
    delegation_id: str,
    state: AppState = Depends(get_state),
):
    """M1.9 step 3 (+ M1.11 kinds): read the current escalation.

    Returns the full escalation record (question, options, kind,
    audience, status, response, deadline — ``deadline_at`` is null
    for no-timeout questions) or 404 if none exists.
    """
    if state.escalation_store is None:
        raise HTTPException(503, "EscalationStore not initialised")
    rec = await state.escalation_store.get(delegation_id=delegation_id)
    if rec is None:
        raise HTTPException(
            404, f"escalation for delegation '{delegation_id}' not found"
        )
    return rec


@router.post("/api/delegations/{delegation_id}/promote")
async def promote_delegation(
    delegation_id: str, state: AppState = Depends(get_state)
):
    """Promote a delegation from ``review`` to ``done`` (M1.4+M1.5 step 3).

    Only valid from ``review``; any other status returns 409. The
    delegation store is updated, the trace records ``status_changed``,
    ``delegation.status_changed`` is published on the WS event bus,
    and the bridged ``ChildSession.status`` (if any) is updated to
    ``done`` so the UI's Children tab reflects the new state.

    M2.1 follow-up §A step 1: promotion clears ``needs_attention``
    (the review no longer owes anything) — unless a question is
    still pending, which keeps the flag. The ``review_request`` is
    preserved as history (M2.1 ruling 3).
    """
    # Find the delegation across all known per-project stores.
    for store in _all_stores(state):
        rec = store.get(delegation_id)
        if rec is None:
            continue
        if rec.status != "review":
            raise HTTPException(
                409,
                f"delegation '{delegation_id}' is in status '{rec.status}'; "
                "only 'review' can be promoted to 'done'",
            )
        # Update the store. Use the same field set the runner uses
        # for its own _transition: status, completed_at, updated_at.
        # M2.1 follow-up §A step 1: promotion also clears
        # ``needs_attention`` (set on review entry) unless a question
        # is still pending — the flag means "answer OR promote".
        from datetime import datetime as _dt
        await store.update(
            delegation_id,
            status="done",
            completed_at=_dt.now(),
        )
        cleared = not await _has_pending_escalation(state, delegation_id)
        if cleared:
            await store.update(delegation_id, needs_attention=False)
        # Trace + WS: mirror the runner's _transition vocabulary so
        # observers (UI, R6) get the same shape they already consume.
        if state.event_bus is not None:
            await state.event_bus.publish(
                "delegation.status_changed",
                {
                    "delegation_id": delegation_id,
                    "status": "done",
                    "agent": rec.agent,
                    "task_id": rec.task_id,
                },
            )
        # Trace log: same shape as JobRunner._transition.
        from sweave.runtime.trace_log import TraceLog

        trace = TraceLog(delegation_id, base_dir=state.traces_dir)
        trace.append(
            "status_changed",
            {"status": "done", "agent": rec.agent, "source": "human_promote"},
        )
        if cleared:
            trace.append(
                "attention_flag",
                {
                    "delegation_id": delegation_id,
                    "value": False,
                    "source": "human_promote",
                },
            )
        trace.close()
        # Worktree isolation lifecycle: human promotion to done
        # retires the task tree like the runner's own settle path
        # (review kept it for inspection; the branch is kept).
        # Best-effort via the runner helper; never fails promotion.
        try:
            runner = getattr(state, "job_runner", None)
            remover = getattr(runner, "_remove_task_worktree", None)
            if remover is not None:
                from sweave.runtime.trace_log import TraceLog as _TraceLog

                await remover(
                    rec,
                    _TraceLog(delegation_id, base_dir=state.traces_dir),
                )
        except Exception:  # noqa: BLE001
            pass
        # UI v1 compat bridge: update the ChildSession.status in the
        # parent session so the Children tab re-renders. The bridge
        # write-through is best-effort: a missing parent (orphan
        # delegation) leaves the child stale; R4 removes the bridge.
        _sync_bridged_child_status(state, delegation_id, "done")
        return store.get(delegation_id).to_dict()  # type: ignore[union-attr]
    raise HTTPException(404, f"Delegation '{delegation_id}' not found")


def _sync_bridged_child_status(
    state: AppState, delegation_id: str, new_status: str
) -> None:
    """Update the bridged ChildSession.status for a promotion.

    The runner wrote a ``ChildSession`` carrying ``delegation_id`` on
    submit (M1.1 step 4 bridge). The Children tab reads from the
    session, not the delegation directly, so a status change on the
    delegation needs to be mirrored back to the child entry for the
    UI to update.

    Walk every session known to the project manager; for the one whose
    ``children`` includes a child with our ``delegation_id``, set its
    status and persist. The walk is cheap (sessions are in-memory; the
    typical project has one or two active sessions at a time) and
    avoids needing to thread the parent_session_id through the
    delegation record.
    """
    from sweave.projects import project_manager

    if project_manager is None:
        return
    for proj in project_manager.list_projects():
        for session in project_manager.list_sessions(proj.name):
            mutated = False
            for child in session.children:
                if child.delegation_id == delegation_id:
                    child.status = new_status
                    mutated = True
            if mutated:
                project_manager.save_session(session)


# ---------------------------------------------------------------------------
# SubAgentRun (M1.1 step 4): ephemeral, capped. R2's /investigate is the
# primary consumer; M1.1 ships the API surface but no orchestrator-side
# caller yet. ``project_name`` is the same default-as-v2-task contract
# so the run lands in the active project's audit trail (R2 may consume
# the project_name to scope the read).
# ---------------------------------------------------------------------------


@router.post("/api/subagent-runs")
async def start_subagent_run(
    request: SubAgentRunStart, state: AppState = Depends(get_state)
):
    if state.subagent_runs is None:
        raise HTTPException(503, "SubAgentRunStore not initialised")
    run = SubAgentRun(
        agent=request.agent,
        purpose=request.purpose,
        parent_session_id=request.parent_session_id,
        project_name=request.project_name,
        status="running",
    )
    await state.subagent_runs.add(run)
    return run.to_dict()


@router.get("/api/subagent-runs")
async def list_subagent_runs(
    agent: Optional[str] = None,
    purpose: Optional[str] = None,
    status: Optional[str] = None,
    state: AppState = Depends(get_state),
):
    if state.subagent_runs is None:
        return {"runs": []}
    runs = state.subagent_runs.list()
    if agent is not None:
        runs = [r for r in runs if r.agent == agent]
    if purpose is not None:
        runs = [r for r in runs if r.purpose == purpose]
    if status is not None:
        runs = [r for r in runs if r.status == status]
    return {"runs": [r.to_dict() for r in runs]}


@router.get("/api/subagent-runs/{run_id}")
async def get_subagent_run(run_id: str, state: AppState = Depends(get_state)):
    if state.subagent_runs is None:
        raise HTTPException(503, "SubAgentRunStore not initialised")
    run = state.subagent_runs.get(run_id)
    if run is None:
        raise HTTPException(404, f"SubAgentRun '{run_id}' not found")
    return run.to_dict()


@router.post("/api/subagent-runs/{run_id}/finish")
async def finish_subagent_run(
    run_id: str, request: SubAgentRunFinish, state: AppState = Depends(get_state)
):
    if state.subagent_runs is None:
        raise HTTPException(503, "SubAgentRunStore not initialised")
    run = await state.subagent_runs.update(
        run_id,
        status=request.status,
        output_summary=request.output_summary,
        finished_at=datetime.now(),
    )
    if run is None:
        raise HTTPException(404, f"SubAgentRun '{run_id}' not found")
    return run.to_dict()
