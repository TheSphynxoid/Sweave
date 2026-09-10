// Sweave permission bridge plugin (M1.12 amendment 1, user-locked
// 2026-09-10). Loaded ONLY by opencode serves sweave spawns: the
// serve is spawned with OPENCODE_CONFIG_DIR pointing into the
// sweave-owned config island (~/.sweave/opencode/), so a standalone
// opencode in the same directory never loads or executes this file.
//
// Contract: ferry opencode `permission.asked` bus events to the
// Sweave HTTP endpoint (POST /api/permission/hijack). Sweave does
// everything else — scope evaluation against the project record,
// auto-allow in scope, a blocking human question out of scope, and
// the reply POST back to the pinned wire
// (`POST /session/{sid}/permissions/{rid}`). The plugin never sends
// the reply itself (it does not know the serve's URL).
//
// Failure posture (user ruling): this plugin NEVER breaks the host.
// A dead Sweave server failing the fetch surfaces as plugin error
// logging only; the out-of-band stall-branch ask-dance
// (SpecialistRuntime._resolve_pending_permission) remains the
// fallback path.

const sweaveUrl =
  (typeof process !== "undefined" && process.env.SWEAVE_HOST
    ? `http://${process.env.SWEAVE_HOST}:${process.env.SWEAVE_PORT || "8100"}`
    : `http://127.0.0.1:${process.env.SWEAVE_PORT || "8100"}`);

export const SweavePermissionPlugin = async () => {
  return {
    event: async ({ event }: { event: { type: string; properties?: Record<string, unknown> } }) => {
      if (!event || event.type !== "permission.asked") return;
      const props = (event.properties || {}) as Record<string, unknown>;
      const token =
        typeof process !== "undefined" ? process.env.SWEAVE_MCP_TOKEN : undefined;
      // Fire-and-forget: Sweave blocks on the scope decision / the
      // human answer; blocking an internal opencode event handler on
      // that would stall unrelated events. Delivery failures are
      // surfaced via console.error (host-safe).
      void fetch(`${sweaveUrl}/api/permission/hijack`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "X-Sweave-MCP-Token": token || "",
          },
          body: JSON.stringify({
            session_id: props.sessionID,
            request_id: props.id,
            permission: props.permission,
            patterns: props.patterns,
            metadata: props.metadata,
            always: props.always,
          }),
        }).catch((err: unknown) => {
          console.error("sweave-permission bridge: forward failed", err);
        });
      } catch (err) {
        console.error("sweave-permission bridge: malformed ask event", err);
      }
    },
  };
};
