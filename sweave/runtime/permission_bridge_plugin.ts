// Sweave permission bridge plugin (M1.12 amendment 1, user-locked
// 2026-09-10). Loaded ONLY by opencode serves sweave spawns: the
// serve is spawned with OPENCODE_CONFIG + OPENCODE_CONFIG_DIR pointing
// into the sweave-owned config island (~/.sweave/opencode/), so a
// standalone opencode in the same directory never loads or executes
// this file.
//
// Contract: ferry opencode `permission.asked` events to the Sweave
// HTTP endpoint (POST /api/permission/hijack). Sweave does everything
// else — scope evaluation against the project record, auto-allow in
// scope, a blocking human question out of scope, and the reply POST
// back to the pinned wire (`POST /session/{sid}/permissions/{rid}`).
// The plugin never sends the reply itself (it does not know the
// serve's URL).
//
// Failure posture (user ruling): this plugin NEVER breaks the host.
// A dead Sweave server failing the fetch surfaces as a logged error
// only; the out-of-band stall-branch ask-dance
// (SpecialistRuntime._resolve_pending_permission) remains the
// fallback path.
//
// NOTE on logging: plugin console.* output does not appear in the
// serve stdout or the opencode log file on 1.18.29; the observable
// channel is the SDK `client.app.log`.

const sweaveUrl =
  typeof process !== "undefined" && process.env.SWEAVE_HOST
    ? `http://${process.env.SWEAVE_HOST}:${process.env.SWEAVE_PORT || "8100"}`
    : `http://127.0.0.1:${process.env.SWEAVE_PORT || "8100"}`;

export const SweavePermissionPlugin = async ({ client }: { client?: any }) => {
  const log = (message: string) => {
    try {
      const p = (client as unknown as {
        app?: { log?: (args: { body: unknown }) => unknown | Promise<unknown> };
      })?.app?.log;
      if (typeof p === "function") {
        void Promise.resolve(
          p.call(client.app, {
            body: { service: "sweave-permission", level: "info", message },
          })
        ).catch(() => {});
      }
    } catch {
      // logging must never break the host
    }
  };
  log(
    `plugin initialised url=${sweaveUrl} ` +
      `host_env=${(process.env.SWEAVE_HOST ?? "<unset>") as string} ` +
      `port_env=${(process.env.SWEAVE_PORT ?? "<unset>") as string} ` +
      `token_set=${Boolean(process.env.SWEAVE_MCP_TOKEN)}`,
  );
  return {
    event: async ({ event }: { event: { type: string; properties?: Record<string, unknown> } }) => {
      if (!event || event.type !== "permission.asked") return;
      const props = (event.properties || {}) as Record<string, unknown>;
      log(`forwarding ask ${String(props.id)}`);
      const token =
        typeof process !== "undefined" ? process.env.SWEAVE_MCP_TOKEN : undefined;
      // Fire-and-forget: Sweave blocks on the scope decision / the
      // human answer; blocking an internal opencode event handler on
      // that would stall unrelated events.
      fetch(`${sweaveUrl}/api/permission/hijack`, {
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
      })
        .then((resp: Response) => {
          if (resp.status !== 200) {
            return resp.text().then((t: string) => {
              log(`forward status=${resp.status} body=${t.slice(0, 400)}`);
            });
          }
          log(`forward status=${resp.status}`);
        })
        .catch((err: unknown) => {
          log(`forward failed: ${String(err)}`);
        });
    },
  };
};
