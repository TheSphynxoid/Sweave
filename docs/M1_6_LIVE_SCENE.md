# M1.6 live mini-scene: orchestrator deferral chain

Date: 2026-09-04. Server: SWEAVE_MOCK_OPENCODE=1, port 8100.

## What we exercise end-to-end

1. Active project + session.
2. Create a parent (orchestrator) delegation directly via the
   JobRunner path (no LLM call needed for the live scene — we
   drive the chain by submitting the parent + a defer child
   separately and assert the chain plumbing).
3. Submit a child via the MCP `defer` tool (against the running
   server) with `caller_delegation_id` = the parent. The MCP
   server is a thin client of `/api/v2/tasks`, so we POST
   directly to the v2 endpoint with `parent_task_id` set.
4. Assert the child delegation's `depth=1`, `chain_root_id=parent.id`.
5. Parent stays in `running` while child is open. Mark child as
   `done` via the v2 task wait + a store.update (manual gate
   trigger). Parent advances to `review` once the gate resolves.
6. Trace the full chain via `~/.sweave/traces/{id}.jsonl`.

## Running the scene

$env:SWEAVE_MOCK_OPENCODE="1"
python start_server.py 8100 127.0.0.1

# in another shell
python scripts/m1_6_live_scene.py

## Expected

* The script prints the parent + child delegation records with
  their v3 fields. The trace file shows the full chain: parent
  `prompt_sent` -> `status_changed: running` -> child's events
  -> parent `children_settled` -> parent `status_changed: review`.
* Process count stable (no leaks); serve TTL armed.
* `python -m pytest tests/` still 336/336 green.
