import http from './index'
import type { Pagination } from '@/types/models'

export interface UploaderItem {
  name: string
  platform?: string | null
  video_count: number
  latest_video_id?: number | null
  /** 已固定的视频类型（空 = 跟随 AI 判断） */
  video_types?: string[]
  /** false = 仅存在于规则里，库内还没有 TA 的作品 */
  in_library?: boolean
}

export interface UploaderListParams {
  q?: string
  page?: number
  page_size?: number
}

/** 博主管理列表：库内博主 ∪ 已配置规则的博主 */
export interface UploaderManageResult {
  items: UploaderItem[]
  total: number
  rule_count: number
}

export interface UploaderRule {
  name: string
  video_types: string[]
}

export interface UploaderRulesResult {
  rules: UploaderRule[]
}

export const uploadersApi = {
  list(params: UploaderListParams = {}) {
    return http.get<Pagination<UploaderItem>>('/uploaders', { params }).then((r) => r.data)
  },
  /** 博主管理：全量列表（不分页），含每行当前固定的视频类型 */
  manage(q?: string) {
    return http
      .get<UploaderManageResult>('/uploaders/manage', { params: q ? { q } : {} })
      .then((r) => r.data)
  },
  rules() {
    return http.get<UploaderRulesResult>('/uploaders/rules').then((r) => r.data)
  },
  /** 整体覆盖规则列表（服务端归一化：丢空类型 / 去重 / 过滤非法类型） */
  saveRules(rules: UploaderRule[]) {
    return http.put<UploaderRulesResult>('/uploaders/rules', { rules }).then((r) => r.data)
  },
}
