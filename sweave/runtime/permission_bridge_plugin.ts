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
// Supervisor step 4 (2026-09-15): the same ferry carries
// `tool.execute.before` as activity pulses (POST
// /api/activity/tool-started). The opencode SSE bus is silent
// mid-tool (probe_bus_inventory: heartbeats only across a 150s
// tool window), so the supervisor's pulse layer would be blind on
// the opencode path without an in-process sensor. The ferry posts
// tool NAME + compact target only (command/filePath head,
// truncated — never file contents); Sweave attributes via its
// session registry and appends a `tool.started` trace pulse the
// supervisor already counts (zero supervisor code change by
// design).
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
    "tool.execute.before": async (input: any, output?: any) => {
      // Activity pulse (supervisor step 4): tool NAME + compact
      // target only. input shape varies by opencode version —
      // extract defensively; a missing session id degrades to a
      // server-side no-op (200 noted:false), never a throw (the
      // hook must not break or slow the tool call).
      try {
        const tool = String(input?.tool ?? output?.tool ?? "unknown");
        // Args shape varies by opencode version and call shape (the
        // docs example mutates output.args, but reads have arrived
        // empty there live) — merge every plausible location, first
        // hit wins. Head-truncated; never file contents.
        const args = {
          ...((input?.args ?? {}) as Record<string, unknown>),
          ...((output?.args ?? {}) as Record<string, unknown>),
        };
        const rawTarget =
          args.command ?? args.filePath ?? args.filepath ?? args.path
          ?? args.pattern ?? args.url ?? "";
        const target = String(rawTarget).slice(0, 200);
        const sessionID =
          input?.sessionID ?? input?.sessionId ?? input?.session_id ?? null;
        const token =
          typeof process !== "undefined" ? process.env.SWEAVE_MCP_TOKEN : undefined;
        fetch(`${sweaveUrl}/api/activity/tool-started`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "X-Sweave-MCP-Token": token || "",
          },
          body: JSON.stringify({
            session_id: sessionID,
            tool,
            target,
          }),
        }).catch(() => {});
      } catch {
        // never break the host
      }
    },
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
