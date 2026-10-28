// AgentDoctor API types. Mirrors backend/src/agentdoctor/models.py (the source of truth).
// All `*_s` fields are seconds relative to the session's recording start.
// `null` means the source report did not contain the value. Never fill it in.

export type Speaker = "user" | "agent";

export type Category =
  | "speech_recognition"
  | "llm_behavior"
  | "conversation_state"
  | "turn_taking"
  | "tool_execution"
  | "missing_or_incorrect_info"
  | "latency";

export type Severity = "high" | "medium" | "low";
/** Is the failure itself directly observed in the data ("confirmed"), or only suspected? */
export type FindingStatus = "confirmed" | "suspected";
/** How well the data supports the stated root cause. */
export type RootCauseConfidence = "confirmed" | "likely" | "possible";

export type EventType =
  | "agent_state"
  | "user_state"
  | "false_interruption"
  | "overlapping_speech"
  | "session_close";

// ---------- sessions ----------

export interface Models {
  stt: string | null;
  llm: string | null;
  tts: string | null;
  turn_detector: string | null;
  interruption_detector: string | null;
}

export interface LatencyStats {
  n: number;
  median_s: number | null;
  p90_s: number | null; // nearest-rank p90
}

export interface SessionStats {
  duration_s: number | null;
  user_turns: number;
  agent_turns: number;
  e2e_latency: LatencyStats;
  llm_ttft: LatencyStats;
  tts_ttfb: LatencyStats;
  end_of_turn_delay: LatencyStats;
  endpointing_timeouts: number;
  false_interruptions: number;
  interrupted_agent_turns: number;
  overlapping_speech_events: number;
}

export interface SessionSummary {
  id: string;
  title: string;
  notes: string | null;
  pipeline_label: string | null; // "workshop" | "recommended" | "hybrid" | null
  models: Models;
  synthetic: boolean;
  stats: SessionStats;
}

export interface SourceInfo {
  format: "livekit_session_report";
  sdk_version: string | null;
  original_name: string;
  imported_at: string; // ISO 8601
  synthetic: boolean;
}

export interface SessionConfig {
  allow_interruptions: boolean | null;
  min_interruption_duration_s: number | null;
  min_endpointing_delay_s: number | null;
  max_endpointing_delay_s: number | null;
  preemptive_generation: boolean | null;
}

export interface TurnMetrics {
  e2e_latency_s: number | null; // agent turns
  end_of_turn_delay_s: number | null; // user turns
  transcription_delay_s: number | null; // user turns
  llm_ttft_s: number | null; // agent turns
  tts_ttfb_s: number | null; // agent turns
  playback_latency_s: number | null; // agent turns
}

export interface Turn {
  id: string; // "t007"
  index: number;
  speaker: Speaker;
  text: string;
  start_s: number | null; // user turns: may be anchored to an earlier voice-activity blip
  end_s: number | null;
  committed_s: number | null; // agent: reply created (before audio); user: after final transcript
  interrupted: boolean;
  transcript_confidence: number | null; // user turns only
  metrics: TurnMetrics;
}

export interface TimelineEvent {
  id: string; // "e042"
  t_s: number;
  type: EventType;
  // agent_state / user_state: { from: string; to: string }
  // false_interruption:       { resumed: boolean }
  // overlapping_speech:       { is_interruption: boolean; probability: number | null; detection_delay_s: number | null }
  // session_close:            { reason: string; error: string | null }
  data: Record<string, unknown>;
}

export interface Session extends SessionSummary {
  source: SourceInfo;
  config: SessionConfig;
  speaker_labels: Record<Speaker, string>;
  agent_instructions: string | null;
  turns: Turn[];
  events: TimelineEvent[];
  usage: Record<string, unknown>[];
}

// ---------- findings ----------

export interface Evidence {
  kind: "transcript" | "metric" | "event" | "config";
  label: string;
  value: string;
  turn_id: string | null; // link to Turn.id
  event_id: string | null; // link to TimelineEvent.id
  t_s: number | null;
}

export interface RootCause {
  statement: string;
  confidence: RootCauseConfidence;
  missing_evidence: string[];
}

export interface Finding {
  id: string;
  session_id: string;
  detector: string;
  title: string;
  summary: string;
  category: Category;
  severity: Severity;
  status: FindingStatus;
  time_range: { start_s: number; end_s: number } | null;
  turn_ids: string[];
  evidence: Evidence[];
  root_cause: RootCause;
  next_steps: string[];
}

export interface FindingsResponse {
  session_id: string;
  detectors_run: string[];
  findings: Finding[];
}

// ---------- comparison ----------

export interface ComparedMetric {
  key: string;
  label: string;
  unit: "s" | "count";
  a: number | null;
  b: number | null;
  better: "lower" | "higher" | null; // null: no "better" direction
}

export interface Comparison {
  a: SessionSummary;
  b: SessionSummary;
  same_models: Record<"stt" | "llm" | "tts", boolean>;
  metrics: ComparedMetric[];
  findings_by_category: Record<string, { a: number; b: number }>;
  caveats: string[];
}

export interface Health {
  status: "ok";
  version: string;
  detectors: string[];
}
