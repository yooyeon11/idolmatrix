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
  // 主标题（name）规范提示词（AI 设置页可编辑；为空时用后端默认）
  title_prompt: string
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
  title_prompt: string
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

// 与后端 ai_service.DEFAULT_TITLE_RULE 保持一致（整段多行文本）。
// 「恢复默认」= 填回这段文本；出站时若与它逐字相同则改存空串（= 跟随后端内置默认）。
export const INGEST_TITLE_DEFAULT_PROMPT = `📛 标题（name）规范：YYMMDD Artist - Song [Event Name Type]

1️⃣ YYMMDD（日期前缀）：6 位日期（如 240511）。优先取表演日期（performance_date），无则取上传日期（upload_date）。

2️⃣ 艺人名（Artist）
- 团体/组合表演 → 填入 团体英文名（如 tripleS、Red Velvet）。
- 个人直拍/Focus Cam → 必须且只能填入 成员的英文艺名/本名（如 Kim YooYeon、Irene）。
- ❌ 严禁拼合为「团体-成员」或「团体 成员」格式。
- 若「库内已选关联」中已有艺人或组合，优先使用其中的官方名称。

3️⃣ 歌曲名（Song）
- 必须优先使用官方英文曲名，严禁对韩文曲名进行机器直译。
- 例：은하수를 여행하는 히치하이커를 위한 안내서 → 必须用 The Hitchhiker's Guide to the Galaxy
- 若「库内已选关联」中已有歌曲，优先使用其中的官方曲名。

4️⃣ 多曲目连接
- 若为串烧/拼盘/多首歌曲，用 & 连接（如 Song A & Song B）。

5️⃣ 后缀括号 [Event Name Type] 特殊标识
- 括号内为「活动/节目名 + 视频类型」，**写到视频类型词即止**。
- 时长 < 1 分钟（duration < 60）→ 必须包含 Shorts（如 [FanCam Shorts]）。
- ❌ 括号内禁止写画质 / 帧率 / 编码等来源技术标签：4K、8K、60p、4K60p、8K60p、1080p、2160p、60fps、HEVC 之类一律不写。
- video_info 的 width / height / fps 只用于判断视频类型与时长分档，**严禁写进 name**。
- ❌ 来源标题 / 文件名里自带的画质标签（如「[4K] Artist - Song MV_240102」「… 4K60p」）同样必须丢弃，不得带进 name。`

/** 展示用：空串（= 用后端默认）统一填回默认文本，编辑框里看得见。 */
export function withPromptFallbacks(ai: AiSettings): AiSettings {
  const desc = (ai.desc_prompt || '').trim()
  const title = (ai.title_prompt || '').trim()
  return {
    ...ai,
    desc_prompt: desc ? ai.desc_prompt : INGEST_DESC_DEFAULT_PROMPT,
    title_prompt: title ? ai.title_prompt : INGEST_TITLE_DEFAULT_PROMPT,
  }
}

/** 出站用：与默认文本一致 → 存空串，让后端继续用内置默认（后端升级默认时能自动跟随）。 */
export function outboundDescPrompt(text: string): string {
  return (text || '').trim() === INGEST_DESC_DEFAULT_PROMPT.trim() ? '' : text
}

/** 出站用：标题规范与默认文本一致 → 存空串（同上）。 */
export function outboundTitlePrompt(text: string): string {
  return (text || '').trim() === INGEST_TITLE_DEFAULT_PROMPT.trim() ? '' : text
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
  title_prompt: INGEST_TITLE_DEFAULT_PROMPT,
}

export function loadAi(): AiSettings {
  if (serverSnapshot?.ingest_ai) {
    return withPromptFallbacks(
      stripLegacyAiFields({ ...aiDefaults, ...serverSnapshot.ingest_ai }),
    )
  }
  const raw = localStorage.getItem(INGEST_AI_KEY) ?? localStorage.getItem(LEGACY_AI_KEY)
  if (!raw) return { ...aiDefaults }
  try {
    return withPromptFallbacks(
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
