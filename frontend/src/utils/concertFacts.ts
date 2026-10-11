/**
 * 演唱会类比参数 —— 统计页把「总观看时长」换算成「几场演唱会 / 几次巡演」用。
 *
 * 不同组合的常规演唱会时长差很多（全员大团一场 4 小时起，小团 2 小时上下，
 * 差 1~2 小时换算出的场次数就完全不同），一轮巡演的场次规模也差一倍不止，
 * 而类比冠名是「年度主角组合」，所以参数必须按组合取。
 *
 * 数值按近年公开巡演的典型口径整理（近似值，够类比用、不追求精确）：
 * hours = 常规一场演唱会时长（小时）；tourShows = 一轮巡演的典型场次数。
 * 查不到的组合回退 DEFAULT_PROFILE（与旧版写死值一致）。
 */

export interface ConcertProfile {
  /** 常规一场演唱会时长（小时） */
  hours: number
  /** 一轮巡演典型场次数 */
  tourShows: number
}

export const DEFAULT_CONCERT_PROFILE: ConcertProfile = { hours: 2.5, tourShows: 30 }

/** 归一化组合名：小写 + 去空白/标点/符号（le-sserafim→lesserafim、(G)I-DLE→gidle、nct 127→nct127） */
function normalizeName(raw: string): string {
  return raw
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[\p{P}\p{S}\s_]+/gu, '')
}

// key 为归一化后的组合名（含常用中文名），value = { 常规场时长, 巡演规模 }
const PROFILES: Record<string, ConcertProfile> = {
  // ===== 大团 =====
  bts: { hours: 3.0, tourShows: 60 },
  防弹少年团: { hours: 3.0, tourShows: 60 },
  twice: { hours: 3.0, tourShows: 40 },
  blackpink: { hours: 2.5, tourShows: 60 },
  粉墨: { hours: 2.5, tourShows: 60 },
  seventeen: { hours: 3.5, tourShows: 35 },
  exo: { hours: 3.0, tourShows: 30 },
  girlsgeneration: { hours: 3.0, tourShows: 20 },
  snsd: { hours: 3.0, tourShows: 20 },
  少女时代: { hours: 3.0, tourShows: 20 },
  superjunior: { hours: 3.0, tourShows: 30 },
  nct: { hours: 3.0, tourShows: 40 },
  nct127: { hours: 3.0, tourShows: 40 },
  nctdream: { hours: 3.0, tourShows: 40 },
  wayv: { hours: 2.5, tourShows: 20 },
  威神v: { hours: 2.5, tourShows: 20 },
  straykids: { hours: 3.0, tourShows: 45 },
  txt: { hours: 3.0, tourShows: 35 },
  tomorrowxtogether: { hours: 3.0, tourShows: 35 },
  enhypen: { hours: 2.5, tourShows: 30 },
  ateez: { hours: 3.0, tourShows: 35 },
  theboyz: { hours: 2.5, tourShows: 30 },
  treasure: { hours: 2.5, tourShows: 20 },
  day6: { hours: 2.5, tourShows: 30 },
  // ===== 四代~五代女团 =====
  redvelvet: { hours: 2.5, tourShows: 30 },
  红贝贝: { hours: 2.5, tourShows: 30 },
  mamamoo: { hours: 2.5, tourShows: 30 },
  ohmygirl: { hours: 2.0, tourShows: 10 },
  宇宙少女: { hours: 2.5, tourShows: 15 },
  wjsn: { hours: 2.5, tourShows: 15 },
  izone: { hours: 2.5, tourShows: 15 },
  fromis9: { hours: 2.0, tourShows: 10 },
  gidle: { hours: 2.5, tourShows: 25 },
  aespa: { hours: 2.0, tourShows: 30 },
  stayc: { hours: 2.0, tourShows: 20 },
  ive: { hours: 2.5, tourShows: 30 },
  newjeans: { hours: 2.0, tourShows: 15 },
  lesserafim: { hours: 2.0, tourShows: 20 },
  nmixx: { hours: 2.0, tourShows: 15 },
  kep1er: { hours: 2.0, tourShows: 20 },
  viviz: { hours: 2.0, tourShows: 10 },
  itzy: { hours: 2.0, tourShows: 25 },
  illit: { hours: 2.0, tourShows: 10 },
  kissoflife: { hours: 2.0, tourShows: 15 },
  babymonster: { hours: 2.0, tourShows: 10 },
  // ===== 六代/大型企划 =====
  triples: { hours: 4.0, tourShows: 30 },
  zerobaseone: { hours: 2.5, tourShows: 20 },
  zb1: { hours: 2.5, tourShows: 20 },
  riize: { hours: 2.5, tourShows: 20 },
  boynextdoor: { hours: 2.0, tourShows: 15 },
  // ===== 其它常见 =====
  shinee: { hours: 2.5, tourShows: 30 },
  bigbang: { hours: 2.5, tourShows: 20 },
  got7: { hours: 2.5, tourShows: 20 },
  astro: { hours: 2.5, tourShows: 15 },
  apink: { hours: 2.5, tourShows: 15 },
}

/**
 * 按组合名取演唱会类比参数：依次尝试传入的候选名（如 name 与 chinese_name），
 * 命中即返回；全部未命中回退通用默认。
 */
export function concertProfile(...names: Array<string | null | undefined>): ConcertProfile {
  for (const raw of names) {
    if (!raw) continue
    const profile = PROFILES[normalizeName(raw)]
    if (profile) return profile
  }
  return DEFAULT_CONCERT_PROFILE
}

/** 小时数展示：4 → "4"，2.5 → "2.5"（避免 "4.0 小时" 这种别扭写法） */
export function formatHours(h: number): string {
  return String(Math.round(h * 10) / 10)
}
