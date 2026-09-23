// 详情页 Hero 占位背景：按实体 id 从固定调色板取渐变色；
// 有头像/海报时改为从图片抽样主色，驱动整页底色。

const HERO_PALETTES: [string, string][] = [
  ['#f472b6', '#8b5cf6'],
  ['#fb923c', '#ef4444'],
  ['#22d3ee', '#3b82f6'],
  ['#a3e635', '#16a34a'],
  ['#facc15', '#f97316'],
  ['#e879f9', '#6366f1'],
]

function hexToRgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16)
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`
}

export function heroGradient(id: number) {
  const [c1, c2] = HERO_PALETTES[id % HERO_PALETTES.length]
  return `linear-gradient(135deg, ${c1}, ${c2})`
}

/** 调色板第一色的 `r, g, b`，给 CSS `rgb(var(--detail-tint))` 当无图时的底色。 */
export function heroRgb(id: number): string {
  return hexToRgb(HERO_PALETTES[id % HERO_PALETTES.length][0])
}

const tintCache = new Map<string, string>()
const panelCache = new Map<string, PanelTint>()

export type PanelTint = {
  /** 近黑刊头底 `r, g, b` */
  panel: string
  /** 同色相短线 `r, g, b` */
  accent: string
}

function sampleCanvas(
  img: HTMLImageElement,
  size: number,
  include: (x: number, y: number, l: number) => boolean,
  skipLowLuma: boolean,
): [number, number, number] | null {
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) return null
  ctx.drawImage(img, 0, 0, size, size)
  let data: Uint8ClampedArray
  try {
    data = ctx.getImageData(0, 0, size, size).data
  } catch {
    return null
  }

  let rSum = 0
  let gSum = 0
  let bSum = 0
  let wSum = 0
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 128) continue
    const px = (i / 4) % size
    const py = Math.floor(i / 4 / size)
    const r = data[i]
    const g = data[i + 1]
    const b = data[i + 2]
    const max = Math.max(r, g, b)
    const min = Math.min(r, g, b)
    const l = (max + min) / 510
    if (l > 0.93) continue
    if (skipLowLuma && l < 0.08) continue
    if (!include(px / size, py / size, l)) continue
    const d = (max - min) / 255
    const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1) || 1)
    const w = 0.25 + s * s
    rSum += r * w
    gSum += g * w
    bSum += b * w
    wSum += w
  }
  if (wSum < 1) return null
  return [Math.round(rSum / wSum), Math.round(gSum / wSum), Math.round(bSum / wSum)]
}

function sampleImageRgb(img: HTMLImageElement): string | null {
  const rgb = sampleCanvas(img, 32, () => true, true)
  if (!rgb) return null
  return `${rgb[0]}, ${rgb[1]}, ${rgb[2]}`
}

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255
  g /= 255
  b /= 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return [0, 0, l]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h = 0
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6
  else if (max === g) h = ((b - r) / d + 2) / 6
  else h = ((r - g) / d + 4) / 6
  return [h, s, l]
}

function hueToRgb(p: number, q: number, t: number) {
  if (t < 0) t += 1
  if (t > 1) t -= 1
  if (t < 1 / 6) return p + (q - p) * 6 * t
  if (t < 1 / 2) return q
  if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
  return p
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) {
    const v = Math.round(l * 255)
    return [v, v, v]
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  return [
    Math.round(hueToRgb(p, q, h + 1 / 3) * 255),
    Math.round(hueToRgb(p, q, h) * 255),
    Math.round(hueToRgb(p, q, h - 1 / 3) * 255),
  ]
}

function crushToPanel(r: number, g: number, b: number): PanelTint {
  const [h, s] = rgbToHsl(r, g, b)
  const panelS = Math.min(Math.max(s * 0.38, 0.04), 0.22)
  const accentS = Math.min(Math.max(s * 0.55, 0.18), 0.5)
  const [pr, pg, pb] = hslToRgb(h, panelS, 0.1)
  const [ar, ag, ab] = hslToRgb(h, accentS, 0.58)
  return {
    panel: `${pr}, ${pg}, ${pb}`,
    accent: `${ar}, ${ag}, ${ab}`,
  }
}

function loadImage(src: string): Promise<HTMLImageElement | null> {
  if (typeof Image === 'undefined') return Promise.resolve(null)
  return new Promise((resolve) => {
    const img = new Image()
    img.decoding = 'async'
    img.onload = () => resolve(img)
    img.onerror = () => resolve(null)
    img.src = src
  })
}

/** 从图片抽饱和主色，返回 `r, g, b`。失败时为 null（调用方回退调色板）。 */
export function extractImageRgb(src: string): Promise<string | null> {
  if (!src) return Promise.resolve(null)
  const cached = tintCache.get(src)
  if (cached) return Promise.resolve(cached)

  return loadImage(src).then((img) => {
    if (!img) return null
    const rgb = sampleImageRgb(img)
    if (rgb) tintCache.set(src, rgb)
    return rgb
  })
}

/** 刊头文字区：取图右缘或下缘主色，压成近黑底 + 同色相短线。 */
export function extractPanelTint(
  src: string,
  region: 'right' | 'bottom' = 'right',
): Promise<PanelTint | null> {
  if (!src) return Promise.resolve(null)
  const key = `${region}:${src}`
  const cached = panelCache.get(key)
  if (cached) return Promise.resolve(cached)

  return loadImage(src).then((img) => {
    if (!img) return null
    const include =
      region === 'bottom'
        ? (_x: number, y: number) => y >= 0.55
        : (x: number) => x >= 0.68
    const sampled = sampleCanvas(img, 48, include, false)
    if (!sampled) return null
    const tint = crushToPanel(sampled[0], sampled[1], sampled[2])
    panelCache.set(key, tint)
    return tint
  })
}

/**
 * 把抽出的主色 `r, g, b` 提到饱和度 / 明度下限，供「强虚化 Hero 背景」的蒙版层用。
 *
 * 浅色写真（白底高调）抽出来接近白灰，直接铺背景会发素、看不出任何色调；
 * 这里补一个下限把它拉回可辨识的淡彩。源色本身无彩色（黑白封面）时返回
 * null，由调用方回退到按实体 id 取的调色板色，避免给灰图硬塞一个色相。
 */
export function vividifyRgb(
  rgb: string,
  sMin = 0.45,
  lMin = 0.64,
  lMax = 0.78,
): string | null {
  const parts = rgb.split(',').map((x) => Number(x.trim()))
  if (parts.length !== 3 || parts.some((n) => !Number.isFinite(n))) return null
  const [h, s, l] = rgbToHsl(parts[0], parts[1], parts[2])
  if (s < 0.12) return null
  const [r, g, b] = hslToRgb(h, Math.max(s, sMin), Math.min(Math.max(l, lMin), lMax))
  return `${r}, ${g}, ${b}`
}
