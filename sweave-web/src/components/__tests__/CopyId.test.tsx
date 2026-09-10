/**
 * CopyId affordance tests.
 *
 * Badges show the sliced id but must copy the FULL id (the debugging
 * contract: quote `chat-1389…` fully, not the 8-char prefix).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CopyIdBadge, CopyIconButton } from "@/components/CopyId";

const FULL_ID = "chat-1389698ec0e2";

function stubClipboard(impl: () => Promise<void>) {
  const writeText = vi.fn(impl);
  Object.assign(navigator, { clipboard: { writeText } });
  return writeText;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("CopyIdBadge", () => {
  it("shows the sliced id with the full id in the title", () => {
    stubClipboard(() => Promise.resolve());
    render(<CopyIdBadge id={FULL_ID} label="Chat turn delegation" testId="badge" />);
    const badge = screen.getByTestId("badge");
    expect(badge.textContent).toBe(FULL_ID.slice(0, 8));
    expect(badge.getAttribute("title")).toContain(FULL_ID);
  });

  it("copies the full id on click", async () => {
    const writeText = stubClipboard(() => Promise.resolve());
    render(<CopyIdBadge id={FULL_ID} label="Chat turn delegation" testId="badge" />);
    fireEvent.click(screen.getByTestId("badge"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(FULL_ID));
    expect(screen.getByTestId("badge").getAttribute("data-copied")).toBe("true");
  });

  it("stays silent when the clipboard is unavailable", () => {
    const writeText = stubClipboard(() => Promise.reject(new Error("denied")));
    render(<CopyIdBadge id={FULL_ID} label="Chat turn delegation" testId="badge" />);
    expect(() => fireEvent.click(screen.getByTestId("badge"))).not.toThrow();
    expect(writeText).toHaveBeenCalledWith(FULL_ID);
  });
});

describe("CopyIconButton", () => {
  it("copies the full id on click with the id in the title", async () => {
    const sessionId = "Sweave-20260909-224620-1e451c";
    const writeText = stubClipboard(() => Promise.resolve());
    render(<CopyIconButton id={sessionId} label="Session id" testId="copy-btn" />);
    const btn = screen.getByTestId("copy-btn");
    expect(btn.getAttribute("title")).toContain(sessionId);
    fireEvent.click(btn);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(sessionId));
  });
});
