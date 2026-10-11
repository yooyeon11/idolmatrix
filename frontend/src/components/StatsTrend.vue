<script setup lang="ts">
/**
 * 艺人轨迹（实验功能）：河流图（Streamgraph / ThemeRiver）——口味重心迁移。
 *
 * 每条流带 = 一个主体（艺人/组合），宽度 = 当月入库条数；河的上下轮廓 =
 * 当月总量；波浪基线天然呈现"总量与份额的双重变迁"。悬停看当月全部主体
 * 与占比。口径切换（按入库时间 / 按表演时间）由父组件持有；数据来自
 * /stats/artist-trend，主体 = 艺人 ∪ 组合 Top6（合作视频计入各相关主体，短视频不计）。
 */
import { computed, ref, watch } from 'vue'
import { useEChart } from '@/composables/useEChart'
import { cssVar, type EChartsOption } from '@/utils/echarts'
import { useThemeStore } from '@/stores/theme'
import { dashboardApi } from '@/api/dashboard'

const props = defineProps<{ by: 'created' | 'performance' }>()
const emit = defineEmits<{ 'update:by': [v: 'created' | 'performance'] }>()

const theme = useThemeStore()
const el = ref<HTMLElement | null>(null)

const data = ref<Awaited<ReturnType<typeof dashboardApi.artistTrend>> | null>(null)
const loading = ref(false)
const loadError = ref('')

const PALETTE = ['#0485f7', '#14b8d4', '#7c66ff', '#f5a623', '#34c77b', '#f07878']
const MAX_STREAMS = 6

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await dashboardApi.artistTrend({ by: props.by, months: 12, limit: 8 })
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
watch(() => props.by, () => void load(), { immediate: true })

interface Sub {
  name: string
  data: number[]
  total: number
  roll: number[]
}

const months = computed(() => data.value?.months ?? [])

const subs = computed<Sub[]>(() => {
  const d = data.value
  if (!d) return []
  return (d.series || [])
    .filter((s) => s.kind !== 'other')
    .slice(0, MAX_STREAMS)
    .map((s) => {
      const arr = s.data
      // 近 3 个月滚动合计：只用于下方"本命/连霸"总结，不进河流
      const roll = arr.map((_, i) =>
        arr.slice(Math.max(0, i - 2), i + 1).reduce((a, b) => a + b, 0),
      )
      return { name: s.name, data: arr, total: arr.reduce((a, b) => a + b, 0), roll }
    })
})

const hasData = computed(() => subs.value.some((s) => s.data.some((v) => v > 0)))

const monthTotals = computed(() =>
  months.value.map((_, i) => subs.value.reduce((a, s) => a + (s.data[i] || 0), 0)),
)

/** ThemeRiver 数据：[月份, 条数, 名称]（0 值保留，流带收窄再展开 = 空窗叙事） */
const streamData = computed(() =>
  subs.value.flatMap((s) =>
    s.data.map((v, i) => [`${months.value[i]}-01`, v, s.name] as [string, number, string]),
  ),
)

// ===== 一句话总结:当前本命 + 连霸月数 + 爬升最快 =====
const insight = computed(() => {
  const list = subs.value
  if (!list.length) return null
  const n = months.value.length
  let last = -1
  for (let i = n - 1; i >= 0; i--) {
    if (monthTotals.value[i] > 0) {
      last = i
      break
    }
  }
  if (last < 0) return null
  // 当月条数最多者为本命；连续月数 = 向前逐月保持"当月最多"的长度
  const topAt = (i: number) => {
    let best: Sub | null = null
    for (const s of list) {
      if (s.data[i] > 0 && (!best || s.data[i] > best.data[i])) best = s
    }
    return best
  }
  const champ = topAt(last)
  let streak = 0
  if (champ) {
    for (let i = last; i >= 0; i--) {
      const t = topAt(i)
      if (t && t.name === champ.name) streak += 1
      else if (monthTotals.value[i] > 0) break
    }
  }
  // 近 3 个月条数合计爬升最快者（相对更早 3 个月）
  let riser: { name: string; from: number; to: number } | null = null
  for (const s of list) {
    const recent = s.data.slice(Math.max(0, last - 2), last + 1).reduce((a, b) => a + b, 0)
    const earlier = s.data.slice(0, Math.max(0, last - 2)).reduce((a, b) => a + b, 0)
    const climb = recent - earlier
    if (climb > 0 && (!riser || climb > riser.to - riser.from)) {
      riser = { name: s.name, from: earlier, to: recent }
    }
  }
  return { champ: champ?.name ?? null, streak, riser }
})

