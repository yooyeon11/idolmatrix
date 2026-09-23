import { computed, ref } from 'vue'

interface DbQueryRow {
  completeness: number
  issues: { error: number; warning: number; hint: number }
}

interface DbListQueryOptions<T> {
  /** 全量行（服务端已按问题优先排好序） */
  rows: () => T[]
  /** 参与搜索匹配的字段 */
  searchKeys: (row: T) => (string | null | undefined)[]
  /** 名称排序用的键 */
  nameKey?: (row: T) => string
  /** 日期排序用的键（如发行日期） */
  dateKey?: (row: T) => string | null
  /** 影像关联数：提供后默认隐藏无影像关联的行，可用 showNoVideo 切换 */
  videoKey?: (row: T) => number
}

/**
 * 资料库列表页的客户端搜索 + 排序 + 影像关联过滤。
 * default = 服务端顺序（问题多 → 完整度低），搜索大小写不敏感。
 */
export function useDbListQuery<T extends DbQueryRow>(opts: DbListQueryOptions<T>) {
  const search = ref('')
  const sort = ref('default')
  const showNoVideo = ref(false)

  function matchSearch(list: T[]) {
    const q = search.value.trim().toLowerCase()
    if (!q) return list
    return list.filter((r) =>
      opts.searchKeys(r).some((v) => (v ?? '').toLowerCase().includes(q)),
    )
  }

  const hiddenNoVideo = computed(() => {
    if (!opts.videoKey || showNoVideo.value) return 0
    return matchSearch(opts.rows()).filter((r) => opts.videoKey!(r) <= 0).length
  })

  const rows = computed(() => {
    let list = matchSearch(opts.rows())
    if (opts.videoKey && !showNoVideo.value) {
      list = list.filter((r) => opts.videoKey!(r) > 0)
    }
    const copy = () => [...list]
    switch (sort.value) {
      case 'name':
        return copy().sort((a, b) =>
          (opts.nameKey?.(a) ?? '').localeCompare(opts.nameKey?.(b) ?? ''),
        )
      case 'comp-desc':
        return copy().sort((a, b) => b.completeness - a.completeness)
      case 'comp-asc':
        return copy().sort((a, b) => a.completeness - b.completeness)
      case 'date-desc':
        return copy().sort((a, b) =>
          (opts.dateKey?.(b) ?? '').localeCompare(opts.dateKey?.(a) ?? ''),
        )
      case 'date-asc':
        return copy().sort((a, b) =>
          (opts.dateKey?.(a) ?? '').localeCompare(opts.dateKey?.(b) ?? ''),
        )
      default:
        return list
    }
  })

  return { search, sort, showNoVideo, hiddenNoVideo, rows }
}

export const DB_SORTS_BASE = [
  { value: 'default', label: '默认 · 问题优先' },
  { value: 'name', label: '按名称' },
  { value: 'comp-desc', label: '完整度 高→低' },
  { value: 'comp-asc', label: '完整度 低→高' },
]

export const DB_SORTS_WITH_DATE = [
  ...DB_SORTS_BASE,
  { value: 'date-desc', label: '发行日期 新→旧' },
  { value: 'date-asc', label: '发行日期 旧→新' },
]
