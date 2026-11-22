export type ChangeClass =
  | "signature"
  | "removed"
  | "renamed"
  | "value"
  | "type"
  | "body"
  | "added"
  | "cosmetic";

export type EdgeKind = "call" | "ref" | "dispatch" | "implements";
export type Confidence = "precise" | "possible" | "degraded";

export interface Breaking {
  line: number;
  message: string;
  source?: string;
}

export interface ImpactNode {
  id: string;
  path: string;
  name: string;
  qualname: string;
  kind: string;
  lines: [number, number];
  change: ChangeClass | null;
  depth: number;
  score: number;
  via: EdgeKind | null;
  parent: string | null;
  seeds: string[];
  confidence: Confidence;
  reasons: string[];
  breaking: Breaking[];
  truncated: Record<string, { total: number; shown: number }> | null;
  signature?: { before: string | null; after: string | null };
  renamed_from?: string | null;
  lines_changed?: number;
  on_base?: boolean;
  notes?: string[];
  impacted?: number;
}

export interface ImpactEdge {
  source: string;
  target: string;
  kind: EdgeKind;
  sites: number[];
}

export interface FileEntry {
  path: string;
  old_path: string | null;
  status: "A" | "M" | "D" | "R";
  language: string | null;
  server: string | null;
  added: number;
  removed: number;
  entities: string[];
  module_lines: [number, number][];
  degraded: string[];
  impacted_files: { path: string; score: number }[];
}

export interface ServerEntry {
  name: string;
  language: string;
  status: "ok" | "degraded" | "unavailable";
  reason?: string | null;
  version?: string | null;
  ready_seconds?: number;
  project_notes?: string[];
}

export interface Impact {
  schema: string;
  tool: { name: string; version: string };
  generated_at: string;
  repo: {
    root: string;
    analyzed_root: string;
    base: { ref: string; sha: string };
    head: { ref: string; sha: string | null };
  };
  servers: ServerEntry[];
  summary: {
    files_changed: number;
    entities_changed: number;
    by_change: Partial<Record<ChangeClass, number>>;
    impacted: number;
    impacted_files: number;
    breaking: number;
    degraded: number;
    truncated: boolean;
  };
  files: FileEntry[];
  nodes: ImpactNode[];
  edges: ImpactEdge[];
  paths: Record<string, string[]>;
  groups?: GroupEntry[];
  locate?: Record<string, { group: string; unit: string; test: boolean }>;
  truncation: { at: string; reason: string; total?: number; shown?: number }[];
  blind_spots: Record<string, string[]>;
  warnings: string[];
  timing: { total_seconds: number; requests: number; request_seconds: number; timeouts: number };
}

export interface GroupEntry {
  id: string;
  module: string;
  module_path: string;
  package: string;
  language: string | null;
  units: { id: string; name: string; files: string[]; test: boolean }[];
}

/** What the board is focused on (click) and what the drawer shows. */
export type Focus =
  | { type: "entity"; id: string }
  | { type: "unit"; id: string }
  | { type: "impactFile"; group: string; path: string }
  | { type: "group"; id: string; side: "changed" | "impact" }
  | { type: "overview" }
  | null;
