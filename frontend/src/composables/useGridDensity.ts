import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'

export type GridDensity = 'compact' | 'standard' | 'loose'

const STORAGE_KEY = 'kpml_grid_density'
const DENSITIES: GridDensity[] = ['compact', 'standard', 'loose']

function resolveDensity(): GridDensity {
  const saved = localStorage.getItem(STORAGE_KEY)
  return DENSITIES.includes(saved as GridDensity) ? (saved as GridDensity) : 'standard'
}

// 模块级单例：所有页面共享同一份密度偏好，切换一次全局生效
const density = ref<GridDensity>(resolveDensity())

export function useGridDensity() {
  function setDensity(value: GridDensity) {
    density.value = value
    localStorage.setItem(STORAGE_KEY, value)
  }
  return { density, setDensity }
}

// 实测 CSS Grid 当前列数：auto-fill 布局下列数随视口宽度连续变化，
// 断点映射无法覆盖所有宽度，必须读取计算样式才能保证 page_size 与列数对齐
export function useGridColumns(target: Ref<HTMLElement | null>) {
  // 初始 0：保证首次实测后值必然变化，触发调用方对 pageSize 的 watch（含首次加载）
  const cols = ref(0)
  let observer: ResizeObserver | null = null

  function measure() {
    const el = target.value
    if (!el) return
    const template = getComputedStyle(el).gridTemplateColumns
    if (!template || template === 'none') return
    const tracks = template.split(' ').filter(Boolean)
    if (tracks.length > 0) cols.value = tracks.length
  }

  function ensureObserver(): ResizeObserver | null {
    if (observer) return observer
    if (typeof ResizeObserver === 'undefined') return null
    observer = new ResizeObserver(measure)
    return observer
  }

  // flush: 'post' + immediate：挂载后才执行，兼容 v-if 场景下元素晚于组件出现的情况
  watch(
    target,
    (el, prev) => {
      const obs = ensureObserver()
      if (!obs) return
      if (prev && prev !== el) obs.unobserve(prev)
      if (el) {
        obs.observe(el)
        nextTick(measure)
      }
    },
    { immediate: true, flush: 'post' }
  )

  onMounted(() => {
    window.addEventListener('resize', measure)
    nextTick(measure)
  })

  onBeforeUnmount(() => {
    observer?.disconnect()
    observer = null
    window.removeEventListener('resize', measure)
  })

  // 切换密度档位会改变网格定义，DOM 更新后重新测量
  watch(density, () => nextTick(measure))

  return { cols }
}

// 列数 × 行数 = page_size，保证整页恰好铺满网格、最后一行不缺位。
// cols 初始为 0（首次测量完成前 pageSize 为 0），调用方须判空后再发起请求
export function useGridPageSize(target: Ref<HTMLElement | null>, maxPageSize = 200) {
  const { cols } = useGridColumns(target)
  const rows = ref(window.innerWidth < 768 ? 8 : 4)

  function syncRows() {
    rows.value = window.innerWidth < 768 ? 8 : 4
  }

  onMounted(() => window.addEventListener('resize', syncRows))
  onBeforeUnmount(() => window.removeEventListener('resize', syncRows))

  const pageSize = computed(() => Math.min(maxPageSize, cols.value * rows.value))

  return { cols, rows, pageSize }
}
