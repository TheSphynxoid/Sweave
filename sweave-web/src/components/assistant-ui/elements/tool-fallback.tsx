/**
 * ToolFallbackPrimitive (assistant-ui shadcn-registry copy).
 *
 * Generic tool call card: icon + tool name + collapsed args + expanded output + status pill.
 * Per-tool renderers (Bash/Edit-diff/Search/Plan/Subagent/MCP/Thinking) are step 2c scope.
 * This is the fallback slot used by MessagePrimitive.Parts.
 */

"use client";

import { type ReactNode } from "react";
import { ChevronDown, Terminal, FileText, Search, Zap, Brain, Globe, Loader2, CheckCircle, AlertCircle } from "lucide-react";
import { useState } from "react";
import { cn } from "@/utils/cn";

interface ToolCall {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  result?: unknown;
  status: "pending" | "running" | "completed" | "error";
}

interface ToolFallbackProps {
  toolCall: ToolCall;
  className?: string;
}

const TOOL_ICONS: Record<string, ReactNode> = {
  bash: <Terminal size={14} />,
  edit: <FileText size={14} />,
  search: <Search size={14} />,
  plan: <Zap size={14} />,
  subagent: <Brain size={14} />,
  mcp: <Globe size={14} />,
  thinking: <Brain size={14} />,
  default: <Terminal size={14} />,
};

const STATUS_ICONS = {
  pending: <Loader2 size={12} className="animate-spin text-muted-foreground" />,
  running: <Loader2 size={12} className="animate-spin text-primary" />,
  completed: <CheckCircle size={12} className="text-green-500" />,
  error: <AlertCircle size={12} className="text-red-500" />,
};

export function ToolFallback({ toolCall, className }: ToolFallbackProps) {
  const [expanded, setExpanded] = useState(false);
  const icon = TOOL_ICONS[toolCall.name] || TOOL_ICONS.default;
  const statusIcon = STATUS_ICONS[toolCall.status];

  return (
    <div
      className={cn(
        "border border-border rounded-lg bg-card/50 p-3 text-sm transition-all",
        className
      )}
      data-testid="tool-fallback"
      data-status={toolCall.status}
    >
      <div className="flex items-center gap-2 mb-2">
        <span className="text-muted-foreground">{icon}</span>
        <span className="font-medium text-foreground">{toolCall.name}</span>
        <span className="ml-auto flex items-center gap-1">
          {statusIcon}
          <span className={cn("text-xs", {
            "text-muted-foreground": toolCall.status === "pending",
            "text-primary": toolCall.status === "running",
            "text-green-500": toolCall.status === "completed",
            "text-red-500": toolCall.status === "error",
          })}>
            {toolCall.status}
          </span>
        </span>
      </div>

      {toolCall.args && (
        <div className="mb-2">
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            aria-expanded={expanded}
          >
            <ChevronDown size={12} className={cn("transition-transform", expanded && "rotate-180")} />
            <span>Arguments</span>
            <span className="text-muted-foreground">({Object.keys(toolCall.args).length})</span>
          </button>
          {expanded && (
            <pre className="mt-2 text-xs bg-muted/50 rounded p-2 overflow-x-auto max-h-48">
              {JSON.stringify(toolCall.args, null, 2)}
            </pre>
          )}
        </div>
      )}

      {toolCall.result !== undefined && (
        <div>
          <div className="flex items-center gap-1 text-xs text-muted-foreground mb-1">
            <span>Output</span>
          </div>
          <pre className="text-xs bg-muted/50 rounded p-2 overflow-x-auto max-h-64">
            {typeof toolCall.result === "string"
              ? toolCall.result
              : JSON.stringify(toolCall.result, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

export const ToolFallbackPrimitive = {
  ToolFallback,
};