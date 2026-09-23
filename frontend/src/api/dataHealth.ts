import http from './index'

export interface DataHealthIssue {
  check: string
  severity: 'error' | 'warning' | 'hint'
  entity_type: string
  entity_id: number
  entity_uid?: string | null
  entity_name: string
  title: string
  detail: string
  suggestion: string
  deep_link?: string | null
  /** 「标注无问题」用：key 定位问题，signature 描述当前事实（数据变则豁免失效） */
  issue_key: string
  signature: string
  /** 曾被标注无问题、但数据已变化 → 重新提示 */
  reopened: boolean
}

export interface DataHealthReport {
  score: number
  counts: { error: number; warning: number; hint: number }
  total_issues: number
  ignored_count: number
  entity_stats: {
    groups: number
    artists: number
    albums: number
    songs: number
    companies: number
    music_videos: number
  }
  checks: { key: string; name: string; severity: string; count: number }[]
  issues: DataHealthIssue[]
  ignored_issues: DataHealthIssue[]
}

export interface HealthIgnoreItem {
  key: string
  signature: string
  check: string
  entity_type: string
  entity_id: number
  title: string
  note: string
  created_at: string
}

export interface PurgeDanglingResult {
  memberships_deleted: number
  mv_subjects_cleared: number
  subunit_parents_cleared: number
  artist_company_relations_deleted: number
  group_company_relations_deleted: number
}

export const dataHealthApi = {
  getReport() {
    return http.get<DataHealthReport>('/data-health').then((r) => r.data)
  },
  purgeDangling() {
    return http.post<PurgeDanglingResult>('/data-health/purge-dangling').then((r) => r.data)
  },
  /** 把某条问题标注为「无问题」：不再提示、不计分；数据变化后会自动重新提示 */
  ignoreIssue(issue: DataHealthIssue) {
    return http
      .post<{ items: HealthIgnoreItem[] }>('/data-health/ignores', {
        key: issue.issue_key,
        signature: issue.signature,
        check: issue.check,
        entity_type: issue.entity_type,
        entity_id: issue.entity_id,
        title: issue.title,
      })
      .then((r) => r.data)
  },
  /** 恢复某条问题的提示 */
  restoreIssue(key: string) {
    return http
      .post<{ items: HealthIgnoreItem[] }>('/data-health/ignores/remove', { key })
      .then((r) => r.data)
  },
  /** 恢复全部提示 */
  clearIgnores() {
    return http
      .post<{ items: HealthIgnoreItem[] }>('/data-health/ignores/clear')
      .then((r) => r.data)
  },
}
