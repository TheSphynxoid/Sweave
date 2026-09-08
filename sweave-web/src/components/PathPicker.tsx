"use client";

/**
 * PathPicker (R4.2 step 2-pre polish round, 2026-09-08).
 *
 * Server-side folder browser for project creation. The old picker was
 * click-only: no path typing, no way back up (reopening reset to C:\),
 * the drives endpoint was never used, and the 224px viewport made
 * scrolling painful on real drives. This version adds:
 *
 *   - an editable path field (type/paste an absolute path + Enter; the
 *     backend expanduser()s, so "~" and "~\\code" work)
 *   - clickable breadcrumbs (jump to any ancestor) + an Up button
 *     (the listing response carries `parent`)
 *   - drive chips from GET /api/fs/drives + a Home chip (~)
 *   - type-to-filter over the current listing
 *   - a taller list with native scrolling (the styled webkit scrollbar)
 *   - a stale-response guard (rapid clicks can't race two loads)
 *
 * Backend contract unchanged: /api/fs/drives, /api/fs/list,
 * /api/fs/create.
 *
 * NOTE: `PathPickerBrowser` (the whole panel) is exported so unit
 * tests can mount it directly — the bare Radix popover hangs jsdom in
 * this dependency tree (see docs/GOTCHAS.md, "assistant-ui 0.15
 * primitives" -> testing note). The Popover shell is browser-only.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Check,
  ChevronRight,
  CornerDownLeft,
  Folder,
  FolderPlus,
  Home,
  HardDrive,
} from "lucide-react";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

interface DirEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

/** Split an absolute Windows/posix path into cumulative clickable segments. */
export function pathSegments(path: string): { label: string; path: string }[] {
  const isWin = /^[A-Za-z]:/.test(path);
  const sep = isWin ? "\\" : "/";
  const parts = path.split(/[\\/]+/).filter(Boolean);
  if (parts.length === 0) return [{ label: "/", path: "/" }];
  const segs: { label: string; path: string }[] = [];
  let acc = "";
  parts.forEach((part, i) => {
    if (i === 0) acc = isWin ? part + "\\" : "/" + part;
    else acc = acc.endsWith(sep) ? acc + part : acc + sep + part;
    segs.push({ label: part, path: acc });
  });
  return segs;
}

// ---------------------------------------------------------------------------
// Browser panel (the popover body; also the unit-test surface)
// ---------------------------------------------------------------------------

