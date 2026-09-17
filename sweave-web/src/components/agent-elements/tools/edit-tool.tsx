import React, { memo } from "react";
import { MultiFileDiff, type FileContents } from "@pierre/diffs/react";
import { TextShimmer } from "../text-shimmer";
import type { TimelineStep, StepState } from "../types/timeline";
import { useToolComplete } from "../hooks/use-tool-complete";
import { IconChevronDown } from "@tabler/icons-react";
import { FileExtIcon } from "../icons/file-ext-icon";
import {
  mapToolInvocationToStep,
  mapToolStateToStepState,
} from "../utils/tool-adapters";
import { ToolApprovalFooter, type ToolApproval } from "./tool-approval-footer";

export type EditToolDiffCardProps = {
  step: Extract<TimelineStep, { type: "tool-call" }>;
  state: StepState;
  onComplete: () => void;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  isCollapsible?: boolean;
  approval?: ToolApproval;
  /** Write overwrite (F4): pre-write file bytes from `detail.old_capture`.
   *  When present the diff shows real green/red; absent degrades to a
   *  create-style all-green (or legacy input-based) diff. Never required. */
  oldCapture?: string | null;
};

export function EditToolDiffCard({
  step,
  state,
  onComplete,
  input,
  output,
  isCollapsible = false,
  approval,
  oldCapture,
}: EditToolDiffCardProps) {
  useToolComplete(state === "animating", step.duration, onComplete);
  const isPending = state === "animating";
  const fileName = step.filePath?.split("/").pop() ?? step.toolDetail;
  const hasFileName = Boolean(fileName);
  const isWrite = step.toolName === "Write";
  const [themeType, setThemeType] = React.useState<"light" | "dark">("light");
  const [isExpanded, setIsExpanded] = React.useState(!isCollapsible);

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const updateTheme = () => {
      const isDark = document.documentElement.classList.contains("dark");
      setThemeType(isDark ? "dark" : "light");
    };
    updateTheme();

    const observer = new MutationObserver(updateTheme);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });

    return () => {
      observer.disconnect();
    };
  }, []);

  React.useEffect(() => {
    setIsExpanded(!isCollapsible);
  }, [isCollapsible]);

  const diffFiles = React.useMemo(() => {
    const fileLabel = fileName || "file";
    const oldFromOutput =
      typeof output?.old_content === "string" ? output.old_content : undefined;
    const newFromOutput =
      typeof output?.content === "string" ? output.content : undefined;
    const oldFromInput: string | undefined =
      !oldFromOutput
        ? typeof input?.old_string === "string"
          ? input.old_string
          : typeof input?.oldString === "string"
            ? input.oldString
            : undefined
        : undefined;
    const newFromInput: string | undefined =
      !newFromOutput
        ? typeof input?.new_string === "string"
          ? input.new_string
          : typeof input?.newString === "string"
            ? input.newString
            : typeof input?.content === "string"
              ? input.content
              : undefined
        : undefined;

    const fallbackOld = step.diffLines
      ?.filter((line) => line.type !== "add")
      .map((line) => line.content)
      .join("\n");
    const fallbackNew = step.diffLines
      ?.filter((line) => line.type !== "remove")
      .map((line) => line.content)
      .join("\n");

    // Write overwrite (F4): prefer the backend's pre-write `old_capture`
    // as the diff old-side so the card renders real green/red (engine
    // persists camelCase `content` + `detail.old_capture`). Absent →
    // degrade to the input/output-based or fallback diff (create-style
    // all-green when there's nothing to remove).
    const oldFromCapture =
      typeof oldCapture === "string" && oldCapture.length > 0 ? oldCapture : undefined;

    const oldContents =
      oldFromCapture ?? oldFromInput ?? oldFromOutput ?? fallbackOld ?? "";
    const newContents = newFromInput ?? newFromOutput ?? fallbackNew ?? "";

    if (!oldContents && !newContents) return null;

    const oldFile: FileContents = {
      name: fileLabel,
      contents: oldContents,
    };
    const newFile: FileContents = {
      name: fileLabel,
      contents: newContents,
    };

    return { oldFile, newFile };
  }, [fileName, input, output, step.diffLines, oldCapture]);

  const diffCssVars = React.useMemo(
    () =>
      themeType === "dark"
        ? ({
            "--diffs-bg": "#000",
            "--diffs-bg-buffer-override": "#000",
            "--diffs-bg-context-override": "#000",
            "--diffs-bg-hover-override": "#0a0a0a",
            "--diffs-bg-separator-override": "#0f0f0f",
          } as React.CSSProperties)
        : undefined,
    [themeType],
  );

  const diffUnsafeCss = React.useMemo(
    () =>
      themeType === "dark"
        ? `
[data-diff],
[data-file],
[data-diffs-header],
[data-error-wrapper],
[data-virtualizer-buffer] {
  --diffs-bg: #000;
  --diffs-bg-buffer-override: #000;
  --diffs-bg-context-override: #000;
  --diffs-bg-hover-override: #0a0a0a;
  --diffs-bg-separator-override: #0f0f0f;
}
`
        : undefined,
    [themeType],
  );

  const diffClassName =
    "an-edit-diff dark:bg-black dark:[--diffs-bg:#000] dark:[--diffs-bg-buffer-override:#000] dark:[--diffs-bg-context-override:#000] dark:[--diffs-bg-hover-override:#0a0a0a] dark:[--diffs-bg-separator-override:#0f0f0f]";

  return (
    <div className="an-edit-tool-card rounded-an-tool-border-radius border border-an-tool-border-color bg-an-tool-background dark:bg-black overflow-hidden">
      <div
        className={
          // Explicit bg-an-tool-background so the header keeps its light-grey
          // contrast in dark mode — the wrapper forces `dark:bg-black` for the
          // diff body, which would otherwise bleed into the header.
          "flex items-center justify-between px-2.5 py-0 h-7 bg-an-tool-background " +
          (isPending && !diffFiles
            ? ""
            : "border-b border-an-tool-border-color")
        }
      >
        <div className="flex items-center gap-1.5 min-w-0">
          {hasFileName && (
            <FileExtIcon filename={fileName} className="w-3 h-3 shrink-0" />
          )}
          {isPending && !diffFiles ? (
            <TextShimmer as="span" duration={1.2} className="text-xs">
              Generating...
            </TextShimmer>
          ) : isPending ? (
            <TextShimmer as="span" duration={1.2} className="text-xs">
              {isWrite ? "Creating" : "Editing"} {fileName}
            </TextShimmer>
          ) : (
            <span className="text-xs text-an-tool-color-muted truncate">
              {isWrite ? "Created" : "Edited"} {fileName}
            </span>
          )}
        </div>
        {step.diffStats && !isPending && (
          <span className="text-[11px] font-mono text-an-tool-color-muted inline-flex gap-2">
            {step.diffStats.split(" ").map((token) => (
              <span
                key={token}
                className={
                  token.startsWith("+")
                    ? "text-an-diff-added-text"
                    : token.startsWith("-")
                      ? "text-an-diff-removed-text"
                      : undefined
                }
              >
                {token}
              </span>
            ))}
          </span>
        )}
      </div>
      {diffFiles ? (
        <div className={`${diffClassName} text-[12px]`} style={diffCssVars}>
          <div
            className={isCollapsible ? "group/edit-diff relative" : "relative"}
          >
            <div
              className={
                isCollapsible && !isExpanded
                  ? "max-h-[260px] overflow-hidden"
                  : undefined
              }
            >
              <MultiFileDiff
                key={themeType}
                oldFile={diffFiles.oldFile}
                newFile={diffFiles.newFile}
                className={diffClassName}
                style={diffCssVars}
                options={{
                  theme: { dark: "github-dark", light: "github-light" },
                  themeType,
                  unsafeCSS: diffUnsafeCss,
                  diffStyle: "unified",
                  disableFileHeader: true,
                }}
              />
            </div>
            {isCollapsible && (
              <>
                <button
                  type="button"
                  onClick={() => setIsExpanded((prev) => !prev)}
                  aria-label={isExpanded ? "Hide" : "Show more"}
                  className={
                    "group absolute inset-x-0 bottom-0 h-16 flex items-end justify-center pb-2 text-muted-foreground " +
                    (isExpanded
                      ? "bg-transparent"
                      : "bg-linear-to-b from-transparent to-background")
                  }
                >
                  <IconChevronDown
                    className={
                      "w-4 h-4 transition-opacity duration-150 opacity-0 group-hover:opacity-100 " +
                      (isExpanded ? "rotate-180" : "rotate-0")
                    }
                  />
                </button>
              </>
            )}
          </div>
        </div>
      ) : null}
      {approval && <ToolApprovalFooter isPending={isPending} {...approval} />}
    </div>
  );
}

