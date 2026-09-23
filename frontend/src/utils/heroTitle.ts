/**
 * 刊头大字的字号分档。首页轮播（HomeCinemaHero）与艺人/组合详情页手机端共用同一套阈值，
 * 避免两处各写一份后漂移。
 *
 * 口径：按「名字本身」的长度分档，**不含任何后缀**。
 * 详情页手机端大字 2026-09-21 起是**单一名字**（艺人取艺名、组合取英文名；
 * 都没有才按各自次序退回中文/韩文名），已不再拼「中文名 (韩文名)」后缀；
 * 量长度时只喂最终渲染的那个名字。
 */

export type HeroNameTier = 'base' | 'xl' | 'long'

const WIDE_CHAR = /[\u3400-\u9fff\uac00-\ud7af]/

export function nameSizeTier(name?: string | null): HeroNameTier {
  const n = (name || '').trim()
  if (!n) return 'base'
  const wide = WIDE_CHAR.test(n)
  // 宽字符（汉字/韩文）天然占地大，≥5 字就已经很长
  if (wide && n.length >= 5) return 'long'
  if (n.length <= 5) return 'xl'
  if (n.length >= 10) return 'long'
  return 'base'
}
