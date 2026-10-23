import { useEffect, useRef } from "react";
import { type ChatModel, type LiveWorker, type Message, type ToolCall } from "../api";
import Markdown from "./Markdown";
import WorkerCard from "./WorkerCard";

export type Status = "running" | "queued" | "done" | "interrupted";

export const LABELS: Record<string, string> = {
  find_parts: "Searched parts",
  save_model: "Saved model",
  write_file: "Wrote file",
  render_model: "Rendered",
  run_python: "Ran Python",
  run_shell: "Ran shell",
  list_files: "Listed files",
  read_file: "Read file",
  run_toolkit: "LDraw Nova",
  publish_model: "Published model",
  view_image: "Reviewed image",
  report_progress: "Progress",
};

export function parseArgs(raw: string): Record<string, unknown> {
  try {
    return JSON.parse(raw || "{}");
  } catch {
    return { raw };
  }
}

export function summary(name: string, args: Record<string, unknown>): string {
  const pick = (k: string) => (typeof args[k] === "string" ? (args[k] as string) : "");
  switch (name) {
    case "run_toolkit":
      return Array.isArray(args.arguments) ? args.arguments.join(" ") : "";
    case "report_progress":
      return pick("summary");
    case "run_python":
      return pick("code").split("\n").find((l) => l.trim() && !l.trim().startsWith("#"))?.trim() ?? "";
    case "run_shell":
      return pick("command");
    case "save_model":
      return pick("name");
    case "render_model":
    case "publish_model":
    case "view_image":
    case "read_file":
    case "write_file":
    case "list_files":
      return pick("path");
    default:
      return pick("query");
  }
}

function prettyArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => {
      const text = typeof v === "string" ? v : JSON.stringify(v);
      return text.includes("\n") ? `${k}:\n${text}` : `${k}: ${text}`;
    })
    .join("\n");
}

export const isFailed = (output: string) => output.startsWith("Error:") || /^\[(?:timed out|exit code [1-9])/.test(output);
export const resultText = (result?: Message) => typeof result?.content === "string" ? result.content : "";

export default function ToolCard({
  call,
  result,
  status,
  live,
  worker,
  chatId = "",
  eurPerUsd,
  now = Date.now(),
  attempts = 1,
  earlier = [],
}: {
  call: ToolCall;
  result?: Message;
  status: Status;
  models?: ChatModel[];
  live?: { output?: string; started_at?: number };
  worker?: LiveWorker;
  chatId?: string;
  eurPerUsd?: number;
  now?: number;
  attempts?: number;
  earlier?: Message[];
}) {
  const live_ = status === "running" ? live?.output?.slice(-4000) : undefined;
  const liveRef = useRef<HTMLPreElement>(null);
  useEffect(() => { const el = liveRef.current; if (el) el.scrollTop = el.scrollHeight; }, [live_]);
  const name = call.function.name;
  const args = parseArgs(call.function.arguments);
  if (name === "delegate") return <WorkerCard chatId={chatId} args={args} result={result} status={status} live={worker} eurPerUsd={eurPerUsd} />;
  const output = resultText(result);
  const failed = isFailed(output);
  if (name === "report_progress") {
    const text = summary(name, args);
    return text ? <div className="progress-callout"><Markdown>{text}</Markdown></div> : null;
  }

  return (
    <div className={`tool${live_ ? " has-live" : ""}`}>
      <details>
        <summary>
          <span className={`tool-status ${failed ? "failed" : status}`} aria-hidden />
          <span className="tool-name">{LABELS[name] ?? name}</span>
          {attempts > 1 && <span className="tool-retries" title={`${attempts} attempts`}>×{attempts}{status === "done" ? (failed ? ", last failed" : ", last ok") : ""}</span>}
          <span className="tool-summary ellipsis">{summary(name, args)}</span>
          {status === "running" && <span className="muted small">{live?.started_at ? `${Math.max(0, Math.floor(now / 1000 - live.started_at))}s` : "running…"}</span>}
          {status === "interrupted" && <span className="muted small">interrupted</span>}
        </summary>
        <div className="tool-body">
          <div className="tool-section">Arguments</div>
          <pre>{prettyArgs(args)}</pre>
          {result && (
            <>
              <div className="tool-section">Result</div>
              <pre className={failed ? "failed" : ""}>{output}</pre>
            </>
          )}
          {earlier.length > 0 && (
            <>
              <div className="tool-section">Earlier attempts</div>
              {earlier.map((m, i) => <pre key={i} className={isFailed(resultText(m)) ? "failed" : ""}>{resultText(m).slice(0, 1200)}</pre>)}
            </>
          )}
        </div>
      </details>
      {live_ && <pre className="live-tool-output" ref={liveRef}>{live_}</pre>}
    </div>
  );
}
