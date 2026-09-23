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

/** 给图片 URL 追加 `w` 参数（自动判断已有 query）。 */
export function withImageWidth(url: string, w: number): string {
  if (!url) return ''
  return `${url}${url.includes('?') ? '&' : '?'}w=${w}`
}
