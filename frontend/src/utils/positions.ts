/** 队内担当：库内保留英文，用户端显示中文。 */

const CANONICAL: { en: string; zh: string; aliases: string[] }[] = [
  { en: 'Leader', zh: '队长', aliases: ['队长', 'leader'] },
  { en: 'Center', zh: '中心', aliases: ['中心', 'center'] },
  { en: 'Visual', zh: '门面', aliases: ['门面', 'visual'] },
  {
    en: 'Face of the Group',
    zh: '团体门面',
    aliases: ['face of the group', 'face', '团体门面', '门面担当'],
  },
  { en: 'Maknae', zh: '忙内', aliases: ['maknae', '忙内', '老幺'] },
  {
    en: 'Main Vocalist',
    zh: '主唱',
    aliases: ['main vocalist', 'main vocal', 'main vocals', '主唱'],
  },
  {
    en: 'Lead Vocalist',
    zh: '领唱',
    aliases: ['lead vocalist', 'lead vocal', 'lead vocals', '领唱'],
  },
  {
    en: 'Sub Vocalist',
    zh: '副唱',
    aliases: ['sub vocalist', 'sub vocal', 'sub vocals', '副唱'],
  },
  { en: 'Vocalist', zh: '歌手', aliases: ['vocalist', 'vocal', 'vocals', '歌手'] },
  {
    en: 'Main Dancer',
    zh: '主舞',
    aliases: ['main dancer', 'main dance', '主舞'],
  },
  {
    en: 'Lead Dancer',
    zh: '领舞',
    aliases: ['lead dancer', 'lead dance', '领舞'],
  },
  { en: 'Dancer', zh: '舞者', aliases: ['dancer', 'dance', '舞者'] },
  {
    en: 'Main Rapper',
    zh: '主rapper',
    aliases: ['main rapper', 'main rap', '主rapper', '主说唱'],
  },
  {
    en: 'Lead Rapper',
    zh: '领rapper',
    aliases: ['lead rapper', 'lead rap', '领rapper', '领说唱'],
  },
  { en: 'Rapper', zh: '说唱', aliases: ['rapper', 'rap', '说唱', 'rapper担当'] },
  { en: 'All-rounder', zh: '全能', aliases: ['all-rounder', 'all rounder', '全能'] },
  { en: 'Producer', zh: '制作人', aliases: ['producer', '制作人'] },
  { en: 'Composer', zh: '作曲', aliases: ['composer', '作曲'] },
  { en: 'Lyricist', zh: '作词', aliases: ['lyricist', '作词'] },
]

function normKey(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/[-_]+/g, ' ')
    .replace(/\s+/g, ' ')
}

const BY_KEY = new Map<string, { en: string; zh: string }>()
for (const row of CANONICAL) {
  const rec = { en: row.en, zh: row.zh }
  BY_KEY.set(normKey(row.en), rec)
  BY_KEY.set(normKey(row.zh), rec)
  for (const a of row.aliases) BY_KEY.set(normKey(a), rec)
}

function lookup(raw: string): { en: string; zh: string } | null {
  const k = normKey(raw)
  if (!k) return null
  return BY_KEY.get(k) ?? null
}

/** 单条担当：用户端中文；未收录的原文原样返回。 */
export function formatPosition(raw: string): string {
  const hit = lookup(raw)
  return hit ? hit.zh : raw.trim()
}

export function formatPositions(
  positions?: string[] | null,
  sep = ' / ',
): string {
  return (positions || []).map(formatPosition).filter(Boolean).join(sep)
}

/** 把输入（中文或英文）归一成库内英文担当。未收录的保留原文。 */
export function parsePositionsInput(raw: string): string[] {
  return raw
    .split(/[,，、/|]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => lookup(s)?.en ?? s)
}
