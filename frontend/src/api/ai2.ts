import http from './index'

export interface Ai2Step {
  step: string
  ok: boolean
  detail: string
  data?: Record<string, unknown> | null
}

export interface Ai2TaskRun {
  id: string
  name: string
  status: 'running' | 'done' | 'failed' | 'skipped'
  steps: Ai2Step[]
  duration_ms: number
  error: string | null
  result: {
    steps?: Ai2Step[]
    evidence?: Record<string, unknown>
    report?: string
    llm_verified?: boolean
    /** artist_create 专用 */
    created?: boolean
    created_id?: number
    reason?: 'name_conflict' | string
    draft?: Record<string, unknown>
    verify_ok?: boolean
    /** ingest_audit 专用 */
    imflow_ok?: boolean
    findings_total?: number
    rule_proposals?: string[]
    /** ingest_suggest 专用 */
    suggestion?: Record<string, unknown>
    verify_issues?: string[]
    /** memory_distill 专用 */
    distilled?: boolean
    before_chars?: number
    after_chars?: number
    backup?: string
  } | null
}

export interface Ai2KnowledgeStatus {
  skills: { name: string; title: string; chars: number }[]
  memory: { text: string; chars: number; needs_distill: boolean; limit: number }
  pending_rules: Ai2PendingRule[]
  reports_count: number
  distill: { needs_distill: boolean; chars: number; limit: number } | null
}

export interface Ai2PendingRule {
  id: string
  kind: string
  draft: string
  rationale: string
  source_task: string
  status: string
  created_at: string
  decision_note?: string
}

export interface Ai2ToolDesc {
  name: string
  title: string
  description: string
  readonly: boolean
}

export interface Ai2Status {
  enabled: boolean
  knowledge: Ai2KnowledgeStatus
  tasks: { name: string; title: string }[]
  tools: Ai2ToolDesc[]
}

export interface Ai2Config {
  enabled: boolean
  provider: string
  base_url: string
  api_key: string
  model: string
  step_models: Record<string, string>
}

export interface Ai2Batch {
  batch: string
  defined: boolean
  kind?: string
  archived?: boolean
  declared_items?: number
  has_plan?: boolean
  planned_items?: number
  has_verify?: boolean
  verify_summary?: Record<string, unknown> | null
  mtime?: number
  parse_error?: boolean
}

/** 轮询后台任务直到结束（后端 POST /run 立即返回 running 快照）。 */
async function waitAi2Run(
  first: Ai2TaskRun,
  timeoutMs = 15 * 60_000,
  intervalMs = 1_500,
): Promise<Ai2TaskRun> {
  if (first.status !== 'running') return first
  const startedAt = Date.now()
  let last = first
  while (Date.now() - startedAt < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
    try {
      last = await ai2Api.getRun(first.id)
    } catch {
      continue
    }
    if (last.status !== 'running') return last
  }
  return last
}

export const ai2Api = {
  status: () => http.get<Ai2Status>('/ai2/status').then((r) => r.data),
  startTask: (name: string, params: Record<string, unknown> = {}) =>
      http.post<Ai2TaskRun>(`/ai2/tasks/${name}/run`, { params }).then((r) => r.data),
    runTask: (name: string, params: Record<string, unknown> = {}) =>
      ai2Api.startTask(name, params).then((run) => waitAi2Run(run)),
  getRun: (runId: string) => http.get<Ai2TaskRun>(`/ai2/tasks/runs/${runId}`).then((r) => r.data),
  pendingRules: () => http.get<Ai2PendingRule[]>('/ai2/rules/pending').then((r) => r.data),
  decideRule: (ruleId: string, approve: boolean, note = '') =>
    http.post<Ai2PendingRule>(`/ai2/rules/${ruleId}/decide`, { approve, note }).then((r) => r.data),
  reports: (limit = 50) =>
    http.get<{ file: string; size: number }[]>('/ai2/knowledge/reports', { params: { limit } }).then((r) => r.data),
  report: (file: string) =>
    http.get<{ file: string; html: string }>(`/ai2/knowledge/reports/${encodeURIComponent(file)}`).then((r) => r.data),
  imflowBatches: () => http.get<{ batches: Ai2Batch[] }>('/ai2/imflow/batches').then((r) => r.data.batches),
  rules: (status?: string) =>
    http.get<Ai2PendingRule[]>('/ai2/rules', { params: status ? { status } : {} }).then((r) => r.data),
  mergeRule: (ruleId: string) =>
    http.post<Ai2PendingRule>(`/ai2/rules/${ruleId}/merge`, {}).then((r) => r.data),
}
