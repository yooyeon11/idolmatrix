<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { StageMatrix, StageMatrixDay, StageMatrixItem, StageMatrixMember } from '@/types/models'
import { CAMERA_ANGLE_LABEL } from '@/types/models'
import { formatDuration } from '@/utils/format'

/**
 * 场次与机位面板（按设计稿重做）
 *
 * 结构：横向场次胶囊轴 → 组合直拍 → 成员直拍。
 * 每个机位一行：序号 / 头像 / 名称 + 机位说明 / 画幅徽标；当前播放行高亮并带声波动画。
 */
const props = defineProps<{ matrix: StageMatrix; wave?: boolean }>()
const emit = defineEmits<{
  (e: 'select', uid: string): void
  /** 胶囊 hover：预取目标机位封面，点下去时切图零空窗 */
  (e: 'prefetch', videoId: number): void
}>()

// ===== 场次维度 =====
/** 选中场次 = 日期 + 活动名；同一天可能有多个活动，只存日期会选不中第二场 */
const selectedSlot = ref<string | undefined>(undefined)
watch(
  () => props.matrix,
  () => {
    selectedSlot.value = undefined
    pickOpen.value = false
  },
)

const days = computed(() => props.matrix.days || [])

// ===== 场次辅助 =====

// ===== 机位分类 =====
const CAMERA_LABEL: Record<string, string> = {
  mv: '官方 MV',
  stage: '官方舞台',
  portrait_fancam: '竖屏直拍',
  landscape_fancam: '横屏直拍',
  facecam: '面部直拍',
  onetake: '一镜到底',
}
function cameraLabel(v: StageMatrixItem): string | null {
  // 真实机位（入库时按词表录入）优先
  const real = CAMERA_ANGLE_LABEL[v.camera]
  if (real) return real
  // 无机位的老条目：按类型给行内说明，避免「特别舞台」行写成「官方舞台」
  const types = (v.video_types || []).map((t) => String(t).toLowerCase())
  const typeLabel = officialTypeLabel(types)
  if (typeLabel) return typeLabel
  return CAMERA_LABEL[v.camera] ?? null
}
function orientLabel(v: StageMatrixItem): string {
  return v.orientation === 'portrait' ? '9:16 竖屏' : v.orientation === 'landscape' ? '16:9 横屏' : ''
}

// ===== 分栏：打歌舞台 / 官方影像 / 粉丝直拍 =====
// 「官方影像」= 官方 MV + 特别舞台 + 合作舞台（业主 2026-10-26 口径：两者合并，避免 tag 过多）。
// 行内副标题仍分写「官方 MV / 特别舞台 / 合作舞台」，需要细分时不用再拆 tab。
type StageKind = 'stage' | 'official' | 'fancam'

const KIND_ORDER: StageKind[] = ['stage', 'official', 'fancam']
const KIND_LABEL: Record<StageKind, string> = {
  stage: '打歌舞台',
  official: '官方影像',
  fancam: '粉丝直拍',
}

/** 分栏只按主类型 video_type（video_types[0]）判定，机位不参与分栏 */
const KIND_BY_TYPE: Record<string, StageKind> = {
  performancevideo: 'stage',
  fancam: 'fancam',
  officialmv: 'official',
  specialstage: 'official',
  collabstage: 'official',
}

/** 行内类型说明：官方影像内部仍区分 MV / 特别舞台 / 合作舞台 */
function officialTypeLabel(types: string[]): string | null {
  if (types.includes('officialmv')) return '官方 MV'
  if (types.includes('specialstage')) return '特别舞台'
  if (types.includes('collabstage')) return '合作舞台'
  return null
}

function kindOf(v: StageMatrixItem): StageKind {
  const primary = String(
    v.video_type || (v.video_types || [])[0] || '',
  ).toLowerCase()
  return KIND_BY_TYPE[primary] ?? 'stage'
}

/** 当前视频所属分栏；用于打开面板时直接落在对应 tab（如特别舞台条不会先跳到打歌舞台） */
function initialKind(): StageKind {
  const videos = (props.matrix?.days || []).flatMap((d) => d.videos || [])
  const current = videos.find((v) => v.current)
  if (current) return kindOf(current)
  return KIND_ORDER.find((k) => videos.some((v) => kindOf(v) === k)) ?? 'stage'
}

const stageTab = ref<StageKind>(initialKind())

