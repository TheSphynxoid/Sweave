"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronRight, Folder, FolderPlus, Check } from "lucide-react";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";

interface DirEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

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
  const [current, setCurrent] = useState(value || "");
  const [entries, setEntries] = useState<DirEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  const load = useCallback(async (path: string) => {
    setLoading(true);
    try {
      const res = await api.listDirectory(path);
      setEntries(res.entries.filter((e) => e.is_dir));
      setCurrent(res.path);
    } catch {
      // ignore — invalid path
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      void load(value || "C:\\" );
    }
  }, [open, value, load]);

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      const res = await api.createDirectory(current, name);
      setNewName("");
      setCreating(false);
      await load(res.path);
    } catch {
      // ignore
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex w-full items-center gap-2 rounded-md border border-input bg-background px-3 py-2 text-left text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Folder size={14} className="shrink-0 text-muted-foreground" />
          <span className="truncate flex-1 font-mono text-xs">
            {value || placeholder}
          </span>
          <span className="text-xs text-muted-foreground">Browse</span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[420px] p-0">
        <div className="flex items-center gap-1 border-b border-border px-3 py-2 text-xs text-muted-foreground">
          <Folder size={12} className="shrink-0" />
          <span className="truncate font-mono">{current}</span>
        </div>
        <ScrollArea className="h-56">
          <div className="p-1">
            {loading && (
              <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                Loading…
              </div>
            )}
            {!loading && entries.length === 0 && (
              <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                No subfolders
              </div>
            )}
            {!loading &&
              entries.map((e) => (
                <button
                  key={e.path}
                  type="button"
                  onClick={() => load(e.path)}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm text-left hover:bg-muted"
                >
                  <ChevronRight size={14} className="shrink-0 text-muted-foreground" />
                  <Folder size={14} className="shrink-0 text-muted-foreground" />
                  <span className="truncate">{e.name}</span>
                </button>
              ))}
          </div>
        </ScrollArea>
        <div className="border-t border-border p-2 space-y-2">
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
                className="h-8"
                onClick={() => void load(current)}
              >
                Open here
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-8 gap-1"
                onClick={() => {
                  onChange(current);
                  setOpen(false);
                }}
              >
                <Check size={14} /> Select this folder
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label="New folder"
                onClick={() => setCreating(true)}
              >
                <FolderPlus size={14} />
              </Button>
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
