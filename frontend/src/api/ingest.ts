import http from './index'

/** 清单里的实体引用 */
export interface ManifestEntity {
  id: number
  name: string
}

export interface ManifestNewEntity {
  kind: 'artist' | 'group' | 'song' | string
  name: string
  reason?: string
}

export interface ManifestEvidence {
  field: string
  source: string
  text: string
}

export interface IngestManifestPayload {
  name?: string
  video_type?: string
  performance_date?: string | null
  event_name?: string | null
  uploader?: string | null
  duration?: number | null
  width?: number | null
  height?: number | null
  groups?: ManifestEntity[]
  artists?: ManifestEntity[]
  songs?: ManifestEntity[]
  new_entities?: ManifestNewEntity[]
  evidence?: ManifestEvidence[]
  warnings?: string[]
  description?: string | null
  chinese_name?: string | null
  chinese_description?: string | null
  camera_angle?: string | null
  session_label?: string | null
  is_solo?: boolean | null
  notes?: string | null
  video_types?: string[]
  original_title?: string | null
  published_date?: string | null
  destination_rel?: string | null
  path_notice?: string | null
  suggested_tracks?: { song?: string; albums?: string[] }[]
}

export interface IngestManifest {
  id: number
  file_path: string
  status: 'draft' | 'ready' | 'accepted' | 'rejected' | 'failed' | string
  source: 'draft' | 'agent' | 'ai' | string
  confidence?: number | null
  payload?: IngestManifestPayload | null
  generated_at?: string | null
  /** 本次清单的 token 用量（最近一次 Agent 运行） */
  run?: {
    tokens_in: number
    tokens_out: number
    duration_ms: number
    model?: string | null
    steps?: number
  } | null
}

export interface IngestConfig {
  draft_enabled: boolean
  ai_button_enabled: boolean
  agent_enabled: boolean
  agent_schedule: string[]
  /** 内置 Agent（容器内直连 API）测试开关 */
  builtin_enabled: boolean
  builtin_max_steps: number
  builtin_batch: number
  builtin_model: string
  agent_base_url: string
  agent_api_key: string
  agent_model: string
  /** 外部数据源 */
  qqmusic_enabled: boolean
  qqmusic_api_key: string
  qqmusic_base_url: string
  web_search_enabled: boolean
  harness_enabled: boolean
  harness_interval_minutes: number
}

export const ingestApi = {
  config: () => http.get<IngestConfig>('/ingest/config').then((r) => r.data),

  updateConfig: (patch: Partial<IngestConfig>) =>
    http.put<IngestConfig>('/ingest/config', patch).then((r) => r.data),

  manifestByPath: (filePath: string) =>
    http
      .get<IngestManifest | null>('/ingest/manifest', { params: { file_path: filePath } })
      .then((r) => r.data),

  rebuildDraft: (filePath: string) =>
    http
      .post<IngestManifest>('/ingest/draft', null, { params: { file_path: filePath } })
      .then((r) => r.data),

  accept: (id: number) => http.post(`/ingest/manifests/${id}/accept`).then((r) => r.data),

  reject: (id: number) => http.post(`/ingest/manifests/${id}/reject`).then((r) => r.data),

  request: (body: { scope: string; target?: string; stage?: string }) =>
    http.post('/ingest/requests', body).then((r) => r.data),

  applyManifestPatch: (manifestId: number, fields: Record<string, unknown>) =>
    http
      .post(`/ingest/manifests/${manifestId}/patch`, { fields })
      .then((r) => r.data),

  testQQMusic: (keyword: string) =>
    http
      .post<{ ok: boolean; keyword: string; items: { kind: string; name: string; singer?: string }[] }>(
        '/ingest/integrations/qqmusic/test',
        null,
        { params: { keyword } },
      )
      .then((r) => r.data),
}
