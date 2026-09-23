// 格式化工具：时长、文件大小、分辨率、日期

import { resolutionLabel } from './resolution'

export function formatDuration(seconds?: number | null): string {
  if (seconds == null || Number.isNaN(seconds)) return '--'
  const s = Math.floor(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  }
  return `${m}:${String(sec).padStart(2, '0')}`
}

export function formatFileSize(bytes?: number | null): string {
  if (bytes == null || Number.isNaN(bytes)) return '--'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = bytes / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(2)} ${units[i]}`
}

/** 分辨率标注：直接给档位（8K/4K/2K/1080P/720P/480P/360P/240P/<240P）。
 *
 *  与统计页构成、浏览页筛选同一套口径（utils/resolution.ts，按短边），
 *  不再用「高清/标清」这类无法核对的模糊措辞；宽高缺失返回 '--'。
 */
export function formatResolution(width?: number | null, height?: number | null): string {
  return resolutionLabel(width, height)
}

export function formatBitrate(bps?: number | null): string {
  if (!bps) return '--'
  const mbps = bps / 1_000_000
  if (mbps >= 1) return `${mbps.toFixed(2)} Mbps`
  return `${(bps / 1000).toFixed(0)} kbps`
}

export function formatDate(dateStr?: string | null): string {
  if (!dateStr) return '--'
  const d = new Date(dateStr)
  if (Number.isNaN(d.getTime())) return String(dateStr)
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

export function formatPhotoDay(day?: string | null): string {
  if (!day) return '日期未知'
  const m = day.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!m) return day
  return `${m[1]}年${Number(m[2])}月${Number(m[3])}日`
}

export function formatPhotoMonth(month?: string | null): string {
  if (!month) return '日期未知'
  const m = month.match(/^(\d{4})-(\d{2})/)
  if (!m) return month
  return `${m[1]}年${Number(m[2])}月`
}

/** 视频关联歌曲显示：多首用 / 连接；songs 缺失时回退单个 song_name 字段 */
export function formatVideoSongs(
  songs?: { chinese_name?: string | null; name?: string }[] | null,
  fallbackName?: string | null,
  fallbackCn?: string | null,
): string {
  const list = (songs || [])
    .map((s) => s.chinese_name || s.name)
    .filter(Boolean)
  return list.join(' / ') || fallbackCn || fallbackName || ''
}

const PLATFORM_RULES: { match: RegExp; name: string }[] = [
  { match: /youtu\.?be/i, name: 'YouTube' },
  { match: /bilibili|b23\.tv|哔哩/i, name: 'Bilibili' },
  { match: /naver|vlive/i, name: 'Naver TV' },
  { match: /weibo/i, name: '微博' },
  { match: /douyin|iesdouyin/i, name: '抖音' },
  { match: /twitter|x\.com/i, name: 'Twitter/X' },
  { match: /instagram/i, name: 'Instagram' },
  { match: /tiktok/i, name: 'TikTok' },
  { match: /facebook|fb\.watch/i, name: 'Facebook' },
  { match: /niconico|nicovideo/i, name: 'Niconico' },
  { match: /vimeo/i, name: 'Vimeo' },
  { match: /dailymotion/i, name: 'Dailymotion' },
  { match: /iqiyi|爱奇艺/i, name: '爱奇艺' },
  { match: /youku|优酷/i, name: '优酷' },
  { match: /tencentvideo|v\.qq\.com/i, name: '腾讯视频' },
]

export function detectPlatform(url?: string | null, extractor?: string | null): string {
  // 优先从链接/提取器关键字判断；extractor 可能是 "youtube"、"bilibili"、"generic" 等
  const blob = `${url ?? ''} ${extractor ?? ''}`
  for (const rule of PLATFORM_RULES) {
    if (rule.match.test(blob)) return rule.name
  }
  if (extractor && !/generic/i.test(extractor)) return extractor
  return '其他'
}

export function formatDateTime(dateStr?: string | null): string {
  if (!dateStr) return '--'
  const d = new Date(dateStr)
  if (Number.isNaN(d.getTime())) return String(dateStr)
  return d.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 名称首字符大写，用作无头像时的占位 */
export function initialOf(name?: string | null) {
  return (name || '?').trim().charAt(0).toUpperCase()
}
