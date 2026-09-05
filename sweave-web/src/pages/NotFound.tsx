/**
 * NotFound page (M1.9 Step 1).
 *
 * Default for unmatched routes. The shell's Layout component
 * still wraps the page so the sidebar + topbar are visible
 * (the user can navigate back via the sidebar).
 */
import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="p-6" data-testid="not-found-page">
      <h1 className="text-xl font-semibold">Not found</h1>
      <p className="text-sm text-muted-foreground mt-2">
        The route you requested doesn't exist.{" "}
        <Link to="/chat" className="text-primary underline">
          Go to Chat
        </Link>
        .
      </p>
    </div>
  );
}