export type EditToolProps = {
  part: any;
  isCollapsible?: boolean;
};

export const EditTool = memo(function EditTool({
  part,
  isCollapsible = false,
}: EditToolProps) {
  const approval = (part.input?.approval ?? part.args?.approval) as
    | ToolApproval
    | undefined;
  const toolName = (part.type as string)?.replace("tool-", "") || "Edit";
  const step = mapToolInvocationToStep(part.toolCallId ?? part.id ?? "edit", {
    toolName,
    args: part.input ?? part.args ?? {},
    state:
      part.state === "output-available"
        ? "result"
        : part.state === "input-streaming"
          ? "partial-call"
          : "call",
    result: part.output ?? part.result,
  });
  const stepState = mapToolStateToStepState(
    part.state === "output-available"
      ? "result"
      : part.state === "input-streaming"
        ? "partial-call"
        : "call",
  );
  const noop = () => {};

  // Write overwrite green/red (F4): the backend `detail.old_capture`
  // carries the pre-write bytes (engine persists it on the sidecar
  // state; the DetailView projects it onto `part.detail`). The chat
  // surface doesn't pass it (no overwrite there); the detail card does.
  const rawDetail = part?.detail as { old_capture?: string | null } | undefined;
  const oldCapture =
    rawDetail && typeof rawDetail.old_capture === "string"
      ? rawDetail.old_capture
      : null;

  return (
    <EditToolDiffCard
      step={step}
      state={stepState}
      onComplete={noop}
      input={part.input ?? part.args}
      output={part.output ?? part.result}
      isCollapsible={isCollapsible}
      approval={approval}
      oldCapture={oldCapture}
    />
  );
});
