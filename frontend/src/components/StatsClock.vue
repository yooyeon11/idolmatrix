<script setup lang="ts">
/**
 * 观看时间（Last.fm CHARTS 形态）：星期分布 + 时段分布两张图并排。
 *
 * 星期 = 极简柱状；时段 = Last.fm LISTENING CLOCK 同款放射状时钟（24 片
 * 花瓣绕钟面一圈，00 在顶顺时针，瓣长 ∝ 观看次数，峰时全强调色）。两张图
 * 旁边都配大字统计块（最活跃星期 / 最活跃时段）。主题切换整图重建。
 */
import { computed, ref } from 'vue'
import { useEChart } from '@/composables/useEChart'
import { cssVar, hexToRgba, type EChartsOption } from '@/utils/echarts'
import { useThemeStore } from '@/stores/theme'
import type { RecapHour } from '@/types/models'

const props = defineProps<{
  hours: RecapHour[]
  peakHour: number | null
  weekdays: RecapHour[]
  peakWeekday: number | null
}>()

const theme = useThemeStore()
const wdEl = ref<HTMLElement | null>(null)
const hrEl = ref<HTMLElement | null>(null)

const WEEKDAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

const hasWeekdayData = () => props.weekdays.some((w) => w.count > 0)
const hasHourData = () => props.hours.some((h) => h.count > 0)

const peakWeekdayCount = () =>
  props.peakWeekday != null ? props.weekdays[props.peakWeekday]?.count ?? 0 : 0
const peakHourCount = () =>
  props.peakHour != null ? props.hours[props.peakHour]?.count ?? 0 : 0

/** 钟面底色：强调色极淡的一层圆盘，随主题/强调色重建。 */
const discColor = computed(() => {
  void theme.mode
  void theme.accent
  return hexToRgba(cssVar('--sa-accent', '#0485f7'), 0.08)
})

function barOption(
  labels: string[],
  values: number[],
  peakIdx: number | null,
  peakLabel: string | null,
): EChartsOption {
  const accent = cssVar('--sa-accent', '#0485f7')
  const text3 = cssVar('--sa-text-tertiary', '#7d7d8e')
  return {
    grid: { left: 4, right: 4, top: 24, bottom: 2, containLabel: true },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: '#20242e',
      borderColor: 'rgba(255,255,255,0.1)',
      textStyle: { color: '#f2f4f8', fontSize: 12 },
      formatter: (ps) => {
        const p = (Array.isArray(ps) ? ps[0] : ps) as {
          name: string
          value: number
        }
        return `${p.name} · ${p.value} 次`
      },
    },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: text3, fontSize: 11, interval: 0 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      max: (v: { max: number }) => Math.max(4, Math.ceil(v.max * 1.15)),
      axisLabel: { show: false },
      splitLine: { show: false },
    },
    series: [
      {
        type: 'bar',
        barCategoryGap: labels.length > 10 ? '35%' : '45%',
        data: values.map((v, i) => ({
          value: v,
          itemStyle: {
            borderRadius: [4, 4, 1, 1],
            color:
              i === peakIdx
                ? accent
                : hexToRgba(accent, 0.32),
          },
        })),
        // 峰值大字标注(柱顶)
        markPoint:
          peakIdx != null && values[peakIdx] > 0
            ? {
                symbol: 'rect',
                symbolSize: [1, 1],
                itemStyle: { color: 'transparent' },
                label: {
                  show: true,
                  position: 'top',
                  color: accent,
                  fontSize: 12,
                  fontWeight: 700,
                  formatter: peakLabel ?? '',
                  offset: [0, -2],
                },
                data: [{ name: 'peak', coord: [peakIdx, values[peakIdx]] }],
              }
            : undefined,
        animationDelay: (i: number) => i * 25,
      },
    ],
  }
}

useEChart(
  wdEl,
  () =>
    barOption(
      WEEKDAY_LABELS,
      props.weekdays.map((w) => w.count),
      props.peakWeekday,
      props.peakWeekday != null ? `${WEEKDAY_LABELS[props.peakWeekday]}` : null,
    ),
  [() => theme.mode, () => theme.accent, () => props.weekdays, () => props.peakWeekday],
)

