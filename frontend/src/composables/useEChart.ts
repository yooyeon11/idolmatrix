/**
 * 生命周期托管的 ECharts 渲染组合式。
 *
 * 挂载时初始化、deps（数据 / 主题）变化时整体重建选项、容器尺寸变化自动 resize、
 * 卸载时 dispose。build() 每次现读 CSS 变量，主题切换后颜色自动跟上。
 */
import { onBeforeUnmount, onMounted, watch, type Ref, type WatchSource } from 'vue'
import { echarts, type EChartsOption } from '@/utils/echarts'

export function useEChart(
  el: Ref<HTMLElement | null>,
  build: () => EChartsOption,
  deps: WatchSource[] = [],
) {
  let chart: ReturnType<typeof echarts.init> | null = null
  let ro: ResizeObserver | null = null
  let lastW = 0

  function render() {
    if (!el.value) return
    if (!chart) {
      chart = echarts.init(el.value)
      // 容器可能是 v-if 晚挂载的:在真正 init 时才挂 ResizeObserver
      ro?.disconnect()
      lastW = el.value.clientWidth
      ro = new ResizeObserver(() => {
        if (!chart) return
        const w = el.value?.clientWidth ?? 0
        if (w !== lastW) {
          // 宽度变了(断点切换):重建选项,option 里按宽度做的分支才会跟上
          lastW = w
          render()
        }
        // init 时的画布像素尺寸不会随容器自动变,重建后也要 resize 铺满
        chart.resize()
      })
      ro.observe(el.value)
    }
    // notMerge：数据 / 主题整体重建，避免旧 series 残留
    chart.setOption(build(), true)
  }

  onMounted(() => {
    render()
  })

  if (deps.length) {
    // post:等 v-if 容器真实挂载后再渲染（pre-flush 会在 DOM 更新前空跑）
    watch(deps, () => render(), { flush: 'post' })
  }

  onBeforeUnmount(() => {
    ro?.disconnect()
    ro = null
    chart?.dispose()
    chart = null
  })

  return {
    rerender: render,
    getInstance: () => chart,
  }
}