// ===== 河流图 =====
function buildOption(): EChartsOption {
  const text3 = cssVar('--sa-text-tertiary', '#7d7d8e')
  // 窄屏 12 个月标签挤成一团 → 隔月显示
  const narrow = (el.value?.clientWidth ?? 1280) < 520
  // 河流两侧留白与其他章节图表对齐(其他图表 grid 只有几像素内缩)
  const inset = narrow ? 2 : 14
  return {
    color: PALETTE,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: 'rgba(128,128,128,0.35)' } },
      backgroundColor: '#20242e',
      borderColor: 'rgba(255,255,255,0.1)',
      textStyle: { color: '#f2f4f8', fontSize: 12 },
      formatter: (ps) => {
        const arr = (Array.isArray(ps) ? ps : [ps]) as unknown as {
          data: [string, number, string]
        }[]
        const rows = arr
          .map((p) => ({ name: p.data[2], value: p.data[1], t: p.data[0] }))
          .filter((r) => r.value > 0)
          .sort((a, b) => b.value - a.value)
        if (!rows.length) return ''
        const total = rows.reduce((a, r) => a + r.value, 0)
        const title = rows[0].t.slice(0, 7)
        let html = `<b>${title}</b> · 共 ${total} 条`
        for (const r of rows) {
          const pct = Math.round((r.value / total) * 100)
          html += `<br/>${r.name}：${r.value} 条（${pct}%）`
        }
        return html
      },
    },
    singleAxis: {
      top: 10,
      bottom: 26,
      left: inset,
      right: inset,
      type: 'time',
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: {
        color: text3,
        fontSize: 11,
        hideOverlap: true,
        formatter: (val: number) => {
          const d = new Date(val)
          if (narrow && d.getMonth() % 2 === 1) return ''
          return `${String(d.getFullYear()).slice(2)}-${String(d.getMonth() + 1).padStart(2, '0')}`
        },
      },
      splitLine: { show: false },
    },
    series: [
      {
        type: 'themeRiver',
        data: streamData.value,
        label: { show: false },
        emphasis: {
          focus: 'series',
          itemStyle: { shadowBlur: 12, shadowColor: 'rgba(0,0,0,0.25)' },
        },
      },
    ],
  }
}
useEChart(el, buildOption, [() => data.value, () => theme.mode, () => theme.accent])

const coverageText = computed(() => {
  const c = data.value?.coverage
  if (!c || !c.total) return ''
  const pct = Math.round((c.with_date / c.total) * 100)
  return `已计入 ${pct}%（${c.with_date}/${c.total} 条有表演日期，其余未计入）`
})
</script>

<template>
  <div class="trend">
    <div class="trend-toolbar">
      <span class="trend-kind">
        <button
          type="button"
          :class="{ on: props.by === 'created' }"
          @click="emit('update:by', 'created')"
        >
          按入库时间
        </button>
        <button
          type="button"
          :class="{ on: props.by === 'performance' }"
          @click="emit('update:by', 'performance')"
        >
          按表演时间
        </button>
      </span>
      <span v-if="props.by === 'performance' && coverageText" class="trend-coverage">{{ coverageText }}</span>
    </div>

    <div v-if="loadError" class="trend-error">{{ loadError }}</div>
    <div v-else-if="!data && loading" class="trend-loading">加载中…</div>

    <template v-else-if="hasData">
      <p v-if="insight" class="trend-insight">
        <template v-if="insight.champ">
          <template v-if="insight.streak > 1">
            <b>{{ insight.champ }}</b> 已连续 {{ insight.streak }} 个月是你收得最多的人
          </template>
          <template v-else>
            最近一个月你收得最多的是 <b>{{ insight.champ }}</b>
          </template>
        </template>
        <template v-if="insight.champ && insight.riser">；</template>
        <template v-if="insight.riser">
          <b>{{ insight.riser.name }}</b> 的分量涨得最快（近 3 月 {{ insight.riser.from }} → {{ insight.riser.to }} 条）
        </template>
      </p>
      <div class="stream-legend">
        <span v-for="(s, i) in subs" :key="s.name" class="legend-item">
          <i :style="{ background: PALETTE[i % PALETTE.length] }" />{{ s.name }}
        </span>
      </div>
      <div ref="el" class="trend-chart" :style="{ height: '400px' }" />
    </template>
    <p v-else class="trend-empty">这 12 个月还没有可展示的记录</p>

    <p v-if="hasData" class="trend-note">
      每条流带的宽度 = 该主体当月入库条数，河的上下轮廓 = 当月总量，颜色见上方图例；
      流带收窄成线 = 那几个月没怎么收该主体；主体 = 艺人 ∪ 组合 Top{{ subs.length }}，
      合作视频计入各相关主体，短视频不计；{{ props.by === 'created' ? '按收藏动作发生的时间归月' : '按表演发生的月份归月' }}。
    </p>
  </div>
</template>

<style scoped>
.trend {
  min-width: 0;
}
.trend-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.trend-kind {
  display: inline-flex;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  overflow: hidden;
}
.trend-kind button {
  padding: 4px 14px;
  border: none;
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}
.trend-kind button + button {
  border-left: 1px solid var(--sa-border-subtle);
}
.trend-kind button.on {
  background: var(--sa-accent);
  color: #fff;
}
.trend-coverage {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.trend-error {
  padding: 16px 0;
  color: #f05252;
  font-size: 13px;
}
.trend-loading {
  padding: 40px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}
.trend-empty {
  padding: 40px 0;
  text-align: center;
  color: var(--sa-text-secondary);
  font-size: 13px;
}
.trend-insight {
  margin: 2px 0 10px;
  font-size: 13px;
  color: var(--sa-text-secondary);
}
.trend-insight b {
  color: var(--sa-text-primary);
  font-weight: 750;
}
.stream-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin: 0 0 4px;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--sa-text-secondary);
}
.legend-item i {
  width: 9px;
  height: 9px;
  border-radius: 3px;
  display: inline-block;
}
.trend-chart {
  width: 100%;
}
.trend-note {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--sa-border-subtle);
  font-size: 12px;
  color: var(--sa-text-tertiary);
  line-height: 1.8;
}
@media (max-width: 900px) {
  .trend-chart {
    height: 320px;
  }
}
</style>
