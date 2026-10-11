/**
 * ECharts 按需引入统一入口。
 *
 * 不引全量包：只注册统计页用到的图表（饼/柱）与组件（网格/提示/图例/标题/标记点），
 * 体积比全量省一大半。新增图表类型时在这里补 `echarts.use`。
 */
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart, ThemeRiverChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkPointComponent,
  PolarComponent,
  SingleAxisComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ComposeOption } from 'echarts/core'
import type {
  BarSeriesOption,
  LineSeriesOption,
  PieSeriesOption,
  ThemeRiverSeriesOption,
} from 'echarts/charts'
import type {
  GridComponentOption,
  LegendComponentOption,
  PolarComponentOption,
  SingleAxisComponentOption,
  TitleComponentOption,
  TooltipComponentOption,
} from 'echarts/components'

echarts.use([
  PieChart,
  BarChart,
  LineChart,
  ThemeRiverChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  MarkPointComponent,
  PolarComponent,
  SingleAxisComponent,
  CanvasRenderer,
])

export { echarts }

export type EChartsOption = ComposeOption<
  | PieSeriesOption
  | BarSeriesOption
  | LineSeriesOption
  | ThemeRiverSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | LegendComponentOption
  | TitleComponentOption
  | PolarComponentOption
  | SingleAxisComponentOption
>

/** 读一个 CSS 变量当前值（随 data-theme / data-accent 切换，重建图表前取一次）。 */
export function cssVar(name: string, fallback = ''): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

/** #rrggbb → rgba(...,alpha)：给强调色做透明度渐变用（强调色可能整体换 rose/blue）。 */
export function hexToRgba(hex: string, alpha: number): string {
  const s = hex.trim().replace('#', '')
  const full = s.length === 3 ? s.split('').map((c) => c + c).join('') : s
  const n = Number.parseInt(full.slice(0, 6), 16)
  if (Number.isNaN(n)) return `rgba(4, 133, 247, ${alpha})`
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}
