import http from './index'

export interface HeroStageItem {
  type: 'artist' | 'group'
  id: number
  uid: string
  name: string
  chinese_name?: string | null
  english_name?: string | null
  korean_name?: string | null
  stage_name?: string | null
  group_type?: string | null
  tagline?: string | null
  description?: string | null
  social_media?: Record<string, string> | null
  focus_x?: number | null
  focus_y?: number | null
  has_banner: boolean
  has_avatar: boolean
  video_count: number
  updated_at?: string | null
}

export interface HeroStageResult {
  items: HeroStageItem[]
}

export const homeApi = {
  heroStage(limit = 8) {
    return http
      .get<HeroStageResult>('/home/hero-stage', { params: { limit } })
      .then((r) => r.data)
  },
}