/** 观看时钟：极坐标 24 花瓣钟面。半径轴 min 取负值让 0 值位置内移 → 中心留孔。 */
useEChart(
  hrEl,
  () => {
    const accent = cssVar('--sa-accent', '#0485f7')
    const text3 = cssVar('--sa-text-tertiary', '#7d7d8e')
    const values = props.hours.map((h) => h.count)
    const maxV = Math.max(...values, 1)
    return {
      polar: { center: ['50%', '50%'], radius: '72%' },
      angleAxis: {
        type: 'category',
        data: props.hours.map((h) => String(h.hour).padStart(2, '0')),
        startAngle: 90,
        clockwise: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: text3, fontSize: 11, interval: 5 },
      },
      radiusAxis: {
        // min 取负值让 0 值位置内移 → 中心留孔;峰值花瓣伸到接近盘缘
        min: -maxV * 0.65,
        max: maxV * 1.02,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
      },
      tooltip: {
        trigger: 'item',
        backgroundColor: '#20242e',
        borderColor: 'rgba(255,255,255,0.1)',
        textStyle: { color: '#f2f4f8', fontSize: 12 },
        formatter: (p) => {
          const q = p as { name: string; value: number }
          return `${q.name}:00 · ${q.value} 次`
        },
      },
      series: [
        {
          type: 'bar',
          coordinateSystem: 'polar',
          barWidth: '58%',
          data: values.map((v, i) => ({
            value: v,
            itemStyle: {
              color:
                i === props.peakHour ? accent : hexToRgba(accent, 0.38),
            },
          })),
          emphasis: { itemStyle: { color: accent } },
          animationDelay: (i: number) => i * 25,
        },
      ],
    } as EChartsOption
  },
  [() => theme.mode, () => theme.accent, () => props.hours, () => props.peakHour],
)
</script>

<template>
  <div class="clock">
    <div v-if="hasWeekdayData() || hasHourData()" class="clock-row">
      <div v-if="hasWeekdayData()" class="clock-cell cell-bars">
        <div class="cell-title">观看星期</div>
        <div ref="wdEl" class="cell-chart" />
        <div class="cell-stat">
          <div class="stat-label">最活跃星期</div>
          <div class="stat-value">{{ props.peakWeekday != null ? WEEKDAY_LABELS[props.peakWeekday] : '—' }}</div>
          <div class="stat-sub">{{ peakWeekdayCount() }} 次观看</div>
        </div>
      </div>
      <div v-if="hasHourData()" class="clock-cell cell-clock">
        <div class="cell-title">观看时段</div>
        <div class="clock-face">
          <div class="clock-disc" :style="{ background: discColor }" />
          <div ref="hrEl" class="face-canvas" />
        </div>
        <div class="cell-stat">
          <div class="stat-label">最活跃时段</div>
          <div class="stat-value">{{ props.peakHour != null ? String(props.peakHour).padStart(2, '0') + ':00' : '—' }}</div>
          <div class="stat-sub">{{ peakHourCount() }} 次观看</div>
        </div>
      </div>
    </div>
    <p v-if="!hasWeekdayData() && !hasHourData()" class="clock-empty">还没有观看记录</p>
  </div>
</template>

<style scoped>
.clock {
  min-width: 0;
}
/* 单行紧凑排布:星期柱状图撑满左侧,时钟+统计紧贴右侧 */
.clock-row {
  display: flex;
  align-items: center;
  gap: 40px;
}
.clock-cell {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 20px;
}
.cell-bars {
  flex: 1;
}
.cell-clock {
  flex: none;
}
.cell-title {
  display: none;
}
.cell-chart {
  flex: 1;
  min-width: 0;
  height: 250px; /* 与环形图画布同高 */
}
/* 放射状时钟:固定正方形画布 + 淡色圆盘底 */
.clock-face {
  position: relative;
  flex: none;
  width: 250px; /* 与环形图画布同高,钟面视觉尺寸对齐其他章节 */
  height: 250px;
}
.clock-disc {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 78%;
  height: 78%;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  pointer-events: none;
}
.face-canvas {
  position: absolute;
  inset: 0;
}
.cell-stat {
  flex: none;
  width: 148px;
}
.stat-label {
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.stat-value {
  font-size: 26px;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--sa-text-primary);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.stat-sub {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  margin-top: 2px;
}
.clock-empty {
  padding: 40px 0;
  text-align: center;
  color: var(--sa-text-secondary);
  font-size: 13px;
}
@media (max-width: 900px) {
  .clock-row {
    flex-direction: column;
    align-items: stretch;
    gap: 20px;
  }
  .cell-bars {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }
  .cell-clock {
    flex-direction: column;
    align-items: center;
    gap: 8px;
  }
  .cell-chart {
    flex: none; /* 纵向排列时 flex:1 会让图塌缩成 0 高 */
    height: 150px;
  }
  .clock-face {
    width: 220px;
    height: 220px;
  }
  .cell-stat {
    width: auto;
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .stat-value {
    font-size: 20px;
  }
}
</style>
