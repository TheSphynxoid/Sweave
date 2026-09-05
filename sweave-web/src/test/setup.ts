// Vitest setup. jsdom provides localStorage + document; the
// theme switcher tests rely on both. Per-test cleanup wipes the
// persisted keys so order doesn't matter.

import { afterEach } from "vitest";

afterEach(() => {
  try {
    window.localStorage.removeItem("sweave.theme.preset");
    window.localStorage.removeItem("sweave.theme.custom");
  } catch {
    // ignore
  }
  // Remove any theme <style> element we appended.
  const el = document.getElementById("sweave-theme-vars");
  if (el) el.remove();
  // Reset the data attribute on :root.
  document.documentElement.removeAttribute("data-theme");
});
