<script setup lang="ts">
import { computed, ref } from 'vue'
import type { RecapDay } from '@/types/models'

/** GitHub 风格入库热力日历：52 周 × 7 天，颜色深浅 = 当天入库量。 */
const props = defineProps<{ days: RecapDay[] }>()

// 按周分列；首日按周一对齐，前置空白格
const weeks = computed(() => {
  if (!props.days.length) return [] as (RecapDay | null)[][]
  const first = new Date(`${props.days[0].date}T00:00:00`)
  const lead = (first.getDay() + 6) % 7 // 周一=0 … 周日=6
  const cells: (RecapDay | null)[] = [...Array<null>(lead), ...props.days]
  const result: (RecapDay | null)[][] = []
  for (let i = 0; i < cells.length; i += 7) result.push(cells.slice(i, i + 7))
  return result
})

const maxCount = computed(() => Math.max(1, ...props.days.map((d) => d.count)))

// 相对最高值分 4 档着色，小库也能拉开层次
function level(count: number) {
  if (!count) return 0
  const ratio = count / maxCount.value
  if (ratio > 0.66) return 4
  if (ratio > 0.4) return 3
  if (ratio > 0.16) return 2
  return 1
}

const monthLabels = computed(() => {
  const labels: { col: number; text: string }[] = []
  let last = -1
  weeks.value.forEach((week, col) => {
    const firstDay = week.find(Boolean)
    if (!firstDay) return
    const m = new Date(`${firstDay.date}T00:00:00`).getMonth()
    if (m !== last) {
      labels.push({ col, text: `${m + 1}月` })
      last = m
    }
  })
  return labels
})

// ===== 三项指标：单日最多 / 最长连续 / 收藏活跃 =====
const bestDay = computed(() =>
  props.days.reduce((a, b) => (b.count > a.count ? b : a), { date: '', count: 0 }),
)
const activeDays = computed(() => props.days.filter((d) => d.count > 0).length)
const bestStreak = computed(() => {
  let best = 0
  let cur = 0
  for (const d of props.days) {
    cur = d.count > 0 ? cur + 1 : 0
    if (cur > best) best = cur
  }
  return best
})

function fmtDay(iso: string) {
  if (!iso) return ''
  const [y, m, d] = iso.split('-')
  return `${y} 年 ${Number(m)} 月 ${Number(d)} 日`
}

// ===== 悬停浮层（桌面增强；移动端依赖 title 兜底） =====
const rootEl = ref<HTMLElement | null>(null)
const tip = ref<{ x: number; y: number; text: string } | null>(null)

function onCellEnter(day: RecapDay, e: MouseEvent) {
  const box = rootEl.value?.getBoundingClientRect()
  if (!box) return
  // 跟随鼠标，右侧越界时收回容器内
  const x = Math.min(e.clientX - box.left + 14, box.width - 160)
  tip.value = {
    x: Math.max(0, x),
    y: e.clientY - box.top - 8,
    text: day.count > 0 ? `${fmtDay(day.date)} · 入库 ${day.count} 条` : `${fmtDay(day.date)} · 没有入库`,
  }
}

function onCellLeave() {
  tip.value = null
}
</script>

<template>
  <div ref="rootEl" class="hm" @mouseleave="onCellLeave">
    <div class="hm-months">
      <span
        v-for="l in monthLabels"
        :key="l.col"
        class="hm-month"
        :style="{ left: `${24 + l.col * 15}px` }"
      >
        {{ l.text }}
      </span>
    </div>
    <div class="hm-body">
      <div class="hm-weekdays">
        <span>一</span>
        <span>三</span>
        <span>五</span>
        <span>日</span>
      </div>
      <div class="hm-grid">
        <template v-for="(week, wi) in weeks" :key="wi">
          <template v-for="(day, di) in week" :key="di">
            <span
              v-if="day"
              class="hm-cell"
              :class="`hm-l${level(day.count)}`"
              :title="`${fmtDay(day.date)} · 入库 ${day.count} 条`"
              @mouseenter="onCellEnter(day, $event)"
            />
            <span v-else class="hm-cell hm-blank" />
          </template>
        </template>
      </div>
    </div>
    <div v-if="tip" class="hm-tip" :style="{ left: `${tip.x}px`, top: `${tip.y}px` }">
      {{ tip.text }}
    </div>
    <div class="hm-foot">
      <div class="hm-stats">
        <div class="hm-stat">
          <b>{{ bestDay.count }}</b>
          <span>单日最多{{ bestDay.count > 0 ? `（${fmtDay(bestDay.date)}）` : '' }}</span>
        </div>
        <div class="hm-stat">
          <b>{{ bestStreak }}</b>
          <span>最长连续天数</span>
        </div>
        <div class="hm-stat">
          <b>{{ activeDays }}</b>
          <span>收藏活跃天数</span>
        </div>
      </div>
      <span class="hm-legend">
        少
        <i class="hm-cell hm-l0" />
        <i class="hm-cell hm-l1" />
        <i class="hm-cell hm-l2" />
        <i class="hm-cell hm-l3" />
        <i class="hm-cell hm-l4" />
        多
      </span>
    </div>
  </div>
</template>

<style scoped>
.hm {
  min-width: 0;
  position: relative;
}
.hm-months {
  position: relative;
  height: 18px;
  margin-bottom: 4px;
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.hm-month {
  position: absolute;
  top: 0;
  white-space: nowrap;
}
.hm-body {
  display: flex;
  gap: 6px;
}
.hm-weekdays {
  display: grid;
  grid-template-rows: repeat(7, 12px);
  gap: 4px;
  width: 18px;
  font-size: 10px;
  color: var(--sa-text-tertiary);
  text-align: right;
  padding-top: 1px;
}
.hm-weekdays span {
  height: 12px;
  line-height: 12px;
}
.hm-grid {
  display: grid;
  grid-auto-flow: column;
  grid-template-rows: repeat(7, 12px);
  grid-auto-columns: 12px;
  gap: 4px;
  min-width: 0;
  overflow-x: auto;
  padding-bottom: 2px;
}
.hm-cell {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  background: var(--sa-subtle);
  flex: none;
}
.hm-blank {
  background: transparent;
}
.hm-l1 {
  background: color-mix(in srgb, var(--sa-accent) 22%, transparent);
}
.hm-l2 {
  background: color-mix(in srgb, var(--sa-accent) 45%, transparent);
}
.hm-l3 {
  background: color-mix(in srgb, var(--sa-accent) 70%, transparent);
}
.hm-l4 {
  background: var(--sa-accent);
}
.hm-tip {
  position: absolute;
  z-index: 4;
  transform: translateY(-100%);
  pointer-events: none;
  white-space: nowrap;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--sa-text-primary);
  color: var(--sa-bg);
  font-size: 12px;
  line-height: 1.4;
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.18);
}
.hm-foot {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: flex-end;
  gap: 12px 20px;
  margin-top: 18px;
}
.hm-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 36px;
}
.hm-stat {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.hm-stat b {
  font-size: 1.5rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  color: var(--sa-text-primary);
}
.hm-stat span {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.hm-legend {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--sa-text-tertiary);
  font-size: 12px;
}
.hm-legend i {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 3px;
}
@media (max-width: 768px) {
  .hm-month {
    display: none;
  }
  .hm-months {
    height: 4px;
    margin-bottom: 2px;
  }
  .hm-stats {
    gap: 8px 24px;
  }
  .hm-stat b {
    font-size: 1.3rem;
  }
}
</style>
