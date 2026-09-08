/**
 * TooltipIconButtonPrimitive (assistant-ui shadcn-registry copy).
 *
 * Icon button with tooltip, used for action bar items.
 */

"use client";

import { type ComponentPropsWithoutRef, type ReactNode, forwardRef } from "react";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/utils/cn";

interface TooltipIconButtonProps extends ComponentPropsWithoutRef<"button"> {
  tooltip: string;
  children: ReactNode;
  variant?: "default" | "ghost" | "outline";
  size?: "sm" | "md" | "lg";
}

export const TooltipIconButton = forwardRef<HTMLButtonElement, TooltipIconButtonProps>(
  ({ tooltip, children, variant = "ghost", size = "sm", className, ...props }, ref) => {
    const sizeClasses = {
      sm: "h-8 w-8",
      md: "h-10 w-10",
      lg: "h-12 w-12",
    };
    const variantClasses = {
      default: "bg-primary text-primary-foreground hover:bg-primary/90",
      ghost: "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
      outline: "border border-border bg-background hover:bg-accent hover:text-accent-foreground",
    };

    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              ref={ref}
              className={cn(
                "inline-flex items-center justify-center rounded-lg transition-colors",
                "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
                "disabled:pointer-events-none disabled:opacity-50",
                sizeClasses[size],
                variantClasses[variant],
                className
              )}
              {...props}
            >
              {children}
            </button>
          </TooltipTrigger>
          <TooltipContent side="top" align="center">
            {tooltip}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }
);

TooltipIconButton.displayName = "TooltipIconButton";

export const TooltipIconButtonPrimitive = {
  TooltipIconButton,
};