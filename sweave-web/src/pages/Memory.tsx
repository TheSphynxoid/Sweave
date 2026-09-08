"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Brain, Search, Save, Loader2, Database } from "lucide-react";
import { api } from "@/api/client";
import { useApp } from "@/context/AppProvider";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";

interface MemoryBank {
  id: string;
  scope: string;
  name: string;
}

export function MemoryPage() {
  const { activeProject } = useApp();

  const { data: banks = [], isLoading } = useQuery<MemoryBank[]>({
    queryKey: ["memory-banks"],
    queryFn: () => api.getMemoryBanks().then((r: { banks: MemoryBank[] }) => r.banks),
  });

  if (!activeProject) {
    return (
      <div className="p-6" data-testid="page-memory">
        <EmptyState
          icon={<Brain size={20} />}
          title="No project active"
          description="Activate a project to inspect its memory banks."
        />
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6" data-testid="page-memory">
      <PageHeader
        title="Memory"
        description="Recall, reflect and retain across the three memory banks."
        icon={<Brain size={20} />}
      />

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-3" data-testid="memory-loading">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-3">
              <div className="flex items-center gap-3">
                <Skeleton className="h-9 w-9 rounded-lg" />
                <Skeleton className="h-3.5 w-28" />
              </div>
              <Skeleton className="h-2.5 w-full" />
              <Skeleton className="h-2.5 w-2/3" />
              <Skeleton className="h-16 w-full rounded-lg" />
              <Skeleton className="h-8 w-full rounded-lg" />
            </div>
          ))}
        </div>
      ) : banks.length === 0 ? (
        <EmptyState
          icon={<Database size={20} />}
          title="No memory banks"
          description="Memory banks appear here once a project is active."
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-3">
          {banks.map((bank) => (
            <MemoryBankCard key={bank.id} bank={bank} />
          ))}
        </div>
      )}
    </div>
  );
}

function MemoryBankCard({ bank }: { bank: MemoryBank }) {
  const { pushNotification } = useApp();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<unknown>(null);
  const [searching, setSearching] = useState(false);
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);

  const recall = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      const res = await api.recallMemory(query.trim(), bank.id);
      setResults(res);
    } catch (err) {
      pushNotification("error", `Recall failed: ${(err as Error).message}`);
    } finally {
      setSearching(false);
    }
  };

  const retain = async () => {
    if (!content.trim()) return;
    setSaving(true);
    try {
      await api.retainMemory(content.trim(), bank.id);
      pushNotification("success", `Saved to ${bank.name}.`);
      setContent("");
    } catch (err) {
      pushNotification("error", `Retain failed: ${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm font-medium">{bank.name}</CardTitle>
          <Badge variant="muted" className="capitalize">
            {bank.scope}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="flex-1 space-y-3">
        <Tabs defaultValue="recall">
          <TabsList className="grid grid-cols-2">
            <TabsTrigger value="recall">Recall</TabsTrigger>
            <TabsTrigger value="retain">Retain</TabsTrigger>
          </TabsList>
          <TabsContent value="recall" className="space-y-2">
            <div className="flex gap-2">
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && recall()}
                placeholder="Search this bank…"
                className="h-8 text-xs"
              />
              <Button size="sm" className="h-8" onClick={() => void recall()} disabled={searching}>
                {searching ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
              </Button>
            </div>
            {results != null && (
              <pre className="max-h-48 overflow-auto rounded-md border border-border bg-muted/40 p-2 text-[11px] text-foreground/80 whitespace-pre-wrap">
                {safeStringify(results)}
              </pre>
            )}
          </TabsContent>
          <TabsContent value="retain" className="space-y-2">
            <Textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="Write something to remember…"
              className="min-h-[80px] text-xs"
            />
            <Button size="sm" className="w-full" onClick={() => void retain()} disabled={saving}>
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
              Save to bank
            </Button>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}

function safeStringify(v: unknown): string {
  try {
    return typeof v === "string" ? v : JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
