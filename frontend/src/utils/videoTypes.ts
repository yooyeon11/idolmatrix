// 视频 type 多值/单值统一处理：供艺人、组合等详情页共用

export const MV_TYPES = ['OfficialMV', 'Teaser', 'SpecialStage']

export const LIVE_TYPES = ['PerformanceVideo', 'Fancam', 'SpecialStage', 'CollabStage', 'CoverStage', 'MixEdit']

export function videoTypeList(v: { video_type?: string | null; video_types?: string[] | null }) {
  if (v.video_types && v.video_types.length) return v.video_types
  return v.video_type ? [v.video_type] : []
}

export function videoHasType(v: { video_type?: string | null; video_types?: string[] | null }, allowed: string[]) {
  return videoTypeList(v).some((t) => allowed.includes(t))
}

// 短视频判定唯一口径：只看视频类型是否含 ShortVideo，与时长无关（与后端 video_meta.is_short_video 对齐）
export function isShortVideo(v: { video_type?: string | null; video_types?: string[] | null }) {
  return videoTypeList(v).includes('ShortVideo')
}
