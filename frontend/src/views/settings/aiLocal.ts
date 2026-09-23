export const INGEST_AI_KEY = 'kpml_ingest_ai_settings'
export const LEGACY_AI_KEY = 'kpml_ai_settings'

export interface AiSettings {
  enabled: boolean
  provider: string
  base_url: string
  api_key: string
  model: string
  // 原「自动化能力」三开关（auto_describe/auto_tag/auto_subject）为死设置，已随 UI 一并移除
  // 艺人/组合一句话简介提示词（AI 设置页可编辑；为空时用后端默认）
  tagline_prompt: string
  // 视频中文简介（chinese_description）规则提示词（AI 设置页可编辑；为空时用后端默认）
  desc_prompt: string
  api_key_set?: boolean
}

export interface IngestAiConfig {
  provider: string
  base_url: string
  api_key: string
  model: string
  enabled: boolean
  tagline_prompt: string
  desc_prompt: string
}

// 与后端 ENTITY_TAGLINE_DEFAULT_PROMPT 保持一致
export const ENTITY_TAGLINE_DEFAULT_PROMPT = `你是 K-pop 音乐杂志的编辑，为一位艺人或组合写一句说明文字。

要求：
- tagline：中文一句话，16–24 字。点出这个人物最鲜明的标签或气质（声线、风格、身份、代表性），像画册图注，不像百科。
- 禁止：出道日期罗列、成员名单、奖项流水账、「著名」「知名」「备受喜爱」等空洞形容、句号结尾。
- 语气克制、有画面感，允许一个轻微的比喻。

只返回一个 JSON 对象，不要输出任何其他文字或代码块标记：
{"tagline": ""}`

// 与后端 ai_service.DEFAULT_DESC_RULE 保持一致（**整行**，含字段名前缀）。
// 「恢复默认」= 填回这段文本；出站时若与它逐字相同则改存空串（= 跟随后端内置默认）。
export const INGEST_DESC_DEFAULT_PROMPT =
  '- chinese_description: 中文简介（用中文根据视频内容生成 2-4 句话，介绍这是什么演出/舞台/节目、表演者与曲目等；不要照抄原始简介原文）'

/** 展示用：空串（= 用后端默认）统一填回默认文本，编辑框里看得见。 */
export function withDescPromptFallback(ai: AiSettings): AiSettings {
  const text = (ai.desc_prompt || '').trim()
  return text ? { ...ai } : { ...ai, desc_prompt: INGEST_DESC_DEFAULT_PROMPT }
}

/** 出站用：与默认文本一致 → 存空串，让后端继续用内置默认（后端升级默认时能自动跟随）。 */
export function outboundDescPrompt(text: string): string {
  return (text || '').trim() === INGEST_DESC_DEFAULT_PROMPT.trim() ? '' : text
}

type AiSnapshot = {
  ingest_ai: AiSettings
}

let serverSnapshot: AiSnapshot | null = null

export function setAiSnapshot(s: AiSnapshot | null) {
  serverSnapshot = s
}

export const aiDefaults: AiSettings = {
  enabled: false,
  provider: 'openai-compatible',
  base_url: '',
  api_key: '',
  model: '',
  tagline_prompt: ENTITY_TAGLINE_DEFAULT_PROMPT,
  desc_prompt: INGEST_DESC_DEFAULT_PROMPT,
}

export function loadAi(): AiSettings {
  if (serverSnapshot?.ingest_ai) {
    return withDescPromptFallback(
      stripLegacyAiFields({ ...aiDefaults, ...serverSnapshot.ingest_ai }),
    )
  }
  const raw = localStorage.getItem(INGEST_AI_KEY) ?? localStorage.getItem(LEGACY_AI_KEY)
  if (!raw) return { ...aiDefaults }
  try {
    return withDescPromptFallback(
      stripLegacyAiFields({ ...aiDefaults, ...(JSON.parse(raw) as Partial<AiSettings>) }),
    )
  } catch {
    return { ...aiDefaults }
  }
}

export function readIngestAiConfig(): IngestAiConfig | null {
  const cfg = loadAi()
  if (!cfg.enabled || !cfg.base_url || !cfg.model) return null
  return cfg
}

// 已下线的「自动化能力」三开关：老 localStorage 里可能还留着，读取时剔除
const LEGACY_AUTO_FIELDS = ['auto_describe', 'auto_tag', 'auto_subject'] as const

/** 去掉已移除的「自动化能力」三开关残留键，避免把死字段再 PUT 回后端。 */
export function stripLegacyAiFields<T extends object>(ai: T): T {
  const out = { ...ai } as Record<string, unknown>
  for (const k of LEGACY_AUTO_FIELDS) delete out[k]
  return out as T
}

/** 出站时去掉空 Key，让后端用已保存的密钥。 */
export function outboundAiCreds(cfg: {
  provider: string
  base_url: string
  api_key?: string
  model: string
}) {
  const api_key = (cfg.api_key || '').trim()
  return {
    provider: cfg.provider,
    base_url: cfg.base_url,
    model: cfg.model,
    ...(api_key ? { api_key } : {}),
  }
}

function withoutKey<T extends { api_key?: string }>(obj: T): T {
  return { ...obj, api_key: '' }
}

export function saveAi(ai: AiSettings) {
  const clean = stripLegacyAiFields(ai)
  if (serverSnapshot) serverSnapshot.ingest_ai = { ...clean }
  localStorage.setItem(INGEST_AI_KEY, JSON.stringify(withoutKey(clean)))
}

export function clearAi() {
  if (serverSnapshot) serverSnapshot.ingest_ai = { ...aiDefaults }
  localStorage.removeItem(INGEST_AI_KEY)
  localStorage.removeItem(LEGACY_AI_KEY)
}
