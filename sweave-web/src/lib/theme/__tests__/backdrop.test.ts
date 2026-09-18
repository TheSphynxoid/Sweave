import { describe, it, expect } from "vitest";
import {
  DEFAULT_BACKDROP,
  CHAT_BACKDROP_ATTR,
  applyBackdropToDocument,
  listBackdrops,
  loadBackdrop,
  saveBackdrop,
} from "../switcher";

describe("chat backdrop", () => {
  it("defaults to glow so existing users see zero change", () => {
    expect(DEFAULT_BACKDROP).toBe("glow");
    expect(loadBackdrop()).toBe("glow");
  });

  it("round-trips each option through localStorage", () => {
    for (const name of listBackdrops()) {
      saveBackdrop(name);
      expect(loadBackdrop()).toBe(name);
    }
  });

  it("falls back to glow on unknown or missing values", () => {
    window.localStorage.setItem("sweave.theme.backdrop", "wallpaper");
    expect(loadBackdrop()).toBe("glow");
    window.localStorage.removeItem("sweave.theme.backdrop");
    expect(loadBackdrop()).toBe("glow");
  });

  it("applyBackdropToDocument writes the attribute (glow keeps current visuals)", () => {
    applyBackdropToDocument("glow");
    expect(document.documentElement.getAttribute(CHAT_BACKDROP_ATTR)).toBe("glow");
    applyBackdropToDocument("floral");
    expect(document.documentElement.getAttribute(CHAT_BACKDROP_ATTR)).toBe("floral");
    applyBackdropToDocument("none");
    expect(document.documentElement.getAttribute(CHAT_BACKDROP_ATTR)).toBe("none");
  });

  it("never touches the color theme keys", () => {
    saveBackdrop("floral");
    expect(window.localStorage.getItem("sweave.theme.preset")).toBeNull();
    expect(window.localStorage.getItem("sweave.theme.custom")).toBeNull();
  });
});
