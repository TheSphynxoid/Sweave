/**
 * R4.1 step 2: WS-event -> query-key invalidation contract.
 *
 * The AppProvider subscribes to five WS events and invalidates
 * the smallest set of React Query keys that need to refresh.
 * The mapping is extracted to ``wsInvalidations.ts`` (a pure
 * function) so the contract is testable without React or a
 * bus. These tests pin the contract; the AppProvider is the
 * wiring.
 */
import { describe, it, expect } from "vitest";
import { invalidationsForEvent } from "../wsInvalidations";

describe("invalidationsForEvent", () => {
  it("project.created invalidates the projects list", () => {
    const keys = invalidationsForEvent({
      event: "project.created",
      data: { name: "p1", path: "/tmp/p1" },
    });
    expect(keys).toEqual([["projects"]]);
  });

  it("project.deleted invalidates the projects list + that project's sessions", () => {
    const keys = invalidationsForEvent({
      event: "project.deleted",
      data: { name: "p1" },
    });
    expect(keys).toEqual([["projects"], ["sessions", "p1"]]);
  });

  it("project.deleted falls back to just ['projects'] when name is missing", () => {
    const keys = invalidationsForEvent({
      event: "project.deleted",
      data: {},
    });
    expect(keys).toEqual([["projects"]]);
  });

  it("session.created invalidates only the owning project's session list", () => {
    const keys = invalidationsForEvent({
      event: "session.created",
      data: { id: "s1", name: "x", project_name: "p1" },
    });
    expect(keys).toEqual([["sessions", "p1"]]);
  });

  it("session.deleted invalidates only the owning project's session list", () => {
    const keys = invalidationsForEvent({
      event: "session.deleted",
      data: { id: "s1", project_name: "p1" },
    });
    expect(keys).toEqual([["sessions", "p1"]]);
  });

  it("session.renamed invalidates only the owning project's session list", () => {
    const keys = invalidationsForEvent({
      event: "session.renamed",
      data: { id: "s1", name: "New title", project_name: "p1" },
    });
    expect(keys).toEqual([["sessions", "p1"]]);
  });

  it("session.renamed with no project_name invalidates nothing", () => {
    const keys = invalidationsForEvent({
      event: "session.renamed",
      data: { id: "s1", name: "New title" },
    });
    expect(keys).toEqual([]);
  });

  it("session.created with no project_name invalidates nothing", () => {
    // Defensive: a malformed event should not cascade-invalidate
    // the entire query cache. Returning an empty list keeps the
    // contract uniform.
    const keys = invalidationsForEvent({
      event: "session.created",
      data: { id: "s1", name: "x" },
    });
    expect(keys).toEqual([]);
  });

  it("active_session.changed invalidates no query keys (the AppProvider refetches its own state)", () => {
    const keys = invalidationsForEvent({
      event: "active_session.changed",
      data: { id: "s1", project_name: "p1" },
    });
    expect(keys).toEqual([]);
  });

  it("unknown events invalidate nothing", () => {
    const keys = invalidationsForEvent({
      event: "delegation.status_changed",
      data: { delegation_id: "d1" },
    });
    expect(keys).toEqual([]);
  });

  it("the session events for different projects do not cross-invalidate", () => {
    // The R4.1 contract: a session event for project p1 must
    // not touch project p2's session list. The sidebar's
    // session tree is scoped to the active project; a
    // cross-invalidation would cause a no-op fetch and waste
    // a render cycle.
    const a = invalidationsForEvent({
      event: "session.created",
      data: { id: "s-a", name: "a", project_name: "p-a" },
    });
    const b = invalidationsForEvent({
      event: "session.deleted",
      data: { id: "s-b", project_name: "p-b" },
    });
    expect(a).toEqual([["sessions", "p-a"]]);
    expect(b).toEqual([["sessions", "p-b"]]);
  });
});
