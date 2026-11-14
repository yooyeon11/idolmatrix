/**
 * 图片变体宽度档位：按「展示宽度 × 屏幕像素比」取档交给服务端出缩放图。
 *
 * 服务端只缩不放（源图比请求宽度还小则回退原图），所以这里放心取偏大的一档，
 * 不会把低分辨率素材放大糊掉。档位集中在这里定义，改一处即可全套生效。
 *
 * 依据（现有 CSS 实测）：
 *   - 列表/宫格：列宽 180~320 CSS px，DPR 2 → 360~640 物理像素
 *   - 首页海报墙：手机 3 列 / 桌面 6 列，约 120~200 CSS px
 *   - 满屏 hero：手机整屏宽 390 CSS px、DPR 3 ≈ 1170；PC 刊头图片区约 845 CSS px、DPR 2 ≈ 1690
 */
export const IMG_W_THUMB = 320
export const IMG_W_CARD = 480
export const IMG_W_FULL = 1280

/** 取色用的极小图：抽取主色只需 48×48 采样，不必下载大图。 */
export const IMG_W_TINT = 160

/** 满屏 hero 的取图档位上限，必须与后端 `img_variant._MAX_W` 保持一致。 */
export const IMG_W_HERO_MAX = 2560

/**
 * 满屏 hero（组合/艺人详情首屏、首页左侧通顶海报）的取图宽度。
 *
 * 这类图铺满整个视口：1440 桌面就要 1440 CSS px，DPR 2 时是 2880 物理像素。
 * 原先固定 1280（艺人页甚至写死 480）会被浏览器拉到 1500~2700 px，
 * 实测 1280 源在 1920 屏放大 1.58×、2560 屏放大 2.1×，观感就是「糊 / 过度放大」。
 *
 * 按「视口宽 × DPR」落三档 1280 / 1920 / 2560；服务端只缩不放，
 * 源图更小时自动回退原图，所以取大档不会有副作用。
 */
export function heroImageWidth(): number {
  // 首页主舞台是「压暗 + 蒙版」的展示图，1280 已足够清晰；
  // 再往上（1920/2560）单张 300KB+，切轮动时会明显顿。
  const vw = typeof window === 'undefined' ? 1440 : window.innerWidth || 1440
  const dpr = typeof window === 'undefined' ? 1 : Math.min(window.devicePixelRatio || 1, 2)
  return vw * dpr >= 900 ? IMG_W_FULL : IMG_W_CARD * 1.5
}

/** 给图片 URL 追加 `w` 参数（自动判断已有 query）。 */
export function withImageWidth(url: string, w: number): string {
  if (!url) return ''
  return `${url}${url.includes('?') ? '&' : '?'}w=${w}`
}
