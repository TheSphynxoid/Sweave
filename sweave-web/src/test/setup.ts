// Vitest setup. jsdom provides localStorage + document; the
// theme switcher tests rely on both. Per-test cleanup wipes the
// persisted keys so order doesn't matter.

import { afterEach, beforeAll } from "vitest";

// jsdom has no ResizeObserver; the assistant-ui thread viewport
// (auto-scroll) and other layout-sensitive components need it.
beforeAll(() => {
  const g = globalThis as unknown as Record<string, unknown>;
  if (!g.ResizeObserver) {
    g.ResizeObserver = class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    };
  }
});

afterEach(() => {
  try {
    window.localStorage.removeItem("sweave.theme.preset");
    window.localStorage.removeItem("sweave.theme.custom");
    window.localStorage.removeItem("sweave.theme.backdrop");
  } catch {
    // ignore
  }
  // Remove any theme <style> element we appended.
  const el = document.getElementById("sweave-theme-vars");
  if (el) el.remove();
  // Reset the data attribute + dark class on :root.
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-chat-backdrop");
  document.documentElement.classList.remove("dark");
  document.documentElement.style.colorScheme = "";
});
