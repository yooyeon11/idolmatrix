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
  /** YouTube 缺失检测配置（来自博主规则） */
  youtube_channel_url?: string
  missing_check_enabled?: boolean
  /** 缺失检测统计（快照汇总；未检测过为 null） */
  channel_video_total?: number | null
  missing_count?: number | null
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
  /** YouTube 频道地址 / ID（@handle、/channel/UC… 链接或裸 UC ID） */
  youtube_channel_url?: string
  /** 是否启用缺失视频检测 */
  missing_check_enabled?: boolean
}

export interface UploaderRulesResult {
  rules: UploaderRule[]
}

/** 缺失视频条目（来自频道快照，未在库内出现） */
export interface MissingVideoItem {
  video_id: string
  title: string
  duration?: number | null
  published_at?: string | null
  is_short: boolean
  thumbnail_path?: string | null
  url: string
}

/** 缺失视频报告：频道全集快照 − 库内该博主 YouTube 视频 */
export interface MissingReport {
  uploader: string
  /** 该博主库内是否有 YouTube 视频；false 时博主页不渲染缺失区块 */
  has_youtube_videos: boolean
  channel_configured: boolean
  missing_check_enabled: boolean
  /** 命中的规则名与原博主名「肉眼同名但写法不同」时回填（如 MBCKpop 命中 MBC Kpop） */
  matched_rule_name?: string
  channel_video_total: number
  library_count: number
  missing_count: number
  short_missing_count: number
  last_checked_at?: string | null
  items: MissingVideoItem[]
}

/** 频道地址探测结果（「测试」按钮：确认地址能解析到哪个频道） */
export interface ChannelProbeResult {
  channel_id: string
  title: string
  uploads_playlist: string
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
  /** 整体覆盖规则列表（服务端归一化：丢空类型且无频道配置的行 / 去重 / 过滤非法类型） */
  saveRules(rules: UploaderRule[]) {
    return http.put<UploaderRulesResult>('/uploaders/rules', { rules }).then((r) => r.data)
  },
  /** 缺失视频报告（不出网：读快照 + 库内实时比对） */
  missingVideos(uploader: string) {
    return http
      .get<MissingReport>('/uploaders/missing-videos', { params: { uploader } })
      .then((r) => r.data)
  },
  /** 触发一次缺失检测（拉频道完整投稿 → 返回缺失报告） */
  missingCheck(uploader: string) {
    return http
      .post<MissingReport>('/uploaders/missing-check', { uploader })
      .then((r) => r.data)
  },
  /** 缺失视频封面（后端本地缓存，不直连 YouTube 图床；img 直链需带 /api 前缀） */
  missingCoverUrl(videoId: string) {
    return `/api/uploaders/missing-videos/${encodeURIComponent(videoId)}/cover`
  },
  /** 测试频道地址能否解析（出网 1-2 quota，不落库） */
  channelProbe(channel: string) {
    return http
      .post<ChannelProbeResult>('/uploaders/channel-probe', { channel })
      .then((r) => r.data)
  },
}
