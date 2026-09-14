"use client";

import {
  createContext,
  useContext,
  useState,
  useRef,
  useEffect,
  type ReactNode,
  type ComponentPropsWithoutRef,
  forwardRef,
  isValidElement,
  cloneElement,
  Children,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "@/utils/cn";

// Simple tooltip implementation (shadcn-style)

interface TooltipContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  triggerRef: React.RefObject<HTMLElement | null>;
  contentRef: React.RefObject<HTMLDivElement | null>;
}

const TooltipContext = createContext<TooltipContextValue | null>(null);

function useTooltipContext() {
  const ctx = useContext(TooltipContext);
  if (!ctx) {
    throw new Error("Tooltip components must be used within TooltipProvider");
  }
  return ctx;
}

interface TooltipProviderProps {
  children: ReactNode;
  defaultOpen?: boolean;
  delayDuration?: number;
}

export function TooltipProvider({ children, defaultOpen = false, delayDuration = 200 }: TooltipProviderProps) {
  const [open, setOpen] = useState(defaultOpen);
  const triggerRef = useRef<HTMLElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Store delayDuration for potential future use
  void delayDuration;

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  return (
    <TooltipContext.Provider value={{ open, setOpen, triggerRef, contentRef }}>
      {children}
    </TooltipContext.Provider>
  );
}

interface TooltipProps extends ComponentPropsWithoutRef<"div"> {
  children: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export function Tooltip({ children, open: controlledOpen, onOpenChange, ...props }: TooltipProps) {
  const { open: uncontrolledOpen, setOpen } = useTooltipContext();
  const open = controlledOpen ?? uncontrolledOpen;
  const setOpenWrapper = (value: boolean) => {
    setOpen(value);
    onOpenChange?.(value);
  };

  return (
    <div {...props}>
      {Children.map(children, (child) => {
        if (!isValidElement(child)) return child;
        return cloneElement(child, {
          open,
          onOpenChange: setOpenWrapper,
        } as any);
      })}
    </div>
  );
}

interface TooltipTriggerProps extends ComponentPropsWithoutRef<"div"> {
  asChild?: boolean;
  children: ReactNode;
}

export const TooltipTrigger = forwardRef<HTMLElement, TooltipTriggerProps>(
  ({ asChild = false, children, ...props }, ref) => {
    const { triggerRef, setOpen } = useTooltipContext();

    const handleMouseEnter = () => setOpen(true);
    const handleMouseLeave = () => setOpen(false);
    const handleFocus = () => setOpen(true);
    const handleBlur = () => setOpen(false);

    const composedRef = (node: HTMLElement | null) => {
      triggerRef.current = node;
      if (ref) {
        if (typeof ref === "function") ref(node);
        else ref.current = node;
      }
    };

    if (asChild && isValidElement(children)) {
      // For asChild, we need to spread the event handlers and ref manually
      // since we can't pass ref through cloneElement
      const child = children as React.ReactElement<any>;
      return cloneElement(child, {
        ...child.props,
        onMouseEnter: handleMouseEnter,
        onMouseLeave: handleMouseLeave,
        onFocus: handleFocus,
        onBlur: handleBlur,
        ref: composedRef,
      });
    }

    return (
      <div
        ref={composedRef}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        onFocus={handleFocus}
        onBlur={handleBlur}
        {...props}
      >
        {children}
      </div>
    );
  }
);

TooltipTrigger.displayName = "TooltipTrigger";

interface TooltipContentProps extends ComponentPropsWithoutRef<"div"> {
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
  sideOffset?: number;
}

export function TooltipContent({
  side = "top",
  align = "center",
  sideOffset = 4,
  className,
  children,
  ...props
}: TooltipContentProps) {
  const { open, triggerRef, contentRef } = useTooltipContext();
  // Null until measured: the first paint stays hidden so the tip
  // never flashes at the viewport corner before positioning.
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    if (!open || !triggerRef.current) return;

    const updatePosition = () => {
      const trigger = triggerRef.current;
      if (!trigger) return;

      const triggerRect = trigger.getBoundingClientRect();
      const content = contentRef.current;
      if (!content) return;

      const contentRect = content.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;

      let top = 0;
      let left = 0;

      switch (side) {
        case "top":
          top = triggerRect.top - contentRect.height - sideOffset;
          break;
        case "bottom":
          top = triggerRect.bottom + sideOffset;
          break;
        case "left":
          left = triggerRect.left - contentRect.width - sideOffset;
          break;
        case "right":
          left = triggerRect.right + sideOffset;
          break;
      }

      switch (align) {
        case "start":
          left = triggerRect.left;
          break;
        case "center":
          left = triggerRect.left + triggerRect.width / 2 - contentRect.width / 2;
          break;
        case "end":
          left = triggerRect.right - contentRect.width;
          break;
      }

      // Clamp to viewport
      left = Math.max(sideOffset, Math.min(left, viewportWidth - contentRect.width - sideOffset));
      top = Math.max(sideOffset, Math.min(top, viewportHeight - contentRect.height - sideOffset));

      setPosition({ top, left });
    };

    updatePosition();
    window.addEventListener("scroll", updatePosition, { passive: true });
    window.addEventListener("resize", updatePosition);

    return () => {
      window.removeEventListener("scroll", updatePosition);
      window.removeEventListener("resize", updatePosition);
    };
  }, [open, side, align, sideOffset]);

  useEffect(() => {
    if (!open) setPosition(null);
  }, [open ]);

  if (!open) return null;

  // Portaled to document.body: the trigger often lives under a
  // backdrop-blur ancestor (message card, composer), which becomes
  // the containing block for non-portaled fixed content — the tip
  // then overflows the card, grows the page, shifts the button out
  // from under the cursor, and hover-flickers in a loop. A portal
  // keeps the tip in viewport coordinates: zero layout impact.
  return createPortal(
    <div
      ref={contentRef}
      style={{
        position: "fixed",
        top: position?.top ?? 0,
        left: position?.left ?? 0,
        zIndex: 50,
        visibility: position ? "visible" : "hidden",
      }}
      className={cn(
        "px-2 py-1 text-xs text-muted-foreground bg-popover border border-border rounded shadow-lg",
        "animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
        className
      )}
      {...props}
    >
      {children}
      <div
        className="absolute left-1/2 -translate-x-1/2 w-0 h-0 border-4 border-transparent"
        style={{
          bottom: side === "top" ? "-8px" : "auto",
          top: side === "bottom" ? "-8px" : "auto",
          borderBottomColor: side === "top" ? "hsl(var(--border))" : "transparent",
          borderTopColor: side === "bottom" ? "hsl(var(--border))" : "transparent",
        }}
      />
    </div>,
    document.body
  );
}

export const TooltipPrimitive = {
  Provider: TooltipProvider,
  Trigger: TooltipTrigger,
  Content: TooltipContent,
};