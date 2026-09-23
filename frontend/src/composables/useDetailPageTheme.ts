import { computed, ref, watch } from 'vue'
import { extractImageRgb, heroRgb, vividifyRgb } from '@/utils/heroPalette'

export type DetailHeroKind = 'avatar' | 'banner'

/**
 * 详情页主图口径：**固定头像优先**。
 * 原「头像 / 横幅海报 / 测试」三选一设置已移除，这里不再读设置；
 * 保留横幅分支只为「有横幅、没头像」的实体兜底，避免主视觉开天窗。
 */
export function resolveHeroKind(hasAvatar: boolean, hasBanner: boolean): DetailHeroKind {
  if (hasAvatar) return 'avatar'
  if (hasBanner) return 'banner'
  return 'avatar'
}

/** 主站艺人/组合详情页：头像（无头像回退横幅）作主图，并从该图抽色做整页底色。 */
export function useDetailPageTheme(opts: {
  entityId: () => number
  hasAvatar: () => boolean
  hasBanner: () => boolean
  avatarSrc: () => string
  bannerSrc: () => string
}) {
  const heroKind = computed(() => resolveHeroKind(opts.hasAvatar(), opts.hasBanner()))

  const coverSrc = computed(() => {
    if (heroKind.value !== 'avatar' && opts.bannerSrc()) return opts.bannerSrc()
    if (opts.avatarSrc()) return opts.avatarSrc()
    return opts.bannerSrc()
  })

  const tintSrc = computed(() => {
    const raw = coverSrc.value
    if (!raw) return ''
    return `${raw}${raw.includes('?') ? '&' : '?'}w=64`
  })

  const extractedRgb = ref<string | null>(null)

  watch(
    tintSrc,
    async (src) => {
      if (!src) {
        extractedRgb.value = null
        return
      }
      const rgb = await extractImageRgb(src)
      if (tintSrc.value === src) extractedRgb.value = rgb
    },
    { immediate: true },
  )

  // 提纯色：给「强虚化 Hero 背景」的蒙版层用。抽出的主色偏浅灰时拉回淡彩，
  // 完全无彩色（黑白封面）则回退到按 id 取的调色板色，保证背景一定有色可看。
  const vividTint = computed(
    () => (extractedRgb.value && vividifyRgb(extractedRgb.value)) || heroRgb(opts.entityId()),
  )

  const pageStyle = computed(() => ({
    '--detail-tint': extractedRgb.value || heroRgb(opts.entityId()),
    '--detail-tint-vivid': vividTint.value,
  }) as Record<string, string>)

  return { heroKind, coverSrc, pageStyle }
}