/** 只展示有内容的分栏（空 tab 不占位） */
const visibleKinds = computed<StageKind[]>(() => KIND_ORDER.filter((k) => countOf(k) > 0))

/** 当前分栏下的场次（只保留该栏有机位的日期） */
const tabDays = computed(() =>
  days.value
    .map((d) => ({ ...d, videos: d.videos.filter((v) => kindOf(v) === stageTab.value) }))
    .filter((d) => d.videos.length > 0),
)
const activeTabDay = computed<StageMatrixDay | null>(() => {
  if (selectedSlot.value !== undefined) {
    return tabDays.value.find((d) => d.key === selectedSlot.value) ?? null
  }
  return (
    tabDays.value.find((d) => d.videos.some((v) => v.current)) ??
    tabDays.value[0] ??
    null
  )
})

watch(stageTab, () => {
  selectedSlot.value = undefined
  pickOpen.value = false
})
watch(
  () => props.matrix,
  () => {
    selectedSlot.value = undefined
    stageTab.value = initialKind()
  },
)
// 当前分栏若因数据变化变空（例如刚切了视频），退回第一个有内容的分栏
watch(visibleKinds, (list) => {
  if (list.length && !list.includes(stageTab.value)) stageTab.value = list[0]
})

const dayVideos = computed(() => activeTabDay.value?.videos ?? [])
const groupItems = computed(() => dayVideos.value.filter((v) => v.member.kind === 'group'))
const memberItems = computed(() => dayVideos.value.filter((v) => v.member.kind !== 'group'))
const groupTitle = computed(() => (isOfficialTab.value ? '组合作品' : '组合直拍'))
const memberTitle = computed(() => (isOfficialTab.value ? '成员作品' : '成员直拍'))
const itemUnit = computed(() => (isOfficialTab.value ? '条' : '机位'))

/** 官方影像的「活动名」= 标题最后一个 [..] 里的内容（如 [Official MV] → Official MV） */
function bracketLabels(videos: StageMatrixDay['videos']): string[] {
  const out: string[] = []
  for (const v of videos) {
    const matches = [...String(v.name || '').matchAll(/\[([^\]]+)\]/g)]
    const text = matches.length ? matches[matches.length - 1][1].trim() : ''
    if (text && !out.includes(text)) out.push(text)
  }
  return out
}

const isOfficialTab = computed(() => stageTab.value === 'official')

/**
 * 场次条目：日期拆成「月/日」大数字 + 年份小字，活动名单独一行。
 * 旧版把「日期 · 活动名」压成一整行塞进 999px 全圆胶囊，462px 宽 34px 高像根扁担，
 * 右侧还留出大片空白 —— 现在改成卡片式两行，左日期块负责视觉重量。
 */
interface StageRow {
  key: string
  md: string
  year: string
  title: string
  meta: string
}

const WEEK_CN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function dateParts(d: StageMatrixDay | null): { md: string; year: string } {
  const raw = d?.date || ''
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!m) return { md: '--', year: '' }
  return { md: `${m[2]}/${m[3]}`, year: m[1] }
}

function weekdayOf(date?: string | null): string {
  if (!date) return ''
  const d = new Date(`${date.slice(0, 10)}T00:00:00`)
  return Number.isNaN(d.getTime()) ? '' : WEEK_CN[d.getDay()]
}

/** 该场次的标题：官方影像分栏用标题里的 [..]，其余用活动名（空就写「未标注」） */
function titleOf(d: StageMatrixDay): string {
  if (isOfficialTab.value) {
    const labels = bracketLabels(d.videos)
    return labels.length ? labels.join(' · ') : '未标注影像'
  }
  return (d.event_name || '').trim() || '未标注活动'
}

function metaOf(d: StageMatrixDay): string {
  const parts: string[] = []
  const wd = weekdayOf(d.date)
  if (wd) parts.push(wd)
  parts.push(`${d.videos.length} ${itemUnit.value}`)
  return parts.join(' · ')
}

const pickRows = computed<StageRow[]>(() =>
  tabDays.value.map((d) => {
    const { md, year } = dateParts(d)
    const title = titleOf(d)
    return {
      key: d.key,
      md,
      year,
      title,
      meta: metaOf(d),
    }
  }),
)

const heroRow = computed<StageRow | null>(() => {
  const row = pickRows.value.find((r) => r.key === activeTabDay.value?.key)
  return row ?? pickRows.value[0] ?? null
})

