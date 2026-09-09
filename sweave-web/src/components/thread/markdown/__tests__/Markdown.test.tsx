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
import { fireEvent, render, screen } from "@testing-library/react";
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

  it("renders raw text while streaming (bit-by-bit visibility)", () => {
    // A partial fence must stay visible mid-stream instead of
    // rendering as an empty/odd Markdown structure.
    render(
      <AssistantTextPart
        type="text"
        text="```ts\nconst x = 1;\n// still streaming\n"
        status={{ type: "running" }}
      />,
    );
    const plain = screen.getByTestId("assistant-streaming-plain");
    expect(plain.textContent).toContain("const x = 1");
    // No Markdown pass while running (no <pre> wrapper, no copy btn).
    expect(screen.queryByTestId("markdown-pre")).toBeNull();
  });
});

describe("ChatLab (real-Thread visual test)", () => {
  it("renders the seeded thread inside the real Thread (markdown + code blocks survive)", () => {
    render(<ChatLab />);
    expect(screen.getByTestId("chat-lab")).toBeTruthy();
    // The seeded assistant message renders through the real
    // MessagePrimitive.Parts -> AssistantTextPart -> Markdown path.
    expect(screen.getAllByTestId("assistant-message-row").length).toBe(2);
    expect(screen.getAllByTestId("user-message-row").length).toBe(2);
    // The markdown demo survives as seeded content: a code block
    // with the copy affordance is present.
    const copies = screen.getAllByTestId("markdown-copy-code");
    expect(copies.length).toBeGreaterThan(0);
  });

  it("renders the user message as plain text (no markdown)", () => {
    render(<ChatLab />);
    const userText = screen.getByText("Show me what the chat surface renders.");
    expect(userText).toBeTruthy();
  });

  it("shows the action bar (copy) only on the last assistant message", () => {
    render(<ChatLab />);
    // autohide="not-last": the bar unmounts on non-last messages.
    const bars = screen.getAllByTestId("action-bar");
    expect(bars.length).toBe(1);
  });

  it("renders the welcome screen with suggested prompts when emptied", () => {
    render(<ChatLab />);
    fireEvent.click(screen.getByTestId("lab-btn-empty"));
    expect(screen.getByTestId("welcome-screen")).toBeTruthy();
    expect(screen.getByTestId("suggested-prompt-0").textContent).toContain(
      "Help me understand this codebase",
    );
  });

  it("mid-stream: composer swaps send for the disabled stop affordance", () => {
    render(<ChatLab />);
    fireEvent.click(screen.getByTestId("lab-btn-stream"));
    // The stop affordance appears while the run is in flight
    // (disabled-with-tooltip per the 2026-09-07 ruling).
    const stop = screen.getByTestId("chat-composer-stop");
    expect(stop.hasAttribute("disabled")).toBe(true);
  });

  it("mid-stream: turn status bar names the phase with elapsed + live state", () => {
    render(<ChatLab />);
    fireEvent.click(screen.getByTestId("lab-btn-stream"));
    const bar = screen.getByTestId("turn-status-bar");
    // Phase label (Thinking before the first token, Streaming after)
    // plus the ticking elapsed seconds that prove liveness.
    expect(bar.textContent).toMatch(/Thinking|Streaming/);
    expect(bar.textContent).toMatch(/\d+s/);
    expect(screen.getByTestId("turn-status-ws")).toBeTruthy();
  });
});