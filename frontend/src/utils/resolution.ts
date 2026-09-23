// 分辨率档位唯一口径（与后端 app/services/video_meta.py::RESOLUTION_TIERS 逐项对应）。
//
// 两条硬规则：
// 1. 按「短边」min(width, height) 归档 —— 竖屏直拍 1080×1920 属 1080P，不是 4K。
// 2. 低清不合并成笼统的「标清」：360P / 240P 各自成档，比 240P 更低的（144P 等）
//    才落到 sd 兜底档。显示的档位必须能跟视频的真实分辨率对上，不用「高清/标清」
//    这类无法核对的模糊词。
//
// 后端改名/增删档位时，这里必须同步（统计页构成、浏览页筛选项、详情页标注三处都读它）。

export interface ResolutionTier {
  /** 与后端一致的小写档位键，用于过滤参数与 resolution_breakdown */
  key: string
  /** 展示用标签 */
  label: string
  /** 短边下限（含） */
  floor: number
}

export const RESOLUTION_TIERS: ResolutionTier[] = [
  { key: '8k', label: '8K', floor: 4000 },
  { key: '4k', label: '4K', floor: 1800 },
  { key: '2k', label: '2K', floor: 1400 },
  { key: '1080p', label: '1080P', floor: 1000 },
  { key: '720p', label: '720P', floor: 700 },
  { key: '480p', label: '480P', floor: 400 },
  { key: '360p', label: '360P', floor: 360 },
  { key: '240p', label: '240P', floor: 240 },
  { key: 'sd', label: '<240P', floor: 1 },
]

export const RESOLUTION_UNKNOWN_KEY = 'unknown'
export const RESOLUTION_UNKNOWN_LABEL = '未探测'

/** 按短边归档成档位标签；宽高缺一或非法 → '--'（不猜档位）。 */
export function resolutionLabel(width?: number | null, height?: number | null): string {
  const w = Number(width) || 0
  const h = Number(height) || 0
  if (w <= 0 || h <= 0) return '--'
  const short = Math.min(w, h)
  const hit = RESOLUTION_TIERS.find((t) => short >= t.floor)
  return hit ? hit.label : '--'
}
