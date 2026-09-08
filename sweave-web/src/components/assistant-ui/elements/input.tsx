/**
 * InputPrimitive (shadcn/ui input).
 *
 * Used by ComposerPrimitive.Input (though composer typically uses textarea).
 */

"use client";

import { type ComponentPropsWithoutRef, forwardRef } from "react";
import { cn } from "@/utils/cn";

interface InputProps extends ComponentPropsWithoutRef<"input"> {}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background",
          "file:border-0 file:bg-transparent file:text-sm file:font-medium",
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

Input.displayName = "Input";

export const InputPrimitive = {
  Input,
};