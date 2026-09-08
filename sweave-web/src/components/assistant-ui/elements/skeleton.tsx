/**
 * SkeletonPrimitive (shadcn/ui skeleton).
 *
 * Loading placeholders for history loading, empty states, etc.
 */

"use client";

import { type ComponentPropsWithoutRef } from "react";
import { cn } from "@/utils/cn";

interface SkeletonProps extends ComponentPropsWithoutRef<"div"> {}

export function Skeleton({ className, ...props }: SkeletonProps) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  );
}

export const SkeletonPrimitive = {
  Skeleton,
};