const pickOpen = ref(false)
const pickRoot = ref<HTMLElement | null>(null)

/**
 * ≤768px 走底部抽屉。
 * ⚠ 这里**不能**退回原生 <select>：vivo / OPPO / 小米等内核不认 `appearance: none`，
 *   会渲染成系统默认的白底控件（业主 2026-11-13 反馈「特别丑的白色选择框」）。
 */
const isNarrow = ref(false)
let narrowMq: MediaQueryList | null = null
function syncNarrow(e: { matches: boolean }) {
  isNarrow.value = e.matches
}

function pickDayKey(key: string) {
  const target = tabDays.value.find((d) => d.key === key)
  if (!target) return
  selectedSlot.value = target.key
  pickOpen.value = false
}
function onDocClick(e: MouseEvent) {
  if (!pickOpen.value) return
  if (!pickRoot.value?.contains(e.target as Node)) pickOpen.value = false
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') pickOpen.value = false
}

// 抽屉打开时锁住背景滚动，否则手指划过遮罩会把页面一起带着滚
watch([pickOpen, isNarrow], ([open, narrow]) => {
  document.body.style.overflow = open && narrow ? 'hidden' : ''
})

onMounted(() => {
  narrowMq = window.matchMedia('(max-width: 768px)')
  isNarrow.value = narrowMq.matches
  narrowMq.addEventListener('change', syncNarrow)
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  narrowMq?.removeEventListener('change', syncNarrow)
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})

/** 分栏计数（给 tab 上的小数字） */
function countOf(kind: StageKind): number {
  return days.value.reduce(
    (n, d) => n + d.videos.filter((v) => kindOf(v) === kind).length,
    0,
  )
}

function subline(v: StageMatrixItem): string {
  const parts = [cameraLabel(v) || '机位']
  if (v.duration) parts.push(formatDuration(v.duration))
  return parts.join(' · ')
}

// ===== 头像 =====
const brokenAvatars = ref<Set<string>>(new Set())
function onAvatarError(key: string) {
  brokenAvatars.value = new Set(brokenAvatars.value).add(key)
}
function avatarUrl(m: StageMatrixMember) {
  if (!m.id) return ''
  // w=128：机位卡片头像走缩放变体；带参数同时避开旧的 immutable 缓存
  return m.kind === 'group'
    ? `/api/groups/${m.id}/avatar?w=128`
    : `/api/artists/${m.id}/avatar?w=128`
}
function avatarKey(m: StageMatrixMember) {
  return `${m.kind}-${m.id ?? m.name}`
}
function showAvatarImg(m: StageMatrixMember) {
  return !!m.avatar_path && !!avatarUrl(m) && !brokenAvatars.value.has(avatarKey(m))
}
function initialOf(name: string) {
  return (name || '?').trim().charAt(0).toUpperCase()
}
function isUnconfirmed(m: StageMatrixMember) {
  return m.confidence === 'low'
}

function onSelect(v: StageMatrixItem) {
  if (!v.current) emit('select', v.uid)
}
</script>

