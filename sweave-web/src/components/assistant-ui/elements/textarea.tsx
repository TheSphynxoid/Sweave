/**
 * TextareaPrimitive (shadcn/ui textarea).
 *
 * Used by ComposerPrimitive.Input.
 */

"use client";

import { type ComponentPropsWithoutRef, forwardRef } from "react";
import { cn } from "@/utils/cn";

interface TextareaProps extends ComponentPropsWithoutRef<"textarea"> {}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        className={cn(
          "flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background",
          "placeholder:text-muted-foreground",
          "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
          "disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
        ref={ref}
        {...props}
      />
    );
  }
);

Textarea.displayName = "Textarea";

export const TextareaPrimitive = {
  Textarea,
};