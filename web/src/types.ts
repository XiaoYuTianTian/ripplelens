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
    untracked_skipped?: number;
    entities_changed: number;
    by_change: Partial<Record<ChangeClass, number>>;
    impact_analyzed?: boolean;
    impacted: number | null;
    impacted_files: number | null;
    impacted_by_via?: Record<string, number>;
    edges_by_kind?: Record<string, number>;
    breaking: number;
    degraded: number;
    truncated: boolean;
    truncation_hubs?: number;
    truncation_budget?: number;
  };
  files: FileEntry[];
  nodes: ImpactNode[];
  edges: ImpactEdge[];
  paths: Record<string, string[]>;
  groups?: GroupEntry[];
  locate?: Record<string, { group: string; unit: string; test: boolean }>;
  truncation: { at: string; reason: string; total?: number; shown?: number }[];
  architecture?: Architecture | null;
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

export interface ArchDep {
  from_module: string;
  from_package: string;
  from_dir: string;
  to_module: string;
  to_package: string;
  to_dir: string;
  to_file: string | null;
  external: boolean;
  cross_module: boolean;
  file: string;
  line: number;
  import: string;
  code: string;
}

export interface Architecture {
  rules: { source: string | null; error?: string; layers: string[]; deny: { from: string; to: string[]; reason: string }[] };
  modules?: string[];
  deps_added: ArchDep[];
  deps_removed: ArchDep[];
  contracts: { id: string; qualname: string; change: ChangeClass; file: string; line: number; module: string; callers: number; external_modules: string[]; compile_errors: number }[];
  cycles: string[][];
  violations: { rule: "deny" | "layer"; detail: string; reason: string; from: string; to: string; file: string; line: number; import: string; code: string }[];
  summary: { deps_added: number; deps_removed: number; cross_module_added: number; external_added: number; contracts_crossing: number; cycles: number; violations: number; has_rules: boolean };
}

export type Severity = "high" | "medium" | "low";

export interface AgentFinding {
  id: string;
  severity: Severity;
  kind: "architecture" | "contract" | "behavior";
  title: string;
  path: string;
  line: number;
  scope: string;
  entity: string | null;
  evidence: string;
  suggestion: string;
  status: "open" | "accepted" | "rejected";
  comment: string;
}

export interface AgentReview {
  range: string;
  agent: { author: string; summary: string; verdict: "approve" | "comment" | "request_changes"; submitted_at: string };
  findings: AgentFinding[];
  human: { verdict: "approve" | "request_changes" | null; note: string; updated_at: string | null };
  stale: boolean;
  counts: { total: number; open: number; accepted: number; rejected: number };
}

/** What the board is focused on (click) and what the drawer shows. */
export type Focus =
  | { type: "entity"; id: string }
  | { type: "unit"; id: string }
  | { type: "impactFile"; group: string; path: string }
  | { type: "group"; id: string; side: "changed" | "impact" }
  | { type: "overview" }
  | null;
