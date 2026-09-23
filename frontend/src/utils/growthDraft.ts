/** Persist per-group growth wizard draft in localStorage (P0.5 — no server table). */

export type GrowthDraft = {
  step: number
  urls: string[]
  completedSteps: number[]
  updatedAt: string
}

const KEY_PREFIX = 'idolmatrix:growth:'

export function growthDraftKey(uid: string): string {
  return `${KEY_PREFIX}${uid}`
}

function clampStep(n: unknown): number {
  const v = typeof n === 'number' ? n : Number(n)
  if (!Number.isFinite(v) || v < 1) return 1
  if (v > 6) return 6
  return Math.floor(v)
}

export function loadGrowthDraft(uid: string): GrowthDraft | null {
  if (!uid || typeof localStorage === 'undefined') return null
  try {
    const raw = localStorage.getItem(growthDraftKey(uid))
    if (!raw) return null
    const data = JSON.parse(raw) as Partial<GrowthDraft>
    const urls = Array.isArray(data.urls)
      ? data.urls.filter((u): u is string => typeof u === 'string' && u.startsWith('http'))
      : []
    const completedSteps = Array.isArray(data.completedSteps)
      ? [...new Set(data.completedSteps.map((s) => clampStep(s)).filter((s) => s >= 1 && s <= 6))]
      : []
    return {
      step: clampStep(data.step ?? 1),
      urls,
      completedSteps,
      updatedAt: typeof data.updatedAt === 'string' ? data.updatedAt : new Date().toISOString(),
    }
  } catch {
    return null
  }
}

export function saveGrowthDraft(
  uid: string,
  draft: { step: number; urls: string[]; completedSteps: number[] },
): GrowthDraft | null {
  if (!uid || typeof localStorage === 'undefined') return null
  const next: GrowthDraft = {
    step: clampStep(draft.step),
    urls: draft.urls.filter((u) => typeof u === 'string' && u.startsWith('http')),
    completedSteps: [
      ...new Set(draft.completedSteps.map((s) => clampStep(s)).filter((s) => s >= 1 && s <= 6)),
    ].sort((a, b) => a - b),
    updatedAt: new Date().toISOString(),
  }
  try {
    localStorage.setItem(growthDraftKey(uid), JSON.stringify(next))
    return next
  } catch {
    return null
  }
}
