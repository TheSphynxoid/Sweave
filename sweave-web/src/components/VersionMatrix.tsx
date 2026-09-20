"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

const ROWS: { key: "backend" | "engine" | "web" | "engine_protocol" | "delegation_schema"; label: string }[] = [
  { key: "backend", label: "Backend" },
  { key: "engine", label: "Engine sidecar" },
  { key: "web", label: "Web UI" },
  { key: "engine_protocol", label: "Engine protocol" },
  { key: "delegation_schema", label: "Delegation schema" },
];

/**
 * Part + contract version matrix (per-part versioning ruling
 * 2026-09-20). Parts identify, contracts gate compatibility.
 * Degrades to an honest note when the backend is unreachable —
 * never a blank card, never a throw (route boundary owns that).
 */
export function VersionMatrix() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["versions"],
    queryFn: () => api.getVersions(),
  });

  return (
    <Card data-testid="versions-card">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Versions</CardTitle>
        <CardDescription>
          Part versions identify each piece; contract versions gate compatibility.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        {isLoading &&
          ROWS.map((row) => (
            <div key={row.key} className="flex items-center justify-between">
              <span className="text-muted-foreground">{row.label}</span>
              <Skeleton className="h-4 w-12" />
            </div>
          ))}
        {!isLoading && isError && (
          <p className="text-muted-foreground" data-testid="versions-unavailable">
            Version info unavailable — the backend did not answer.
          </p>
        )}
        {!isLoading && !isError && data && (
          <div className="space-y-2">
            {ROWS.map((row) => {
              const raw = data[row.key];
              const value = raw === null || raw === undefined ? "unknown" : String(raw);
              return (
                <div
                  key={row.key}
                  className="flex items-center justify-between"
                  data-testid={`versions-row-${row.key}`}
                >
                  <span className="text-muted-foreground">{row.label}</span>
                  <Badge variant="muted">{value}</Badge>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