<template>
  <div class="stage-sheet">
    <!-- 打歌场次切换：横向胶囊轴 -->
    <section class="sheet-block">
      <div class="block-head block-head--tabs">
        <!-- 分栏：打歌舞台 / 官方影像 / 粉丝直拍（空分栏不渲染） -->
        <div class="kind-tabs">
          <button
            v-for="k in visibleKinds"
            :key="k"
            type="button"
            class="kind-tab"
            :class="{ 'is-active': stageTab === k }"
            @click="stageTab = k"
          >
            {{ KIND_LABEL[k] }}
            <span class="kind-count">{{ countOf(k) }}</span>
          </button>
        </div>
        <span class="block-count">{{ isOfficialTab ? countOf(stageTab) : tabDays.length }} {{ isOfficialTab ? '条' : '场' }}</span>
      </div>

      <div ref="pickRoot" class="session-pick">
        <!-- 桌面：卡片式触发器（左日期块 + 两行信息 + 箭头） -->
        <button
          type="button"
          class="sp-trigger"
          :class="{ 'is-open': pickOpen && !isNarrow, 'is-sheet': pickOpen && isNarrow }"
          :aria-label="isOfficialTab ? '选择日期' : '选择打歌场次'"
          :aria-expanded="pickOpen"
          aria-haspopup="listbox"
          @click="pickOpen = !pickOpen"
        >
          <span class="sp-date">
            <b>{{ heroRow?.md ?? '--' }}</b>
            <i v-if="heroRow?.year">{{ heroRow.year }}</i>
          </span>
          <span class="sp-main">
            <span class="sp-title">{{ heroRow?.title ?? '无场次' }}</span>
            <span class="sp-meta">{{ heroRow?.meta ?? '' }}</span>
          </span>
          <svg class="sp-chevron" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        <Transition name="sp-pop">
          <div v-if="pickOpen && !isNarrow" class="sp-panel" role="listbox" @click.stop>
            <button
              v-for="r in pickRows"
              :key="r.key"
              type="button"
              class="sp-opt"
              :class="{ 'is-active': activeTabDay?.key === r.key }"
              role="option"
              :aria-selected="activeTabDay?.key === r.key"
              @click="pickDayKey(r.key)"
            >
              <span class="sp-date">
                <b>{{ r.md }}</b>
                <i v-if="r.year">{{ r.year }}</i>
              </span>
              <span class="sp-main">
                <span class="sp-title">{{ r.title }}</span>
                <span class="sp-meta">{{ r.meta }}</span>
              </span>
              <svg
                v-if="activeTabDay?.key === r.key"
                class="sp-check"
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                aria-hidden="true"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.6" d="M20 6L9 17l-5-5" />
              </svg>
            </button>
          </div>
        </Transition>
      </div>

      <!-- 移动端：底部抽屉（不用原生 select —— 国产内核会把它画成系统白框） -->
      <Teleport to="body">
        <Transition name="sp-sheet">
          <div v-if="pickOpen && isNarrow" class="sp-scrim" @click="pickOpen = false">
            <div
              class="sp-sheet"
              role="dialog"
              aria-modal="true"
              :aria-label="isOfficialTab ? '选择日期' : '选择打歌场次'"
              @click.stop
            >
              <div class="sp-sheet-head">
                <span class="sp-sheet-title">{{ isOfficialTab ? '选择日期' : '选择场次' }}</span>
                <span class="sp-sheet-count">{{ pickRows.length }} {{ isOfficialTab ? '条' : '场' }}</span>
                <button type="button" class="sp-sheet-close" aria-label="关闭" @click="pickOpen = false">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" d="M6 6l12 12M18 6L6 18" />
                  </svg>
                </button>
              </div>
              <div class="sp-sheet-list">
                <button
                  v-for="r in pickRows"
                  :key="`s-${r.key}`"
                  type="button"
                  class="sp-opt"
                  :class="{ 'is-active': activeTabDay?.key === r.key }"
                  @click="pickDayKey(r.key)"
                >
                  <span class="sp-date">
                    <b>{{ r.md }}</b>
                    <i v-if="r.year">{{ r.year }}</i>
                  </span>
                  <span class="sp-main">
                    <span class="sp-title">{{ r.title }}</span>
                    <span class="sp-meta">{{ r.meta }}</span>
                  </span>
                  <svg
                    v-if="activeTabDay?.key === r.key"
                    class="sp-check"
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    aria-hidden="true"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.6" d="M20 6L9 17l-5-5" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </Transition>
      </Teleport>
    </section>

    <!-- 组合直拍 -->
    <section v-if="groupItems.length" class="sheet-block">
      <div class="block-head">
        <span class="block-title">{{ groupTitle }}</span>
        <span class="block-count">{{ groupItems.length }} {{ itemUnit }}</span>
      </div>
      <div class="cam-list">
        <div
          v-for="(v, i) in groupItems"
          :key="v.id"
          class="cam-item"
          :class="{ 'is-active': v.current }"
          @mouseenter="v.current || emit('prefetch', v.id)"
          @click="onSelect(v)"
        >
          <span class="cam-no">{{ String(i + 1).padStart(2, '0') }}</span>
          <span class="cam-avatar is-group">
            <img
              v-if="showAvatarImg(v.member)"
              :src="avatarUrl(v.member)"
              alt=""
              loading="lazy"
              @error="onAvatarError(avatarKey(v.member))"
            />
            <template v-else>ALL</template>
          </span>
          <span class="cam-main">
            <span class="cam-name">
              {{ v.member.name }}
              <span v-if="v.current" class="sound-wave"><i /><i /><i /></span>
            </span>
            <span class="cam-sub">{{ subline(v) }}</span>
          </span>
          <span v-if="orientLabel(v)" class="cam-badge">{{ orientLabel(v) }}</span>
        </div>
      </div>
    </section>

    <!-- 成员直拍 -->
    <section v-if="memberItems.length" class="sheet-block">
      <div class="block-head">
        <span class="block-title">{{ memberTitle }}</span>
        <span class="block-count">{{ memberItems.length }} {{ itemUnit }}{{ isOfficialTab ? '' : '已索引' }}</span>
      </div>
      <div class="cam-list">
        <div
          v-for="(v, i) in memberItems"
          :key="v.id"
          class="cam-item"
          :class="{ 'is-active': v.current }"
          @mouseenter="v.current || emit('prefetch', v.id)"
          @click="onSelect(v)"
        >
          <span class="cam-no">{{ String(i + 1).padStart(2, '0') }}</span>
          <span class="cam-avatar">
            <img
              v-if="showAvatarImg(v.member)"
              :src="avatarUrl(v.member)"
              alt=""
              loading="lazy"
              @error="onAvatarError(avatarKey(v.member))"
            />
            <template v-else>{{ initialOf(v.member.name) }}</template>
          </span>
          <span class="cam-main">
            <span class="cam-name">
              {{ v.member.name }}
              <span v-if="isUnconfirmed(v.member)" class="cam-flag">待确认</span>
              <span v-if="v.current" class="sound-wave"><i /><i /><i /></span>
            </span>
            <span class="cam-sub">{{ subline(v) }}</span>
          </span>
          <span v-if="orientLabel(v)" class="cam-badge">{{ orientLabel(v) }}</span>
        </div>
      </div>
    </section>

    <div v-if="!dayVideos.length" class="cam-empty">
      {{ countOf(stageTab) ? (isOfficialTab ? '该日期暂无其他官方影像' : '该场次暂无其他机位') : `这条视频暂无${KIND_LABEL[stageTab]}` }}
    </div>
  </div>
