/**
 * CollapsiblePrimitive (shadcn/ui collapsible).
 *
 * Used for reasoning/thinking traces, tool call details.
 */

"use client";

import {
  createContext,
  useContext,
  useState,
  type ReactNode,
  type ComponentPropsWithoutRef,
} from "react";
import { cn } from "@/utils/cn";
import { ChevronDown } from "lucide-react";

interface CollapsibleContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
}

const CollapsibleContext = createContext<CollapsibleContextValue | null>(null);

function useCollapsibleContext() {
  const ctx = useContext(CollapsibleContext);
  if (!ctx) {
    throw new Error("Collapsible components must be used within Collapsible");
  }
  return ctx;
}

interface CollapsibleProps extends ComponentPropsWithoutRef<"div"> {
  open?: boolean;
  defaultOpen?: boolean;
  children: ReactNode;
}

export function Collapsible({ open: controlledOpen, defaultOpen = false, children, className, ...props }: CollapsibleProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const open = controlledOpen ?? uncontrolledOpen;
  const setOpen = (value: boolean) => {
    if (controlledOpen === undefined) setUncontrolledOpen(value);
  };

  return (
    <CollapsibleContext.Provider value={{ open, setOpen }}>
      <div className={cn("", className)} {...props}>{children}</div>
    </CollapsibleContext.Provider>
  );
}

interface CollapsibleTriggerProps extends ComponentPropsWithoutRef<"button"> {
  children: ReactNode;
}

export function CollapsibleTrigger({ className, children, ...props }: CollapsibleTriggerProps) {
  const { open, setOpen } = useCollapsibleContext();

  return (
    <button
      type="button"
      onClick={() => setOpen(!open)}
      className={cn(
        "flex items-center gap-2 text-sm font-medium text-foreground hover:text-primary",
        "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
        className
      )}
      {...props}
    >
      {children}
      <ChevronDown size={14} className={cn("transition-transform", open && "rotate-180")} />
    </button>
  );
}

interface CollapsibleContentProps extends ComponentPropsWithoutRef<"div"> {
  children: ReactNode;
}

export function CollapsibleContent({ className, children, ...props }: CollapsibleContentProps) {
  const { open } = useCollapsibleContext();

  return (
    <div
      className={cn(
        "overflow-hidden transition-all data-[state=open]:animate-accordion-down data-[state=closed]:animate-accordion-up",
        className
      )}
      {...props}
    >
      <div style={{ overflow: "hidden" }}>{open && children}</div>
    </div>
  );
}

export const CollapsiblePrimitive = {
  Collapsible,
  Trigger: CollapsibleTrigger,
  Content: CollapsibleContent,
};