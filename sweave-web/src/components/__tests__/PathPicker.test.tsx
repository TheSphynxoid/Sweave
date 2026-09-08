/**
 * PathPicker tests (R4.2 step 2-pre polish round).
 *
 * The folder picker is the create-project funnel's bottleneck; these
 * pins cover the navigation affordances: stale-response guard,
 * breadcrumbs, up-navigation, typed-path jumps, drive chips, and
 * client-side filtering. The api module is mocked (no axios).
 *
 * The tests mount `PathPickerBrowser` DIRECTLY: the bare Radix popover
 * hangs jsdom in this dependency tree (see docs/GOTCHAS.md,
 * "assistant-ui 0.15 primitives"). The Popover shell is browser-only
 * and is visually verified by the screenshot gates.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PathPickerBrowser, pathSegments } from "@/components/PathPicker";
import { api } from "@/api/client";

vi.mock("@/api/client", () => ({
  api: {
    listDrives: vi.fn(),
    listDirectory: vi.fn(),
    createDirectory: vi.fn(),
  },
}));

const mocked = vi.mocked(api);

function listRes(path: string, parent: string | null, names: string[]) {
  return {
    path,
    parent,
    entries: names.map((name) => ({ name, path: `${path}\\${name}`, is_dir: true })),
  };
}

describe("pathSegments", () => {
  it("splits a Windows path into cumulative clickable segments", () => {
    expect(pathSegments("C:\\Users\\foo\\bar")).toEqual([
      { label: "C:", path: "C:\\" },
      { label: "Users", path: "C:\\Users" },
      { label: "foo", path: "C:\\Users\\foo" },
      { label: "bar", path: "C:\\Users\\foo\\bar" },
    ]);
  });

  it("handles a posix path and the filesystem root", () => {
    expect(pathSegments("/srv/code")[0]).toEqual({ label: "srv", path: "/srv" });
    expect(pathSegments("/")).toEqual([{ label: "/", path: "/" }]);
  });
});

describe("PathPickerBrowser", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listDrives.mockResolvedValue([
      { name: "C:", path: "C:\\", is_dir: true },
      { name: "D:", path: "D:\\", is_dir: true },
    ]);
    mocked.listDirectory.mockResolvedValue(
      listRes("C:\\Users\\foo", "C:\\Users", ["alpha", "beta", "gamma"]),
    );
  });

  it("lists folders and shows drive chips", async () => {
    render(<PathPickerBrowser initialPath="C:\\Users\\foo" onSelect={() => {}} />);
    await waitFor(() => {
      expect(screen.getByTestId("path-picker-row-alpha")).toBeTruthy();
    });
    expect(screen.getByText("D:")).toBeTruthy();
    // The ".." entry is filtered out (the Up button replaces it).
    expect(document.querySelectorAll('[data-testid^="path-picker-row-"]').length).toBe(3);
  });

  it("navigates via breadcrumbs, the Up button, and a typed path", async () => {
    render(<PathPickerBrowser initialPath="C:\\Users\\foo" onSelect={() => {}} />);
    await waitFor(() => {
      expect(screen.getByTestId("path-picker-row-alpha")).toBeTruthy();
    });

    // Breadcrumb: click an ancestor.
    fireEvent.click(screen.getByText("Users"));
    await waitFor(() => {
      expect(mocked.listDirectory).toHaveBeenCalledWith("C:\\Users");
    });

    // Up button: goes to the listing's parent.
    fireEvent.click(screen.getByTestId("path-picker-up"));
    await waitFor(() => {
      expect(mocked.listDirectory).toHaveBeenCalledWith("C:\\Users");
    });

    // Typed path + Enter.
    const input = screen.getByTestId("path-picker-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "~\\code" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => {
      expect(mocked.listDirectory).toHaveBeenCalledWith("~\\code");
    });
  });

  it("filters the current listing client-side", async () => {
    render(<PathPickerBrowser initialPath="C:\\Users\\foo" onSelect={() => {}} />);
    await waitFor(() => {
      expect(screen.getByTestId("path-picker-row-alpha")).toBeTruthy();
    });
    fireEvent.change(screen.getByTestId("path-picker-filter"), {
      target: { value: "be" },
    });
    expect(screen.getByTestId("path-picker-row-beta")).toBeTruthy();
    expect(screen.queryByTestId("path-picker-row-alpha")).toBeNull();
    expect(screen.queryByTestId("path-picker-row-gamma")).toBeNull();
  });

  it("survives a stale response: the slower earlier load cannot clobber the newer one", async () => {
    // First call resolves LAST and must lose.
    let resolveSlow: (v: Awaited<ReturnType<typeof api.listDirectory>>) => void;
    mocked.listDirectory.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveSlow = resolve;
        }),
    );
    render(<PathPickerBrowser initialPath="C:\\Users\\foo" onSelect={() => {}} />);
    // While the first (slow) load is in flight, jump elsewhere: the
    // second load bumps the request id; the first becomes stale.
    await waitFor(() => {
      expect(screen.getByTestId("path-picker-input")).toBeTruthy();
    });
    const input = screen.getByTestId("path-picker-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "D:\\elsewhere" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => {
      expect(mocked.listDirectory).toHaveBeenCalledWith("D:\\elsewhere");
    });
    resolveSlow!(listRes("C:\\STALE", "C:\\", ["stale"]));
    await new Promise((r) => setTimeout(r, 20));
    // The stale listing never rendered.
    expect(screen.queryByText("stale")).toBeNull();
  });

  it("reports selection: Select this folder fires onSelect with the current path", async () => {
    const onSelect = vi.fn();
    render(<PathPickerBrowser initialPath="C:\\Users\\foo" onSelect={onSelect} />);
    await waitFor(() => {
      expect(screen.getByTestId("path-picker-select")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("path-picker-select"));
    expect(onSelect).toHaveBeenCalledWith("C:\\Users\\foo");
  });

  it("shows an inline error and keeps the current path when a jump fails", async () => {
    render(<PathPickerBrowser initialPath="C:\\Users\\foo" onSelect={() => {}} />);
    await waitFor(() => {
      expect(screen.getByTestId("path-picker-row-alpha")).toBeTruthy();
    });
    // Queue the failure AFTER the initial load has been consumed.
    mocked.listDirectory.mockRejectedValueOnce(new Error("Path does not exist"));
    const input = screen.getByTestId("path-picker-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Q:\\nope" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => {
      expect(screen.getByTestId("path-picker-error")).toBeTruthy();
    });
    // The old listing is still there (not cleared by the failure).
    expect(screen.getByTestId("path-picker-row-alpha")).toBeTruthy();
  });
});
