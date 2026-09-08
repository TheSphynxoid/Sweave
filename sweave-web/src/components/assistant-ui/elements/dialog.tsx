/**
 * DialogPrimitive (shadcn/ui dialog).
 *
 * Used for confirmation dialogs, edit composer, etc.
 */

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
} from "react";
import { cn } from "@/utils/cn";
import { X } from "lucide-react";

interface DialogContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  triggerRef: React.RefObject<HTMLElement | null>;
  contentRef: React.RefObject<HTMLDivElement | null>;
}

const DialogContext = createContext<DialogContextValue | null>(null);

function useDialogContext() {
  const ctx = useContext(DialogContext);
  if (!ctx) {
    throw new Error("Dialog components must be used within DialogProvider");
  }
  return ctx;
}

interface DialogProviderProps {
  children: ReactNode;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export function DialogProvider({
  children,
  open: controlledOpen,
  defaultOpen = false,
  onOpenChange,
}: DialogProviderProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const triggerRef = useRef<HTMLElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  const open = controlledOpen ?? uncontrolledOpen;
  const setOpen = (value: boolean) => {
    if (controlledOpen === undefined) setUncontrolledOpen(value);
    onOpenChange?.(value);
  };

  return (
    <DialogContext.Provider value={{ open, setOpen, triggerRef, contentRef }}>
      {children}
    </DialogContext.Provider>
  );
}

interface DialogProps extends ComponentPropsWithoutRef<"div"> {}

export function Dialog({ children, ...props }: DialogProps) {
  const { open } = useDialogContext();
  if (!open) return null;
  return <div {...props}>{children}</div>;
}

interface DialogTriggerProps extends ComponentPropsWithoutRef<"button"> {
  asChild?: boolean;
  children: ReactNode;
}

export const DialogTrigger = forwardRef<HTMLButtonElement, DialogTriggerProps>(
  ({ asChild = false, children, ...props }, ref) => {
    const { setOpen, triggerRef } = useDialogContext();

    const composedRef = (node: HTMLButtonElement | null) => {
      triggerRef.current = node;
      if (ref) {
        if (typeof ref === "function") ref(node);
        else ref.current = node;
      }
    };

    if (asChild && isValidElement(children)) {
      const child = children as React.ReactElement<any>;
      return cloneElement(child, {
        ...child.props,
        onClick: () => setOpen(true),
        ref: composedRef,
      });
    }

    return (
      <button ref={composedRef} onClick={() => setOpen(true)} {...props}>
        {children}
      </button>
    );
  }
);

DialogTrigger.displayName = "DialogTrigger";

interface DialogContentProps extends ComponentPropsWithoutRef<"div"> {
  children: ReactNode;
}

export function DialogContent({ className, children, ...props }: DialogContentProps) {
  const { open, setOpen, contentRef } = useDialogContext();

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    if (open) {
      document.addEventListener("keydown", handleEscape);
      document.body.style.overflow = "hidden";
    }
    return () => {
      document.removeEventListener("keydown", handleEscape);
      document.body.style.overflow = "";
    };
  }, [open, setOpen]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setOpen(false)}>
      <div
        ref={contentRef}
        className={cn(
          "relative z-50 w-full max-w-lg rounded-lg bg-background p-6 shadow-lg animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
          className
        )}
        onClick={(e) => e.stopPropagation()}
        {...props}
      >
        {children}
        <button
          className="absolute right-4 top-4 rounded-sm opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:pointer-events-none disabled:opacity-50"
          onClick={() => setOpen(false)}
          aria-label="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

export const DialogPrimitive = {
  Provider: DialogProvider,
  Trigger: DialogTrigger,
  Content: DialogContent,
};