export function PathPickerBrowser({
  initialPath,
  onSelect,
  testPrefix = "path-picker",
}: {
  initialPath: string;
  /** Fired by the "Select this folder" button. */
  onSelect: (path: string) => void;
  /** data-testid prefix; the popover differs only visually. */
  testPrefix?: string;
}) {
  const [current, setCurrent] = useState(initialPath);
  const [parent, setParent] = useState<string | null>(null);
  const [entries, setEntries] = useState<DirEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [pathDraft, setPathDraft] = useState(initialPath);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [drives, setDrives] = useState<DirEntry[]>([]);
  // Monotonic request id: only the latest load may touch state.
  const loadIdRef = useRef(0);

  const load = useCallback(async (path: string) => {
    const id = ++loadIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await api.listDirectory(path);
      if (id !== loadIdRef.current) return; // stale
      setEntries(res.entries.filter((e) => e.is_dir && e.name !== ".."));
      setCurrent(res.path);
      setParent(res.parent);
      setPathDraft(res.path);
      setFilter("");
    } catch (err) {
      if (id !== loadIdRef.current) return;
      setError((err as Error).message || "Cannot open that path");
    } finally {
      if (id === loadIdRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void api
      .listDrives()
      .then((d) => setDrives(d))
      .catch(() => setDrives([]));
    void load(initialPath);
  }, [initialPath, load]);

  const filtered = filter
    ? entries.filter((e) => e.name.toLowerCase().includes(filter.toLowerCase()))
    : entries;

  const commitPathDraft = () => {
    const p = pathDraft.trim();
    if (p && p !== current) void load(p);
  };

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      const res = await api.createDirectory(current, name);
      setNewName("");
      setCreating(false);
      await load(res.path);
    } catch {
      setError("Could not create that folder");
    }
  };

  const crumbs = pathSegments(current || "C:\\");

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Breadcrumbs + jump-to-ancestor */}
      <div
        className="flex items-center gap-0.5 overflow-x-auto border-b border-border px-2 py-1.5 text-xs scrollbar-thin"
        data-testid={`${testPrefix}-breadcrumbs`}
      >
        {crumbs.map((seg, i) => (
          <span key={seg.path} className="flex shrink-0 items-center">
            {i > 0 && <ChevronRight size={12} className="mx-0.5 text-muted-foreground/50" />}
            <button
              type="button"
              onClick={() => void load(seg.path)}
              className={
                i === crumbs.length - 1
                  ? "rounded px-1 py-0.5 font-mono text-foreground"
                  : "rounded px-1 py-0.5 font-mono text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              }
            >
              {seg.label}
            </button>
          </span>
        ))}
        <span className="flex-1" />
        <button
          type="button"
          aria-label="Up one level"
          data-testid={`${testPrefix}-up`}
          disabled={!parent}
          onClick={() => parent && void load(parent)}
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-30"
        >
          <ArrowUp size={13} />
        </button>
      </div>

      {/* Type / paste an absolute path + Enter */}
      <div className="flex items-center gap-1.5 border-b border-border px-2 py-1.5">
        <Input
          value={pathDraft}
          onChange={(e) => setPathDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitPathDraft();
          }}
          data-testid={`${testPrefix}-input`}
          placeholder="Type a path and press Enter (try ~)"
          className="h-7 flex-1 border-0 bg-transparent px-1 font-mono text-xs shadow-none focus-visible:ring-0"
          spellCheck={false}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-6 w-6 shrink-0"
          aria-label="Open typed path"
          onClick={commitPathDraft}
        >
          <CornerDownLeft size={13} />
        </Button>
      </div>

      {/* Drives + quick jumps */}
      <div className="flex items-center gap-1 border-b border-border px-2 py-1.5">
        {drives.map((d) => (
          <button
            key={d.path}
            type="button"
            onClick={() => void load(d.path)}
            className="flex h-6 items-center gap-1 rounded border border-border bg-background px-1.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <HardDrive size={11} />
            {d.name}
          </button>
        ))}
        <button
          type="button"
          onClick={() => void load("~")}
          className="flex h-6 items-center gap-1 rounded border border-border bg-background px-1.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Home size={11} />
          Home
        </button>
      </div>

      {/* Filter over the current listing */}
      <div className="border-b border-border px-2 py-1.5">
        <Input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          data-testid={`${testPrefix}-filter`}
          placeholder="Filter folders…"
          className="h-7 text-xs"
        />
      </div>

      {/* Listing (native scroll — faster wheel than the radix viewport);
          flexes to the popover's available height. */}
      <div
        className="flex-1 overflow-y-auto scrollbar-thin"
        data-testid={`${testPrefix}-list`}
      >
        {loading && (
          <div className="space-y-1 p-2" aria-busy="true">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="flex items-center gap-2 px-1.5 py-1.5">
                <div className="h-3.5 w-3.5 animate-pulse rounded bg-muted" />
                <div className="h-3 flex-1 animate-pulse rounded bg-muted" />
              </div>
            ))}
          </div>
        )}
        {!loading && error && (
          <div
            className="px-3 py-6 text-center text-xs text-destructive"
            data-testid={`${testPrefix}-error`}
          >
            {error}
          </div>
        )}
        {!loading && !error && filtered.length === 0 && (
          <div className="px-3 py-6 text-center text-xs text-muted-foreground">
            {filter ? `No folders match "${filter}"` : "No subfolders"}
          </div>
        )}
        {!loading &&
          filtered.map((e) => (
            <button
              key={e.path}
              type="button"
              data-testid={`${testPrefix}-row-${e.name}`}
              onClick={() => void load(e.path)}
              className="flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-sm text-left transition-colors hover:bg-muted"
            >
              <Folder size={14} className="shrink-0 text-muted-foreground" />
              <span className="truncate">{e.name}</span>
              <ChevronRight size={13} className="ml-auto shrink-0 text-muted-foreground/40" />
            </button>
          ))}
      </div>

      <div className="space-y-2 border-t border-border p-2">
        {error && !loading && (
          <p className="text-[11px] text-muted-foreground">
            Still on <span className="font-mono">{current}</span> — check the typed path.
          </p>
        )}
        {creating ? (
          <div className="flex items-center gap-2">
            <Input
              autoFocus
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleCreate();
                if (e.key === "Escape") setCreating(false);
              }}
              placeholder="New folder name"
              className="h-8 text-xs"
            />
            <Button size="sm" className="h-8" onClick={() => void handleCreate()}>
              Create
            </Button>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="h-8 gap-1"
              onClick={() => setCreating(true)}
            >
              <FolderPlus size={13} /> New folder
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1"
              data-testid={`${testPrefix}-select`}
              onClick={() => onSelect(current)}
            >
              <Check size={14} /> Select this folder
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Popover shell (browser-only; do not mount in jsdom tests)
// ---------------------------------------------------------------------------

export function PathPicker({
  value,
  onChange,
  placeholder = "Select a directory on the server",
}: {
  value: string;
  onChange: (path: string) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="path-picker-trigger"
          className="flex w-full items-center gap-2 rounded-md border border-input bg-background px-3 py-2 text-left text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Folder size={14} className="shrink-0 text-muted-foreground" />
          <span className="truncate flex-1 font-mono text-xs">
            {value || placeholder}
          </span>
          <span className="text-xs text-muted-foreground">Browse</span>
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        sideOffset={6}
        className="flex max-h-[var(--radix-popover-content-available-height)] w-[min(560px,calc(100vw-2rem))] flex-col overflow-hidden p-0"
      >
        <PathPickerBrowser
          initialPath={value || "C:\\"}
          onSelect={(p) => {
            onChange(p);
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
