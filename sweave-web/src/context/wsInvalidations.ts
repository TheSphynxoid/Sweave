/**
 * R4.1 Step 2: WS event -> query-key invalidation contract.
 *
 * The AppProvider subscribes to the step-1b WS events and
 * invalidates the smallest set of React Query keys that need to
 * refresh. This module extracts the mapping as a pure
 * function so the contract is testable without spinning up
 * React + the bus.
 *
 * The function returns a list of query keys to invalidate;
 * the AppProvider (or any test consumer) iterates and calls
 * ``queryClient.invalidateQueries({ queryKey })`` for each.
 *
 * The mapping:
 *   project.created        -> ["projects"]
 *   project.deleted        -> ["projects"], ["sessions", <name>]
 *   session.created        -> ["sessions", <project_name>]
 *   session.deleted        -> ["sessions", <project_name>]
 *   session.renamed        -> ["sessions", <project_name>]
 *   active_session.changed -> [] (the AppProvider refetches
 *                              the active session itself; the
 *                              queries are not affected)
 *
 * The function is pure -- no side effects, no React imports.
 * Test consumers can call it directly with a synthetic event
 * envelope and assert on the returned key list.
 */

export type WSEventName =
  | "project.created"
  | "project.deleted"
  | "session.created"
  | "session.deleted"
  | "session.renamed"
  | "active_session.changed";

export interface WSEventLike {
  event: string;
  data: Record<string, unknown>;
}

export function invalidationsForEvent(env: WSEventLike): ReadonlyArray<readonly unknown[]> {
  switch (env.event) {
    case "project.created": {
      return [["projects"]];
    }
    case "project.deleted": {
      const name = env.data?.name;
      if (typeof name === "string" && name) {
        return [["projects"], ["sessions", name]];
      }
      return [["projects"]];
    }
    case "session.created":
    case "session.deleted":
    case "session.renamed": {
      const projectName = env.data?.project_name;
      if (typeof projectName === "string" && projectName) {
        return [["sessions", projectName]];
      }
      return [];
    }
    case "active_session.changed": {
      // No React Query key to invalidate here -- the
      // AppProvider refetches /sessions/active itself and
      // patches its own state. Returning an empty list keeps
      // the contract uniform: callers iterate the list and
      // skip when empty.
      return [];
    }
    default: {
      // Unknown events are ignored (the AppProvider only
      // subscribes to the six known names, but defensiveness
      // is cheap and the test covers it).
      return [];
    }
  }
}