</template>

<style scoped>
.stage-sheet {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding-top: 0;
}

.sheet-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}
/* 分栏行：底部一条极淡的分割线，把「筛选」和「内容」在卡内分开。
   active 的 2px 下划线用 margin-bottom: -1px 压在分割线上，而不是浮在上方 12px 处。 */
.block-head--tabs {
  padding-bottom: 0;
  align-items: flex-end;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.block-head--tabs .block-count {
  padding-bottom: 9px;
}
/* 组合直拍 / 成员直拍：卡片内的小节标，弱化成辅助字 */
.block-title {
  font-size: 11.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--sa-text-tertiary);
}
.block-count {
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--sa-text-tertiary);
}

/* ===== 分栏标签 ===== */
.kind-tabs {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 16px;
  max-width: 100%;
}
.kind-tab {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 0 10px;
  margin-bottom: -1px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-tertiary);
  cursor: pointer;
  transition: color 0.18s ease, border-color 0.18s ease;
}
.kind-tab:hover {
  color: var(--sa-text-secondary);
}
.kind-tab.is-active {
  color: var(--sa-text-primary);
  font-weight: 700;
  border-bottom-color: var(--sa-text-primary);
}
/* 计数：药丸徽标（纯数字直接跟在文字后面会跟标题粘连） */
.kind-count {
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--sa-subtle);
  font-family: inherit;
  font-size: 11px;
  font-weight: 600;
  color: var(--sa-text-secondary);
}
.kind-tab.is-active .kind-count {
  background: color-mix(in oklab, var(--sa-text-primary) 9%, transparent);
  color: var(--sa-text-primary);
}
/* ===== 场次选择器：极简日历票券 ===== */
.session-pick {
  position: relative;
}
.sp-trigger {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 9px 12px;
  border-radius: 12px;
  border: 1px solid var(--sa-border-subtle);
  /* 票券底：白卡上的一道极浅灰，不抢标题的视觉重量 */
  background: color-mix(in oklab, var(--sa-text-primary) 3%, var(--sa-elevated));
  text-align: left;
  cursor: pointer;
  transition: background-color 0.2s ease, border-color 0.2s ease;
}
.sp-trigger:hover {
  background: color-mix(in oklab, var(--sa-text-primary) 5%, var(--sa-elevated));
  border-color: var(--sa-border);
}
.sp-trigger.is-open {
  /* 展开时与浮层连成一张卡：底部圆角抹平、底边描边让位给浮层，
     底色同步转白 —— 否则上半截浅灰、下半截纯白，接缝处会露出色差。 */
  border-color: var(--sa-border);
  border-bottom-color: transparent;
  border-radius: 12px 12px 0 0;
  background: var(--sa-elevated);
  box-shadow: none;
}

