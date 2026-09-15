/**
 * RouteErrorBoundary (per-route render guard).
 *
 * React unmounts the whole tree on an uncaught render throw — one bad
 * page (e.g. a stats cell with an unexpected shape) blank-screens the
 * entire app including sidebar/topbar. Every route element in App.tsx
 * renders inside one of these, so a crash is contained to the surface
 * that threw: the fallback names the surface, shows the message, and
 * offers retry + escape to chat. No new dependency (hand-rolled class
 * boundary; ~60 lines).
 */
import { Component, type ReactNode } from "react";

interface RouteErrorBoundaryProps {
  /** Human label for the surface ("Chat", "Usage", ...) shown in the fallback. */
  label: string;
  children: ReactNode;
}

interface RouteErrorBoundaryState {
  error: Error | null;
}

export class RouteErrorBoundary extends Component<
  RouteErrorBoundaryProps,
  RouteErrorBoundaryState
> {
  state: RouteErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): RouteErrorBoundaryState {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: Error): void {
    // eslint-disable-next-line no-console
    console.error(`[sweave:${this.props.label}] render crash contained`, error);
  }

  private retry = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    const { label } = this.props;
    return (
      <div className="p-6" data-testid="route-error">
        <div
          role="alert"
          className="mx-auto max-w-lg rounded-xl border border-rose-500/40 bg-card px-5 py-4 shadow-sm"
        >
          <h1 className="text-base font-semibold">
            Something went wrong in {label}
          </h1>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {error.message || "Unknown render error"}
          </p>
          <p className="mt-2 text-sm text-muted-foreground">
            The rest of the app is unaffected — this surface alone failed
            to render.
          </p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={this.retry}
              className="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
            >
              Try again
            </button>
            <a
              href="/chat"
              className="rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground"
            >
              Back to chat
            </a>
          </div>
          {import.meta.env.DEV && error.stack && (
            <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
              {error.stack}
            </pre>
          )}
        </div>
      </div>
    );
  }
}
