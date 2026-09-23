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

export interface HeroFancamItem {
  id: number
  uid: string
  name: string
  video_type?: string | null
  video_types?: string[] | null
  thumbnail_path?: string | null
  file_hash?: string | null
  duration?: number | null
  performance_date?: string | null
}

export interface HeroFancamResult {
  items: HeroFancamItem[]
}

export const homeApi = {
  heroStage(limit = 8) {
    return http
      .get<HeroStageResult>('/home/hero-stage', { params: { limit } })
      .then((r) => r.data)
  },
  heroFancams(type: 'artist' | 'group', id: number, limit = 4) {
    return http
      .get<HeroFancamResult>(`/home/hero-stage/${type}/${id}/fancams`, { params: { limit } })
      .then((r) => r.data)
  },
}