/* 左日期块：月/日大字 + 年份小字，白底微凸，像票根上的日期戳 */
.sp-date {
  flex: none;
  width: 52px;
  height: 44px;
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  border-radius: 8px;
  border: 1px solid var(--sa-hairline);
  background: var(--sa-elevated);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
  font-variant-numeric: tabular-nums;
  font-feature-settings: 'tnum';
}
.sp-date b {
  font-size: 12.5px;
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -0.01em;
  color: var(--sa-text-primary);
}
.sp-date i {
  font-style: normal;
  font-size: 9px;
  line-height: 1;
  letter-spacing: 0.04em;
  color: var(--sa-text-tertiary);
}
/* 深色下票券底比卡片亮，日期块若仍用 --sa-elevated 就会「沉下去」，
   物理方向反了（浅色是白块浮在灰底上）。这里补一档更亮的值保持同向。
   ⚠ 必须排除 .is-active：选中行是深色反相块，被覆盖会失去选中态。 */
:global(html[data-theme='dark']) .sp-trigger .sp-date {
  background: color-mix(in oklab, var(--sa-text-primary) 7%, var(--sa-elevated));
}
:global(html[data-theme='dark']) .sp-opt:not(.is-active) .sp-date {
  background: color-mix(in oklab, var(--sa-text-primary) 6%, var(--sa-elevated));
}

.sp-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.sp-title {
  font-size: 13px;
  font-weight: 700;
  line-height: 1.25;
  color: var(--sa-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sp-meta {
  font-size: 11px;
  line-height: 1.2;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sp-chevron {
  flex: none;
  color: var(--sa-text-tertiary);
  transition: transform 0.3s var(--spring, cubic-bezier(0.16, 1, 0.3, 1));
}
.sp-trigger.is-open .sp-chevron,
.sp-trigger.is-sheet .sp-chevron {
  transform: rotate(180deg);
  color: var(--sa-text-primary);
}

/* 浮层：跟触发器同宽，行内两行排布，不再是「一行 12px 字躺在大玻璃板上」 */
.sp-panel {
  position: absolute;
  top: calc(100% - 1px);
  left: 0;
  right: 0;
  z-index: 60;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px;
  max-height: 340px;
  overflow-y: auto;
  border-radius: 0 0 12px 12px;
  border: 1px solid var(--sa-border);
  border-top-color: transparent;
  background: var(--sa-elevated);
  box-shadow:
    0 18px 36px -16px rgba(15, 23, 42, 0.22),
    0 4px 10px -4px rgba(15, 23, 42, 0.08);
  transform-origin: top center;
}
.sp-opt {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 6px 9px 6px 6px;
  border: 0;
  border-radius: 11px;
  background: none;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.16s ease;
}
.sp-opt:hover {
  background: color-mix(in oklab, var(--sa-text-primary) 6%, transparent);
}
.sp-opt.is-active {
  background: color-mix(in oklab, var(--sa-text-primary) 8%, transparent);
}
.sp-opt .sp-date {
  width: 46px;
  height: 40px;
  background: var(--sa-subtle);
  border-color: transparent;
  box-shadow: none;
}
.sp-opt.is-active .sp-date {
  background: var(--sa-text-primary);
  border-color: var(--sa-text-primary);
}
.sp-opt.is-active .sp-date b {
  color: var(--sa-bg);
}
.sp-opt.is-active .sp-date i {
  color: color-mix(in oklab, var(--sa-bg) 65%, transparent);
}
.sp-opt .sp-title {
  font-size: 12.5px;
}
.sp-check {
  flex: none;
  color: var(--sa-text-primary);
}

.sp-pop-enter-active,
.sp-pop-leave-active {
  transition: opacity 0.18s ease, transform 0.26s cubic-bezier(0.19, 1, 0.22, 1);
}
.sp-pop-enter-from,
.sp-pop-leave-to {
  opacity: 0;
  transform: translateY(-5px) scale(0.985);
}

/* 移动端：底部抽屉 */
.sp-scrim {
  position: fixed;
  inset: 0;
  z-index: 320;
  display: flex;
  align-items: flex-end;
  background: rgba(10, 12, 16, 0.44);
}
.sp-sheet {
  width: 100%;
  max-height: 78vh;
  display: flex;
  flex-direction: column;
  padding: 12px 12px calc(12px + env(safe-area-inset-bottom, 0px));
  border-radius: 20px 20px 0 0;
  background: var(--sa-elevated);
  box-shadow: 0 -20px 56px -14px rgba(0, 0, 0, 0.45);
}
.sp-sheet-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 4px 10px;
}
.sp-sheet-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--sa-text-primary);
}
.sp-sheet-count {
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
}
.sp-sheet-close {
  margin-left: auto;
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 999px;
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  cursor: pointer;
}
.sp-sheet-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}
.sp-sheet .sp-opt {
  padding: 9px 10px 9px 8px;
}
.sp-sheet .sp-date {
  width: 50px;
  height: 44px;
}
.sp-sheet .sp-title {
  font-size: 13.5px;
}

