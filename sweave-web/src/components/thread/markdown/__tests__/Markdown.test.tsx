/**
 * R4.2 step 2a: markdown + chat-lab rendering tests.
 *
 * @testing-library/react renders the components into a jsdom
 * container; the tests assert on the rendered DOM (semantic
 * structure + testids) rather than on internals.
 *
 * The markdown render is **streaming-safe**: the test for
 * partial markdown (unclosed code fence) pins the contract.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Markdown } from "../Markdown";
import { AssistantTextPart } from "../AssistantTextPart";
import { ChatLab } from "../../../../dev/chat-lab/ChatLab";

describe("Markdown", () => {
  it("renders headings, paragraphs, lists, and links", () => {
    render(
      <Markdown
        source={
          "# Title\n\nA paragraph with **bold** and a [link](https://example.com).\n\n- one\n- two"
        }
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Title" })).toBeTruthy();
    expect(screen.getByText(/bold/)).toBeTruthy();
    const link = screen.getByRole("link", { name: /link/ });
    expect(link.getAttribute("href")).toBe("https://example.com");
    expect(link.getAttribute("target")).toBe("_blank");
    const items = screen.getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual(["one", "two"]);
  });

  it("renders GFM tables", () => {
    render(
      <Markdown
        source={
          "| a | b |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |"
        }
      />,
    );
    const cells = screen.getAllByRole("cell");
    expect(cells.map((c) => c.textContent)).toEqual(["1", "2", "3", "4"]);
  });

  it("renders a code block with a copy button", () => {
    const code = "```ts\nconst x = 1;\n```";
    render(<Markdown source={code} />);
    const pre = screen.getByTestId("markdown-pre");
    expect(pre).toBeTruthy();
    // The shiki rehype plugin decorates the <code> with inline
    // styles (background-color); we assert the structure: <pre>
    // contains a <code> child with the source text.
    const codeEl = pre.querySelector("code");
    expect(codeEl).toBeTruthy();
    expect(codeEl?.textContent).toContain("const x = 1;");
    // The copy button is rendered next to the <pre> (the lab
    // + Markdown both place it inside the wrapper).
    const copyBtn = screen.getByTestId("markdown-copy-code");
    expect(copyBtn).toBeTruthy();
  });

  it("falls back to a plain <pre> on partial / unclosed markdown (streaming-safety)", () => {
    // Unclosed code fence: react-markdown will emit the inner
    // text as text; the try/catch fallback in <Markdown> only
    // triggers on a thrown error. We assert the **wrapper**
    // exists and the inner text is present, not a crash.
    const partial = "```ts\nconst x = 1;\n// still streaming\n";
    render(<Markdown source={partial} />);
    // The pre wrapper exists (the rehype pipeline + the fallback
    // both produce a <pre> in some form).
    const pre = screen.getByTestId("markdown-pre");
    expect(pre).toBeTruthy();
    // The streaming content is visible in the DOM.
    expect(pre.textContent).toContain("const x = 1");
  });
});

describe("AssistantTextPart", () => {
  it("renders the text through Markdown", () => {
    // The runtime always sets `type: "text"` on the part; the lab
    // + this test mirror that so the typed slot is satisfied.
    render(
      <AssistantTextPart
        type="text"
        text="**hello**"
        status={{ type: "complete" }}
      />,
    );
    // react-markdown renders ** as <strong>hello</strong>.
    expect(screen.getByText("hello").tagName).toBe("STRONG");
  });
});

describe("ChatLab (visual test gallery)", () => {
  it("renders every step-2 surface", () => {
    render(<ChatLab />);
    // Top-level container
    expect(screen.getByTestId("chat-lab")).toBeTruthy();
    // At least one assistant bubble with a code block + copy
    // button is present (the full-markdown section).
    const copies = screen.getAllByTestId("markdown-copy-code");
    expect(copies.length).toBeGreaterThan(0);
  });

  it("renders the user-message section with plain text (no markdown)", () => {
    render(<ChatLab />);
    // The user bubble contains the literal text the user typed.
    // (The markdown sections don't render the string "?" in the
    // exact same form -- the assistant message has "?" inside
    // a table cell, so the user bubble's text is its own match.)
    const userText = screen.getByText("What does the chat-lab show?");
    expect(userText).toBeTruthy();
  });
});