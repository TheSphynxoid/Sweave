/**
 * AvatarPrimitive (shadcn/ui avatar).
 *
 * Used for message avatars (user/assistant identity).
 */

"use client";

import { type ComponentPropsWithoutRef, type ReactNode, forwardRef, useState } from "react";
import { cn } from "@/utils/cn";

interface AvatarProps extends ComponentPropsWithoutRef<"div"> {
  src?: string;
  alt?: string;
  fallback?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}

const sizeClasses = {
  sm: "h-8 w-8 text-xs",
  md: "h-10 w-10 text-sm",
  lg: "h-12 w-12 text-base",
  xl: "h-16 w-16 text-lg",
};

export const Avatar = forwardRef<HTMLDivElement, AvatarProps>(
  ({ className, src, alt, fallback, size = "md", ...props }, ref) => {
    const [imageError, setImageError] = useState(false);

    return (
      <div
        ref={ref}
        className={cn("relative flex shrink-0 overflow-hidden rounded-full", sizeClasses[size], className)}
        {...props}
      >
        {src && !imageError ? (
          <img
            src={src}
            alt={alt ?? ""}
            onError={() => setImageError(true)}
            className="aspect-square h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center rounded-full bg-muted">
            {fallback}
          </div>
        )}
      </div>
    );
  }
);

Avatar.displayName = "Avatar";

export const AvatarPrimitive = {
  Avatar,
};