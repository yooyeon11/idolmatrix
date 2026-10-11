<script setup lang="ts">
/**
 * 统计构成环形图（ECharts 画环 + 自绘 HTML 图例）。
 *
 * 为什么图例不用 ECharts 自带的：分类多时底部图例挤成多行、点/名/数/比参差错行，
 * 移动端尤其乱 —— HTML 网格图例（移动 1 列 / 桌面 2 列）每行列宽对齐，可扫读。
 * 段内百分比只在桌面显示（移动环小，会和中心总数重叠）；两类信息图例里都有。
 * 点击图例行 = 隐藏/恢复该分类，中心总数随可见分类重算。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useEChart } from '@/composables/useEChart'
import { cssVar, type EChartsOption } from '@/utils/echarts'
import { useThemeStore } from '@/stores/theme'

export interface StatsDonutRow {
  name: string
  value: number
  color: string
}

const props = withDefaults(
  defineProps<{
    rows: StatsDonutRow[]
    /** 中心总数下的量词小字（条舞台 / 条影像） */
    centerSub: string
    height?: number
  }>(),
  { height: 250 },
)

const theme = useThemeStore()
const el = ref<HTMLElement | null>(null)

// 隐藏的分类（点图例行切换）；重建 deps 含它，环与中心总数随之更新
const hidden = ref(new Set<string>())
function toggle(name: string) {
  const next = new Set(hidden.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  // 全隐藏没意义：最后一个是唯一切换回来
  if (next.size === props.rows.length) return
  hidden.value = next
}

const visibleRows = computed(() => props.rows.filter((r) => !hidden.value.has(r.name)))
const visibleTotal = computed(() => visibleRows.value.reduce((n, r) => n + r.value, 0))

// 窄容器（移动端）：关段内标签（环小会与中心文字重叠），图例承担全部占比信息
const narrow = ref(false)
let ro: ResizeObserver | null = null
onMounted(() => {
  ro = new ResizeObserver((entries) => {
    const w = entries[0]?.contentRect.width ?? 0
    narrow.value = w > 0 && w < 420
  })
  if (el.value) ro.observe(el.value)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  ro = null
})

function buildOption(): EChartsOption {
  // 扇区留白的描边色 = 页面底色（章节内容直接坐在 --sa-bg 上）
  const bg = cssVar('--sa-bg', '#0e0e12')
  return {
    tooltip: {
      trigger: 'item',
      backgroundColor: '#20242e',
      borderColor: 'rgba(255,255,255,0.1)',
      textStyle: { color: '#f2f4f8', fontSize: 12 },
      formatter: (p) => {
        const d = p as { name: string; value: number; percent: number }
        return `${d.name}：${d.value} 条（${d.percent}%）`
      },
    },
    series: [
      {
        type: 'pie',
        radius: ['54%', '80%'],
        center: ['50%', '50%'],
        padAngle: 3,
        itemStyle: { borderRadius: 8, borderColor: bg, borderWidth: 2 },
        // 段内白字标占比（仅桌面）：窄环标不下且会撞中心文字，移动端交给图例
        label:
          narrow.value || visibleRows.value.length > 8
            ? { show: false }
            : {
                show: true,
                position: 'inside',
                color: '#fff',
                fontWeight: 700,
                fontSize: 13,
                formatter: (p) => {
                  const d = p as { percent: number }
                  return d.percent >= 8 ? `${Math.round(d.percent)}%` : ''
                },
              },
        labelLine: { show: false },
        emphasis: {
          scaleSize: 6,
          itemStyle: { shadowBlur: 16, shadowColor: 'rgba(0,0,0,0.35)' },
        },
        animationDuration: 800,
        data: visibleRows.value.map((r) => ({
          name: r.name,
          value: r.value,
          itemStyle: { color: r.color },
        })),
      },
    ],
  }
}

useEChart(el, buildOption, [
  () => theme.mode,
  () => theme.accent,
  () => props.rows,
  hidden,
  narrow,
])

function pctOf(row: StatsDonutRow): number {
  const total = props.rows.reduce((n, r) => n + r.value, 0)
  return total ? Math.round((row.value / total) * 100) : 0
}
</script>

<template>
  <div class="stats-donut">
    <div class="donut-wrap">
      <div ref="el" class="donut-canvas" :style="{ height: `${height}px` }" />
      <div class="donut-center">
        <b>{{ visibleTotal }}</b>
        <span>{{ centerSub }}</span>
      </div>
    </div>
    <ul class="donut-legend">
      <li
        v-for="r in props.rows"
        :key="r.name"
        :class="{ off: hidden.has(r.name) }"
        @click="toggle(r.name)"
      >
        <i :style="{ background: r.color }" />
        <span class="lg-name">{{ r.name }}</span>
        <b>{{ r.value }}</b>
        <em>{{ pctOf(r) }}%</em>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.stats-donut {
  width: 100%;
  min-width: 0;
}
.donut-wrap {
  position: relative;
}
.donut-canvas {
  width: 100%;
  min-width: 0;
}
/* 中心数字:HTML 覆盖层,与环心(['50%','50%'])精确对中(ECharts title 块内左对齐会导致视觉偏移) */
.donut-center {
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  text-align: center;
  pointer-events: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.donut-center b {
  font-size: 24px;
  font-weight: 800;
  line-height: 1.1;
  color: var(--sa-text-primary);
  font-variant-numeric: tabular-nums;
}
.donut-center span {
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
/* 自绘图例：行内四列（点/名/数/比）对齐；移动 1 列、≥520px 2 列 */
.donut-legend {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: grid;
  grid-template-columns: 1fr;
  gap: 2px 22px;
}
@media (min-width: 520px) {
  .donut-legend {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.donut-legend li {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) auto auto;
  gap: 8px;
  align-items: center;
  font-size: 13px;
  padding: 4px 6px;
  border-radius: 8px;
  cursor: pointer;
  transition: opacity 0.15s, background 0.12s;
}
.donut-legend li:hover {
  background: var(--sa-hover);
}
.donut-legend li.off {
  opacity: 0.38;
}
.donut-legend li.off .lg-name {
  text-decoration: line-through;
}
.donut-legend i {
  width: 10px;
  height: 10px;
  border-radius: 99px;
}
.lg-name {
  color: var(--sa-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.donut-legend b {
  color: var(--sa-text-primary);
  font-variant-numeric: tabular-nums;
}
.donut-legend em {
  color: var(--sa-text-tertiary);
  font-style: normal;
  font-variant-numeric: tabular-nums;
  min-width: 34px;
  text-align: right;
}
</style>
