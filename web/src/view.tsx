import { createContext, useContext } from "react";
import type { Focus } from "./types";
import type { Model } from "./model";

export interface Finding {
  severity: "high" | "medium" | "low";
  title: string;
  path: string;
  line: number;
  evidence: string;
  suggestion: string;
  entity?: string | null;
}

export interface ViewState {
  m: Model;
  lit: Set<string> | null;
  focused: boolean;
  selected: string | null;
  reviewed: Set<string>;
  marks: Map<string, "high" | "medium" | "low">;
  act: {
    hover: (ids: string[] | null) => void;
    focus: (f: Focus) => void;
    hide: (key: string, label: string) => void;
    toggleCard: (id: string) => void;
    toggleGroup: (key: string) => void;
    toggleReviewed: (unit: string) => void;
    dragging: () => boolean;
  };
}

export const View = createContext<ViewState | null>(null);
export const useView = () => useContext(View)!;

export function litState(lit: Set<string> | null, ids: string[]): "" | "lit" | "faded" {
  if (!lit) return "";
  return ids.some((id) => lit.has(id)) ? "lit" : "faded";
}

const LANG_COLOR: Record<string, string> = {
  java: "var(--lang-java)",
  kotlin: "var(--lang-java)",
  typescript: "var(--lang-ts)",
  typescriptreact: "var(--lang-ts)",
  javascript: "var(--lang-js)",
  javascriptreact: "var(--lang-js)",
  go: "var(--lang-go)",
  python: "var(--lang-py)",
  c: "var(--lang-c)",
  cpp: "var(--lang-cpp)",
};
export const langColor = (l: string | null | undefined) => (l && LANG_COLOR[l]) || "var(--text-3)";

export function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path d="M5.2 26.2V6.4h6.4a5.2 5.2 0 0 1 0 10.4H5.2" />
      <path d="M10.6 16.8 17.2 26.2" />
      <circle className="logo-dot" cx="11.2" cy="11.6" r="1.45" />
      <path d="M20.4 6.4v19.8H29" />
    </svg>
  );
}

const ICONS: Record<string, string> = {
  search: "M7 2.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zM10.5 10.5 14 14",
  focus: "M2 5V2h3M11 2h3v3M14 11v3h-3M5 14H2v-3M8 6.2a1.8 1.8 0 1 1 0 3.6 1.8 1.8 0 0 1 0-3.6z",
  hide: "M2 8s2.2-4 6-4 6 4 6 4-2.2 4-6 4-6-4-6-4zM3 13 13 3",
  check: "M3.5 8.5 6.5 11.5 12.5 4.5",
  more: "M3.5 8h.01M8 8h.01M12.5 8h.01",
  filter: "M2.5 3.5h11L9.5 8.5v4l-3 1.5V8.5z",
  help: "M6 6a2 2 0 1 1 2.8 1.8c-.5.3-.8.7-.8 1.2v.5M8 11.8h.01",
  close: "M4 4l8 8M12 4l-8 8",
  chevron: "M6 4l4 4-4 4",
  plus: "M8 3.5v9M3.5 8h9",
  minus: "M3.5 8h9",
  fit: "M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10",
  spark: "M8 1.8 9.4 6.6 14.2 8 9.4 9.4 8 14.2 6.6 9.4 1.8 8 6.6 6.6z",
  eye: "M2 8s2.2-4 6-4 6 4 6 4-2.2 4-6 4-6-4-6-4zM8 6.3a1.7 1.7 0 1 1 0 3.4 1.7 1.7 0 0 1 0-3.4z",
  warn: "M8 2.5 14 13H2zM8 6.5v3M8 11.3h.01",
};

export function Icon({ name, size = 14 }: { name: keyof typeof ICONS | string; size?: number }) {
  const strokeW = name === "more" ? 2.6 : 1.5;
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <path d={ICONS[name]} fill={name === "spark" ? "currentColor" : "none"} stroke="currentColor" strokeWidth={strokeW} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
