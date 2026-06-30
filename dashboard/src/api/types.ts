/* ═══════════════════════════════════════════════════════════════
   LensGate Dashboard — TypeScript 类型定义
   ═══════════════════════════════════════════════════════════════ */

/** 单个 pipeline stage 快照 */
export interface StageSnapshot {
  stage: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  duration_ms: number;
  status: string;
}

/** 完整 trace 记录（详情用，含所有 stage 完整数据） */
export interface TraceRecord {
  id: string;
  timestamp: string;
  method: string;
  path: string;
  source_format: string;
  target_model: string;
  stream: boolean;
  status_code: number;
  total_duration_ms: number;
  original_body: Record<string, unknown>;
  stages: StageSnapshot[];
  replay_of: string | null;
  replays: string[];
}

/** Decision snippet in trace summary */
export interface DecisionSnippet {
  mode: string;
  focus: string;
  hashes: string[];
  reasoning: string;
  status: string;
}

/** Vision snippet for a single image in trace summary */
export interface VisionSnippet {
  hash: string;
  description: string;
}

/** trace 列表摘要（含 stage I/O 片段供卡片视图渲染） */
export interface TraceSummary {
  id: string;
  timestamp: string;
  method: string;
  path: string;
  source_format: string;
  target_model: string;
  has_images: boolean;
  image_count: number;
  status_code: number;
  total_duration_ms: number;
  stream: boolean;
  user_input: string;
  decision_snippet: DecisionSnippet | null;
  vision_snippets: VisionSnippet[] | null;
  target_response_preview: string;
}

/** 决策审计记录 */
export interface DecisionRecord {
  timestamp: string;
  trace_id: string;
  user_messages: string[];
  cached_images_count: number;
  new_image_count: number;
  mode: string;
  hashes: string[];
  focus_prompt: string;
  reasoning: string;
  attempt: number;
  status: string;
}

/** 缓存条目 */
export interface CacheEntry {
  hash: string;
  file_name: string;
  position: number;
  position_label: string;
  label: string;
  summary: string;
  focus_results: Record<string, string>;
}

/** 仪表盘汇总统计 */
export interface Stats {
  total: number;
  success_rate: number;
  avg_duration_ms: number;
  p99_duration_ms: number;
  cache_hit_rate: number;
  total_images: number;
}

/** 通用分页响应 */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

/** 重放响应 */
export interface ReplayResponse {
  replay_id: string;
  timestamp: string;
}