.sp-sheet-enter-active,
.sp-sheet-leave-active {
  transition: opacity 0.22s ease;
}
.sp-sheet-enter-active .sp-sheet,
.sp-sheet-leave-active .sp-sheet {
  transition: transform 0.3s cubic-bezier(0.19, 1, 0.22, 1);
}
.sp-sheet-enter-from,
.sp-sheet-leave-to {
  opacity: 0;
}
.sp-sheet-enter-from .sp-sheet,
.sp-sheet-leave-to .sp-sheet {
  transform: translateY(100%);
}

/* ===== 机位列表：卡内扁平行（透明底，靠淡分割与悬停区分） ===== */
.cam-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cam-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 12px;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  transition: background-color 0.2s ease, border-color 0.2s ease;
}
.cam-item:hover {
  background: color-mix(in oklab, var(--sa-text-primary) 3.5%, transparent);
  border-color: var(--sa-border-subtle);
}
/* 正在播放：主题色 8% 底 + 22% 边，比「主色实边 + 灰底」更轻，也不会跟深色主题打架 */
.cam-item.is-active {
  background: color-mix(in oklab, var(--sa-accent) 8%, transparent);
  border-color: color-mix(in oklab, var(--sa-accent) 22%, transparent);
}
.cam-no {
  width: 18px;
  flex: none;
  text-align: center;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  font-weight: 700;
  color: var(--sa-text-tertiary);
}
.cam-item.is-active .cam-no {
  color: var(--sa-accent);
  font-weight: 700;
}
.cam-avatar {
  width: 36px;
  height: 36px;
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  overflow: hidden;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-hairline);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  font-weight: 700;
  color: var(--sa-text-secondary);
}
.cam-avatar.is-group {
  background: var(--sa-text-primary);
  color: var(--sa-bg);
  border-color: var(--sa-text-primary);
}
.cam-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.cam-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.cam-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 700;
  color: var(--sa-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cam-flag {
  font-size: 10px;
  font-weight: 500;
  color: #b26a00;
}
.cam-sub {
  font-family: inherit;
  font-size: 11.5px;
  color: var(--sa-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 画幅徽标：哑光色块，无描边（描边会让每个机位行多一圈噪点） */
.cam-badge {
  flex: none;
  padding: 3px 8px;
  border-radius: 6px;
  border: 0;
  background: var(--sa-subtle);
  font-family: inherit;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--sa-text-secondary);
}
.cam-item.is-active .cam-badge {
  background: var(--sa-text-primary);
  color: var(--sa-bg);
}

/* 正在播放：三根声波（主题色，跟 active 的浅蓝底呼应） */
.sound-wave {
  display: inline-flex;
  align-items: flex-end;
  gap: 2px;
  height: 11px;
}
.sound-wave i {
  display: inline-block;
  width: 2px;
  border-radius: 1px;
  background: var(--sa-accent);
  animation: wave 1s ease-in-out infinite alternate;
}
.sound-wave i:nth-child(1) {
  height: 5px;
  animation-delay: 0.1s;
}
.sound-wave i:nth-child(2) {
  height: 11px;
  animation-delay: 0.3s;
}
.sound-wave i:nth-child(3) {
  height: 7px;
  animation-delay: 0.2s;
}
@keyframes wave {
  0% {
    transform: scaleY(0.4);
  }
  100% {
    transform: scaleY(1.3);
  }
}
.cam-empty {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  padding: 8px 0;
}
</style>
