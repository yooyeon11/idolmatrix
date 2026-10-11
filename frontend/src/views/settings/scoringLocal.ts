/** AI 视频评分（实验功能）的本地配置。
 *
 * 刻意不走后端 app_settings：实验模块独立自持（localStorage + 随请求带凭据），
 * 功能下线 = 删组件 + 删这个文件即可，无迁移负担。
 */

export const SCORING_AI_KEY = 'kpml_scoring_ai_settings'

export type FrameMode = 'auto' | '8' | '12' | '16'

export interface ScoringAiSettings {
  enabled: boolean
  provider: string
  base_url: string
  api_key: string
  model: string
  /** 抽帧策略：auto=按时长自适应（6~16 帧），8/12/16=固定档位 */
  frames_mode: FrameMode
}

export const scoringAiDefaults: ScoringAiSettings = {
  enabled: false,
  provider: 'openai-compatible',
  base_url: '',
  api_key: '',
  model: '',
  frames_mode: 'auto',
}

export function loadScoringAi(): ScoringAiSettings {
  const raw = localStorage.getItem(SCORING_AI_KEY)
  if (!raw) return { ...scoringAiDefaults }
  try {
    return { ...scoringAiDefaults, ...(JSON.parse(raw) as Partial<ScoringAiSettings>) }
  } catch {
    return { ...scoringAiDefaults }
  }
}

export function saveScoringAi(s: ScoringAiSettings) {
  localStorage.setItem(SCORING_AI_KEY, JSON.stringify(s))
}

/** 出站配置：未启用或配置不全返回 null（详情页据此提示去设置）。 */
export function readScoringAiConfig(): (ScoringAiSettings & { model: string }) | null {
  const cfg = loadScoringAi()
  if (!cfg.enabled || !cfg.base_url.trim() || !cfg.model.trim()) return null
  return cfg
}
