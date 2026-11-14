<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NSpin } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import PhotoGallery from '@/components/PhotoGallery.vue'
import VideoFilterBar from '@/components/VideoFilterBar.vue'
import { groupsApi } from '@/api/groups'
import { artistsApi } from '@/api/artists'
import { membershipsApi } from '@/api/memberships'
import { companiesApi } from '@/api/companies'
import { musicVideosApi } from '@/api/musicVideos'
import type { Company, Group, MembershipWithArtist, MusicVideo } from '@/types/models'
import { VIDEO_TYPE_LABEL } from '@/types/models'
import { formatDate } from '@/utils/format'
import { resolutionLabel } from '@/utils/resolution'
import { artistPath, fetchByRouteParam, groupPath, videoPath } from '@/utils/routes'
import { MV_TYPES, LIVE_TYPES, videoHasType } from '@/utils/videoTypes'
import { IMG_W_CARD, heroImageWidth } from '@/utils/imageSizes'
import { useEntityDetail, ENTITY_TABS } from '@/composables/useEntityDetail'
import { useSettingsStore } from '@/stores/settings'
import { GroupOutlined, MovieOutlined } from '@/components/icons'

const router = useRouter()
// 海报压暗档位（设置 →「组合页海报压暗」）：1 轻度 / 2 中等 / 3 通透
const settings = useSettingsStore()
const heroDimClass = computed(() => `gp-hero--dim${settings.groupHeroDim}`)
// 本页不再有「概览」分区：简介搬进 hero，维度胶囊直接落到作品
const activeTab = ref('all')

const members = ref<MembershipWithArtist[]>([])
const companies = ref<Company[]>([])
const portraitStamp = ref(Date.now())

const {
  entity: group,
  loading,
  notFound,
  videosError,
  retryVideos,
  videos,
  longVideos,
  shortVideos,
  displayedVideos,
  videoSongId,
  videoSearchText,
  videoSortKey,
  videoSongOptions,
  hasVideoFilters,
  isPhotoTab,
  photoSection,
} = useEntityDetail<Group>(
  {
    fetchEntity: (param) => fetchByRouteParam(param, groupsApi.getByUid, groupsApi.get),
    entityPath: groupPath,
    fetchVideos: (g) => musicVideosApi.listAll({ group_id: g.id, ingestion_status: 'library' }),
    fetchExtras: async (g, isCurrent) => {
      const [ms, cs] = await Promise.all([
        membershipsApi.byGroup(g.id).catch(() => [] as MembershipWithArtist[]),
        companiesApi.brief().catch(() => [] as Company[]),
      ])
      if (!isCurrent()) return
      members.value = ms
      companies.value = cs
    },
  },
  activeTab,
)

// ===== 第一屏：聚光灯舞台 =====
const heroName = computed(
  () =>
    group.value?.english_name ||
    group.value?.name ||
    group.value?.chinese_name ||
    group.value?.korean_name ||
    '',
)
const koreanName = computed(() => group.value?.korean_name || '')
const companyNames = computed(() => {
  const ids = group.value?.company_ids || []
  if (!ids.length) return []
  return companies.value
    .filter((x) => ids.includes(x.id))
    .map((c) => c.chinese_name || c.name)
    .filter(Boolean)
})
const companyName = computed(() => companyNames.value[0] || '')

const sortedMembers = computed(() => {
  const list = [...members.value]
  list.sort((a, b) => {
    const order = (s?: string) => (s === 'Active' ? 0 : 1)
    const od = order(a.status) - order(b.status)
    if (od) return od
    return (a.join_date || '').localeCompare(b.join_date || '')
  })
  return list
})
/** 悬停临时预览（仅鼠标）/ 点击常驻预览 —— 显示时以常驻优先 */
const hoverMember = ref<MembershipWithArtist | null>(null)
const pinnedMember = ref<MembershipWithArtist | null>(null)
const spotMember = computed<MembershipWithArtist | null>(
  () => pinnedMember.value ?? hoverMember.value,
)

// 悬停防抖：快速划过名字条时不逐个加载大图（150ms 内离开就取消）
let hoverTimer: ReturnType<typeof setTimeout> | undefined
const HOVER_DELAY_MS = 150

// ===== 移动端首屏（复刻稿）：ABOUT / MEMBERS 下划线 Tab =====
const mobileTab = ref<'intro' | 'members'>('intro')
/** 移动端「展开完整简介」= 毛玻璃弹窗，不再把 hero 撑高 */
const bioModalOpen = ref(false)
function onBioKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && bioModalOpen.value) bioModalOpen.value = false
}
onMounted(() => window.addEventListener('keydown', onBioKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onBioKey))
/** 出道年月（复刻稿 meta 行：2022.10 出道 · 24 MEMBERS） */
const debutLabel = computed(() => {
  const d = group.value?.debut_date
  if (!d || d.length < 7) return ''
  return `${d.slice(0, 4)}.${d.slice(5, 7)} 出道`
})
function hoverIn(m: MembershipWithArtist) {
  if (hoverTimer) clearTimeout(hoverTimer)
  hoverTimer = setTimeout(() => {
    hoverMember.value = m
  }, HOVER_DELAY_MS)
}
function hoverOut() {
  if (hoverTimer) clearTimeout(hoverTimer)
  hoverMember.value = null
}
onBeforeUnmount(() => {
  if (hoverTimer) clearTimeout(hoverTimer)
})

function memberName(m: MembershipWithArtist) {
  return (
    m.artist_chinese_name ||
    m.artist_stage_name ||
    m.artist_korean_name ||
    m.artist_name ||
    `#${m.artist_id}`
  )
}
function memberAvatarSrc(m: MembershipWithArtist, w = 256) {
  // v=artist_updated_at：换成员头像后成员格不会卡在一年期缓存里
  return `${artistsApi.avatarUrl(m.artist_id)}?w=${w}&v=${encodeURIComponent(m.artist_updated_at || '')}`
}
/** 拉丁/艺名：mockup 里配 Instrument Serif 斜体大字的那一行 */
function memberEn(m: MembershipWithArtist) {
  return m.artist_stage_name || m.artist_name || ''
}
/** mockup 的三档字号：≤4 人 / ≤9 人 / 更多各一档，随人数自动降档 */
const rosterSizeClass = computed(() => {
  const n = sortedMembers.value.length
  if (n <= 4) return 'gp-roster-list--s4'
  if (n <= 9) return 'gp-roster-list--s9'
  return 'gp-roster-list--s24'
})

/** 每位成员在库内的作品数（按视频关联艺人 + 主体艺人统计） */
function memberWorks(m: MembershipWithArtist): number {
  const id = m.artist_id
  return videos.value.filter(
    (v) => v.subject_artist_id === id || (v.artists || []).some((a) => a.id === id),
  ).length
}

const heroImg = computed(() => {
  const g = group.value
  if (!g) return ''
  // 成员主视觉同样需要版本号：换成员头像后要立刻生效
  if (spotMember.value) {
    return `${memberAvatarSrc(spotMember.value, heroImageWidth())}&t=${portraitStamp.value}`
  }
  const base = g.banner_path
    ? groupsApi.bannerUrl(g.id)
    : g.avatar_path
      ? groupsApi.avatarUrl(g.id)
      : ''
  return base ? `${base}?w=${heroImageWidth()}&t=${portraitStamp.value}` : ''
})

const heroKicker = computed(() => {
  if (spotMember.value) {
    const m = spotMember.value
    return [m.status === 'Former' ? '已退出成员' : '现役成员', `${memberWorks(m)} 部作品`]
      .filter(Boolean)
      .join(' · ')
  }
  return [koreanName.value, companyName.value].filter(Boolean).join(' · ')
})
const heroDesc = computed(() => {
  if (spotMember.value) {
    const m = spotMember.value
    return `${memberName(m)} 在 ${heroName.value} 期间共入库 ${memberWorks(m)} 部作品，点击下方维度可只看这位成员参与的舞台。`
  }
  return (group.value?.description || '').replace(/\{BR\}/g, '\n')
})

// ===== 维度胶囊（与 ENTITY_TABS 同源，去掉「概览」）=====
interface DimTab {
  key: string
  label: string
  count: number | null
}
const dims = computed<DimTab[]>(() => {
  const pool = longVideos.value
  const countOf = (key: string): number | null => {
    switch (key) {
      case 'all':
        return longVideos.value.length + shortVideos.value.length
      case 'mv':
        return pool.filter((v) => videoHasType(v, MV_TYPES)).length
      case 'live':
        return pool.filter((v) => videoHasType(v, LIVE_TYPES)).length
      case 'behind':
        return pool.filter((v) => videoHasType(v, ['SpecialVideo', 'Other'])).length
      case 'shorts':
        return shortVideos.value.length
      default:
        // 照片分区（官方 / 粉丝 / 照片墙）不预取数量，交给 PhotoGallery 自己报
        return null
    }
  }
  return ENTITY_TABS.filter((t) => t.key !== 'overview').map((t) => ({
    key: t.key,
    label: t.label,
    count: countOf(t.key),
  }))
})


// 白色滑块：测量激活按钮的 offsetLeft/width
const trackEl = ref<HTMLElement | null>(null)
const railScrollEl = ref<HTMLElement | null>(null)
const pillStyle = ref<Record<string, string>>({ width: '0px', transform: 'translateX(0px)' })
function movePill() {
  const btn = trackEl.value?.querySelector<HTMLElement>('button[data-active="1"]')
  if (!btn) return
  pillStyle.value = {
    width: `${btn.offsetWidth}px`,
    transform: `translateX(${btn.offsetLeft}px)`,
  }
  // 选中项滑出可视区时把胶囊列横向滚过去，否则滑块跑到屏外、看起来像“没切换”
  const sc = railScrollEl.value
  if (!sc) return
  const br = btn.getBoundingClientRect()
  const sr = sc.getBoundingClientRect()
  const pad = 6
  if (br.left - pad < sr.left) {
    sc.scrollLeft -= sr.left - br.left + pad
  } else if (br.right + pad > sr.right) {
    sc.scrollLeft += br.right - sr.right + pad
  }
}
watch(activeTab, () => void nextTick(movePill))
watch(dims, () => void nextTick(movePill))

// ===== 成员过滤 =====
const memberFilter = ref<number | null>(null)

const gridVideos = computed(() => {
  const list = displayedVideos.value
  const id = memberFilter.value
  if (!id) return list
  return list.filter(
    (v) => v.subject_artist_id === id || (v.artists || []).some((a) => a.id === id),
  )
})

const worksEl = ref<HTMLElement | null>(null)

function scrollToWorks() {
  worksEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
/** 作品区「只看该成员」chip：筛选入口从成员名挪到了作品区 */
function pickMember(m: MembershipWithArtist) {
  memberFilter.value = memberFilter.value === m.artist_id ? null : m.artist_id
}
function clearMember() {
  memberFilter.value = null
}
/** 还原组合全员主视觉（清掉成员投影 + 成员过滤） */
function resetHero() {
  pinnedMember.value = null
  hoverMember.value = null
  memberFilter.value = null
}

// ===== 分片渲染 =====
// 908 条视频的团（tripleS）一次性渲染 822 张卡会产生 ~1s 的主线程长任务。
// 这里首屏只挂 60 张，滚到哨兵再追加一批；视觉、动效与排序完全不变。
const PAGE_SIZE = 60
/** 只有前 24 张跑入场波浪，后续分片直接显示（否则追加时会集体「渐入」） */
const WAVE_LIMIT = 24
const renderLimit = ref(PAGE_SIZE)
const shownVideos = computed(() => gridVideos.value.slice(0, renderLimit.value))
const hasMoreVideos = computed(() => gridVideos.value.length > renderLimit.value)
// 切维度 / 换成员 / 搜索 / 排序 → 回到第一片
watch(gridVideos, () => {
  renderLimit.value = PAGE_SIZE
})
const sentinelEl = ref<HTMLElement | null>(null)
let sentinelObserver: IntersectionObserver | null = null
watch(sentinelEl, (el) => {
  sentinelObserver?.disconnect()
  if (!el) return
  sentinelObserver = new IntersectionObserver(
    (entries) => {
      if (!entries.some((e) => e.isIntersecting)) return
      renderLimit.value = Math.min(renderLimit.value + PAGE_SIZE, gridVideos.value.length)
    },
    // 提前 800px 预挂下一批，滚动手感不受影响
    { rootMargin: '800px 0px' },
  )
  sentinelObserver.observe(el)
})
onBeforeUnmount(() => {
  sentinelObserver?.disconnect()
  sentinelObserver = null
})

// ===== 卡片内容 =====
const brokenThumbs = ref<Set<number>>(new Set())
function thumbOf(v: MusicVideo): string {
  if (brokenThumbs.value.has(v.id)) return ''
  return musicVideosApi.thumbnailUrl(v.id, v.file_hash || v.file_size, IMG_W_CARD)
}
function onThumbError(id: number) {
  brokenThumbs.value = new Set(brokenThumbs.value).add(id)
}
function tagOf(v: MusicVideo): string {
  return VIDEO_TYPE_LABEL[v.video_type] || v.video_type || '作品'
}
function badgeOf(v: MusicVideo): string {
  const label = resolutionLabel(v.width, v.height)
  return label === '--' ? '' : label
}
function metaOf(v: MusicVideo): string {
  return [v.event_name, formatDate(v.performance_date || v.published_date)]
    .filter((x) => x && x !== '--')
    .join(' · ')
}
/** 移动端卡片脚注只留日期：390 宽下「活动名 + 日期」会把类型标签挤没 */
function dateOf(v: MusicVideo): string {
  return formatDate(v.performance_date || v.published_date) || ''
}

// ===== 交互 =====
function goDbEdit() {
  if (!group.value) return
  void router.push(`/db/groups/${group.value.uid}`)
}

function goVideo(uid: string) {
  router.push(videoPath(uid))
}
function goArtist(uid?: string | null) {
  if (!uid) return
  router.push(artistPath(uid))
}
function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/')
}

async function onPortraitApplied() {
  if (!group.value) return
  try {
    group.value = await groupsApi.get(group.value.id)
    portraitStamp.value = Date.now()
  } catch {
    /* 裁切已成功，刷新失败时下次进入页面会看到 */
  }
}

onMounted(() => {
  void nextTick(movePill)
  window.addEventListener('resize', movePill)
})
onBeforeUnmount(() => window.removeEventListener('resize', movePill))
</script>

<template>
  <div class="gp">
    <SaHeader overlay />

    <n-spin :show="loading">
      <div v-if="notFound" class="gp-empty">
        <GroupOutlined :size="40" />
        <p>组合不存在或已删除</p>
        <button class="gp-empty-btn" type="button" @click="goBack">返回首页</button>
      </div>

      <div v-else-if="group" class="gp-body">

        <!-- ============ 第一屏：100vh 聚光灯舞台 ============ -->
        <section
          class="gp-hero"
          :class="[
            heroDimClass,
            { 'gp-hero--member': !!spotMember },
          ]"
          @click.self="pinnedMember = null"
        >
          <!-- 三层遮罩：亮度压到 62% 的大片 + 紫黑星光晕影 + 上下渐变压暗 -->
          <div class="gp-hero-media" aria-hidden="true">
            <Transition name="gp-xfade">
              <img v-if="heroImg" :key="heroImg" class="gp-hero-img" :src="heroImg" alt="" />
            </Transition>
            <div class="gp-mask-radial" />
            <div class="gp-mask-linear" />
          </div>

          <!-- 移动端刊头烙印：与首页焦点海报同款做法（移动端全站无顶栏） -->
          <span class="gp-m-brand" aria-hidden="true">IDOLMATRIX.</span>

          <div class="gp-hero-center">
            <div class="gp-eyebrow-row">
              <p v-if="spotMember" class="gp-eyebrow">
                <span class="gp-eyebrow-chip">
                  {{ String(sortedMembers.indexOf(spotMember) + 1).padStart(2, '0') }} · 成员
                </span>
                已收录 {{ memberWorks(spotMember) }} 部关联视频
              </p>
              <button v-else class="gp-kicker gp-kicker--link" type="button" title="进入资料库编辑" @click="goDbEdit">
                {{ heroKicker }}
              </button>
              <button
                v-if="memberFilter"
                class="gp-reset"
                type="button"
                @click="resetHero"
              >
                ↺ 还原组合全员主视觉
              </button>
            </div>
            <!-- 韩文名不再跟在大字后面重复一次（上方 kicker 已有「에이핑크 · 公司」） -->
            <h1 v-if="!spotMember" class="gp-name">
              {{ heroName }}
            </h1>
            <h1 v-else class="gp-name gp-name--member">
              {{ memberName(spotMember) }}
              <span v-if="memberEn(spotMember)" class="gp-name-en">{{ memberEn(spotMember) }}</span>
            </h1>
            <p v-if="heroDesc" class="gp-desc">{{ heroDesc }}</p>
            <div class="gp-actions">
              <button class="gp-cta" type="button" @click="scrollToWorks">
                浏览全部 {{ longVideos.length + shortVideos.length }} 部作品 ↓
              </button>
              <button
                v-if="spotMember"
                class="gp-cta gp-cta--ghost"
                type="button"
                @click="goArtist(spotMember.artist_uid)"
              >
                打开 {{ memberName(spotMember) }} 的成员页 ↗
              </button>
              <button
                v-else-if="group.parent_uid"
                class="gp-cta gp-cta--ghost"
                type="button"
                @click="router.push(groupPath(group.parent_uid!))"
              >
                隶属于 {{ group.parent_name }}
              </button>
            </div>
          </div>

          <!-- ===== 移动端首屏信息（复刻稿：下划线 Tab + 成员胶囊） ===== -->
          <div class="gp-m-info">
            <p class="gp-m-kicker">
              <i class="gp-m-dot" aria-hidden="true" />
              <span>{{ heroKicker }}</span>
            </p>
            <h1 class="gp-m-name">{{ heroName }}</h1>

            <div class="gp-m-tabs" role="tablist">
              <button
                class="gp-m-tab"
                :class="{ 'gp-m-tab--on': mobileTab === 'intro' }"
                type="button"
                role="tab"
                :aria-selected="mobileTab === 'intro'"
                @click="mobileTab = 'intro'"
              >
                ABOUT 组合简介
              </button>
              <button
                class="gp-m-tab"
                :class="{ 'gp-m-tab--on': mobileTab === 'members' }"
                type="button"
                role="tab"
                :aria-selected="mobileTab === 'members'"
                @click="mobileTab = 'members'"
              >
                MEMBERS 成员 ({{ sortedMembers.length }})
              </button>
            </div>

            <!-- 简介 / 成员 叠在同一格：容器高度取两者较大值，切 tab 时 hero 不会跳 -->
            <div class="gp-m-stack">
              <div
                class="gp-m-panel gp-m-intro"
                :class="{ 'is-off': mobileTab !== 'intro' }"
                :aria-hidden="mobileTab !== 'intro'"
              >
                <p class="gp-m-desc">{{ heroDesc || '暂无简介' }}</p>
                <div class="gp-m-meta-row">
                  <div class="gp-m-meta">
                    <span v-if="debutLabel">{{ debutLabel }}</span>
                    <span v-if="debutLabel" class="gp-m-sep">•</span>
                    <span>{{ sortedMembers.length }} MEMBERS</span>
                  </div>
                  <button class="gp-m-expand" type="button" @click="bioModalOpen = true">
                    展开完整简介
                    <span class="gp-m-caret" aria-hidden="true">›</span>
                  </button>
                </div>
              </div>

              <div
                class="gp-m-panel gp-m-members"
                :class="{ 'is-off': mobileTab !== 'members' }"
                :aria-hidden="mobileTab !== 'members'"
              >
              <div class="gp-m-caps">
                <button
                  v-for="m in sortedMembers"
                  :key="m.id"
                  type="button"
                  class="gp-m-cap"
                  :class="{ 'gp-m-cap--on': memberFilter === m.artist_id }"
                  :title="`只看 ${memberName(m)} 的作品`"
                  @click="pickMember(m)"
                >
                  <img
                    v-if="m.artist_avatar_path"
                    class="gp-m-cap-img"
                    :src="memberAvatarSrc(m, 96)"
                    alt=""
                    loading="lazy"
                  />
                  <span v-else class="gp-m-cap-img gp-m-cap-img--ph" aria-hidden="true">
                    {{ (memberEn(m) || memberName(m)).charAt(0) }}
                  </span>
                  <span class="gp-m-cap-en">{{ memberEn(m) || memberName(m) }}</span>
                  <span v-if="memberEn(m) && memberEn(m) !== memberName(m)" class="gp-m-cap-cn">
                    {{ memberName(m) }}
                  </span>
                </button>
              </div>
            </div>
          </div>
          </div>

          <!-- 成员名册：悬停投影到 hero，点击只看该成员 -->
          <div v-if="sortedMembers.length" class="gp-roster">
            <div class="gp-roster-list" :class="rosterSizeClass">
              <button
                v-for="(m, i) in sortedMembers"
                :key="m.id"
                type="button"
                class="gp-roster-item"
                :class="{
                  'gp-roster-item--on': spotMember?.id === m.id,
                  'gp-roster-item--filter': memberFilter === m.artist_id,
                  'gp-roster-item--former': m.status === 'Former',
                }"
                @mouseenter="hoverIn(m)"
                @mouseleave="hoverOut"
                @focus="pinnedMember = m"
                @click="pinnedMember = m"
              >
                <span class="gp-roster-num">{{ String(i + 1).padStart(2, '0') }}</span>
                <span class="gp-roster-name">{{ memberEn(m) || memberName(m) }}</span>
                <span v-if="memberEn(m) && memberEn(m) !== memberName(m)" class="gp-roster-cn">
                  {{ memberName(m) }}
                </span>
              </button>
              <button
                v-if="memberFilter"
                type="button"
                class="gp-roster-clear"
                @click="clearMember"
              >
                ✕ 还原全员
              </button>
            </div>
          </div>
        </section>

        <!-- ============ 吸顶维度胶囊 ============ -->
        <div ref="worksEl" class="gp-rail-wrap">
          <div class="gp-rail">
            <div ref="railScrollEl" class="gp-rail-scroll">
              <div ref="trackEl" class="gp-rail-track">
                <span class="gp-rail-pill" :style="pillStyle" />
                <button
                  v-for="d in dims"
                  :key="d.key"
                  type="button"
                  class="gp-rail-btn"
                  :class="{ 'gp-rail-btn--active': activeTab === d.key }"
                  :data-active="activeTab === d.key ? '1' : '0'"
                  @click="activeTab = d.key"
                >
                  {{ d.label }}
                  <i v-if="d.count != null" class="gp-rail-count">{{ d.count }}</i>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- ============ 作品区 ============ -->
        <section class="gp-works">
          <div v-if="videosError && !isPhotoTab" class="gp-error">
            <span>{{ videosError }}</span>
            <button class="gp-empty-btn" type="button" @click="retryVideos">重试</button>
          </div>

          <PhotoGallery
            v-else-if="isPhotoTab && group"
            owner-type="group"
            :owner-id="group.id"
            :section="photoSection"
            @portrait-applied="onPortraitApplied"
          />

          <template v-else>
            <!-- 作品多时才出现筛选条：小组合保持纯画廊观感，大组合仍可搜歌 / 排序 -->
            <div v-if="spotMember" class="gp-member-filter">
              <button class="gp-member-chip" type="button" @click="pickMember(spotMember)">
                {{ memberFilter === spotMember.artist_id ? '取消筛选' : '只看' }}{{ memberName(spotMember) }}的作品
              </button>
            </div>

            <VideoFilterBar
              v-if="gridVideos.length > 12 || hasVideoFilters"
              :count="gridVideos.length"
              :songs="videoSongOptions"
              v-model:song-id="videoSongId"
              v-model:sort="videoSortKey"
              v-model:query="videoSearchText"
            />

            <template v-if="gridVideos.length">
              <div class="gp-darkroom">
              <article
                v-for="(v, i) in shownVideos"
                :key="v.id"
                class="gp-item gallery-card"
                :class="{ 'gp-item--short': activeTab === 'shorts', 'gp-item--static': i >= WAVE_LIMIT }"
                :style="i < WAVE_LIMIT ? { animationDelay: `${i * 40}ms` } : undefined"
                @click="goVideo(v.uid)"
              >
                <div class="gc-cover">
                  <img
                    v-if="thumbOf(v)"
                    class="gc-img"
                    :src="thumbOf(v)"
                    alt=""
                    loading="lazy"
                    decoding="async"
                    @error="onThumbError(v.id)"
                  />
                  <div v-else class="gc-img gc-placeholder">
                    <MovieOutlined :size="26" />
                  </div>
                </div>
                <h3 class="gc-title" :title="v.name">{{ v.name }}</h3>
                <div class="gc-foot">
                  <span class="gc-type">{{ tagOf(v) }}</span>
                  <div class="gc-nums">
                    <span>{{ badgeOf(v) || '—' }}</span>
                    <span class="gc-dot">●</span>
                    <span class="gc-res gc-res--full">{{ metaOf(v) || '—' }}</span>
                    <span class="gc-res gc-res--short">{{ dateOf(v) || '—' }}</span>
                  </div>
                </div>
              </article>
            </div>

              <!-- 分片渲染哨兵：滚到这里再追加下一批（空元素，不改变视觉） -->
              <div v-if="hasMoreVideos" ref="sentinelEl" class="gp-sentinel" aria-hidden="true" />
            </template>

            <div v-else class="gp-empty gp-empty--inline">
              <MovieOutlined :size="32" />
              <p>{{ memberFilter ? '该成员在此分类下暂无作品' : '该分类下暂无作品' }}</p>
              <button v-if="memberFilter" class="gp-empty-btn" type="button" @click="clearMember">
                还原全员
              </button>
            </div>
          </template>
        </section>
      </div>
    </n-spin>

    <!-- 移动端「展开完整简介」：毛玻璃弹窗（不再撑高 hero） -->
    <transition name="gp-bio-pop">
      <div v-if="bioModalOpen" class="gp-bio-mask" @click.self="bioModalOpen = false">
        <div class="gp-bio-card" role="dialog" aria-modal="true" aria-label="组合简介">
          <div class="gp-bio-head">
            <span class="gp-bio-kicker">ABOUT</span>
            <button class="gp-bio-close" type="button" aria-label="关闭" @click="bioModalOpen = false">
              ✕
            </button>
          </div>
          <h2 class="gp-bio-title">{{ heroName }}</h2>
          <div class="gp-bio-body">{{ heroDesc || '暂无简介' }}</div>
          <div class="gp-bio-foot">
            <span v-if="debutLabel">{{ debutLabel }}</span>
            <span v-if="debutLabel" class="gp-bio-sep">•</span>
            <span>{{ sortedMembers.length }} MEMBERS</span>
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.gp {
  --spring: cubic-bezier(0.16, 1, 0.3, 1);
  /* 统一左基线：≥769px 时与顶栏品牌 IDOLMATRIX. 的 left:36px 严格对齐，
     顶栏隐藏的窄屏才收窄成 16~24px */
  --gp-gutter: clamp(14px, 4vw, 24px);
  min-height: 100vh;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
  -webkit-font-smoothing: antialiased;
}
@media (min-width: 769px) {
  .gp {
    --gp-gutter: 36px;
  }
}

/* ================= 第一屏 ================= */
/* ================= 第一屏：聚光灯舞台 ================= */
.gp-hero {
  --ink: #ffffff;
  --ink-2: rgba(255, 255, 255, 0.74);
  --ink-3: rgba(255, 255, 255, 0.5);
  --hair: rgba(255, 255, 255, 0.12);
  /* 海报压暗档位：设置 →「组合页海报压暗」切换（1 轻度 / 2 中等 / 3 通透）。
     三层是相乘关系：中心透光率 ≈ 图片亮度 × (1-晕影黑) × (1-渐变黑)。
     1 档 ≈ 28%，2 档 ≈ 45%，3 档 ≈ 68%；现状（旧值 0.62/0.65/0.30）只有 ≈15%。 */
  --gp-img-bright: 0.76;
  --gp-mask-radial-bg: radial-gradient(
    circle at 65% 38%,
    rgba(139, 92, 246, 0.14) 0%,
    rgba(8, 8, 11, 0.52) 58%,
    #08080b 92%
  );
  --gp-mask-linear-bg: linear-gradient(
    to top,
    #08080b 0%,
    rgba(8, 8, 11, 0.22) 50%,
    rgba(0, 0, 0, 0.36) 100%
  );
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: clamp(16px, 3.5vh, 36px);
  /* 背景直接通顶：负 margin 把整块顶到视口上沿（顶栏改为透明叠在上面） */
  margin-top: calc(-1 * var(--sa-header-height, 77px));
  min-height: 100svh;
  padding: calc(var(--sa-header-height, 77px) + clamp(8px, 1.4vw, 16px))
    var(--gp-gutter) 40px;
  overflow: hidden;
  isolation: isolate;
  background: #08080b;
  color: var(--ink);
}
.gp-hero-media {
  position: absolute;
  inset: 0;
  z-index: 0;
}
.gp-hero-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  filter: brightness(var(--gp-img-bright));
  transform: scale(1.05);
}
/* 紫黑星光晕影：中心 65%/38% 透出一点紫，边缘收到近乎纯黑 */
.gp-mask-radial {
  position: absolute;
  inset: 0;
  background: var(--gp-mask-radial-bg);
}
/* 上下压暗：底部实边沉入页面底色 */
.gp-mask-linear {
  position: absolute;
  inset: 0;
  background: var(--gp-mask-linear-bg);
}

/* ===== 压暗档位（2 中等 / 3 通透）：越靠右海报越亮，文字靠光晕兜底 ===== */
.gp-hero--dim2 {
  --gp-img-bright: 0.88;
  --gp-mask-radial-bg: radial-gradient(
    circle at 65% 38%,
    rgba(139, 92, 246, 0.16) 0%,
    rgba(8, 8, 11, 0.4) 60%,
    #08080b 95%
  );
  --gp-mask-linear-bg: linear-gradient(
    to top,
    #08080b 0%,
    rgba(8, 8, 11, 0.14) 50%,
    rgba(0, 0, 0, 0.24) 100%
  );
}
.gp-hero--dim3 {
  --gp-img-bright: 0.98;
  --gp-mask-radial-bg: radial-gradient(
    circle at 65% 38%,
    rgba(139, 92, 246, 0.1) 0%,
    rgba(8, 8, 11, 0.26) 62%,
    #08080b 92%
  );
  --gp-mask-linear-bg: linear-gradient(
    to top,
    #08080b 0%,
    rgba(8, 8, 11, 0.06) 42%,
    transparent 100%
  );
}
/* 3 档背景最亮：标题/副标题加一层柔和光晕，保证大字仍可读 */
.gp-hero--dim3 .gp-name,
.gp-hero--dim3 .gp-desc,
.gp-hero--dim3 .gp-kicker,
.gp-hero--dim3 .gp-eyebrow {
  text-shadow: 0 2px 24px rgba(0, 0, 0, 0.55);
}
/* 成员投影切图：交叉淡入（mockup 先把图降到 35% 再换源） */
.gp-xfade-enter-active,
.gp-xfade-leave-active {
  transition: opacity 0.42s ease;
}
.gp-xfade-enter-from,
.gp-xfade-leave-to {
  opacity: 0;
}

/* 状态行（已精简为：一行小字 + 需要时的「还原」按钮） */
.gp-member-filter {
  display: flex;
  justify-content: flex-start;
  padding: 0 0 10px;
}
.gp-member-chip {
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  font-size: 12px;
  cursor: pointer;
}
.gp-member-chip:hover {
  color: var(--sa-text-primary);
  border-color: var(--sa-border);
}
.rotation-chip {
  position: fixed;
  top: calc(var(--sa-header-h, 64px) + 14px);
  right: 20px;
  z-index: 60;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.28);
  background: rgba(0, 0, 0, 0.42);
  backdrop-filter: blur(10px);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}
.rotation-chip.is-on {
  border-color: rgba(4, 133, 247, 0.8);
  background: rgba(4, 133, 247, 0.28);
}
.rotation-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.5);
}
.rotation-chip.is-on .rotation-dot {
  background: #0485f7;
}
.gp-kicker--link {
  padding: 0;
  border: 0;
  background: transparent;
  font: inherit;
  color: inherit;
  cursor: pointer;
}
.gp-kicker--link:hover {
  opacity: 0.82;
}
.gp-eyebrow-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  margin-bottom: 12px;
}
.gp-eyebrow-row .gp-kicker,
.gp-eyebrow-row .gp-eyebrow {
  margin: 0;
}
.gp-reset {
  padding: 4px 11px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  color: var(--ink);
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
  transition:
    background-color 0.25s ease,
    color 0.25s ease;
}
.gp-reset:hover {
  background: #ffffff;
  color: #08080b;
}

/* 中部刊头 */
.gp-hero-center {
  position: relative;
  z-index: 2;
  max-width: 920px;
  margin-block: auto;
  padding-block: clamp(8px, 2vh, 24px);
}
.gp-kicker {
  margin: 0 0 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: #c4b5fd;
}
.gp-eyebrow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  font-size: 11.5px;
  color: var(--ink-2);
}
.gp-eyebrow-chip {
  padding: 3px 10px;
  border-radius: 999px;
  background: #8b5cf6;
  color: #ffffff;
  font-size: 11px;
  font-weight: 700;
}
.gp-name {
  margin: 0;
  /* mockup 的 font-serif-kr：CJK 衬线优先，拉丁回退到同族重字重 */
  font-family: 'Playfair Display', 'Noto Serif SC', 'Songti SC', 'SimSun', serif;
  font-size: clamp(52px, 9.4vw, 132px);
  line-height: 0.9;
  letter-spacing: -0.045em;
  font-weight: 900;
  color: var(--ink);
  text-shadow: 0 18px 60px rgba(0, 0, 0, 0.55);
}
/* 成员投影：中文名照旧，右侧补拉丁名（mockup 用 Instrument Serif 斜体 + 紫色 + 常规字重） */
.gp-name--member {
  letter-spacing: -0.02em;
}
.gp-name-en {
  margin-left: clamp(8px, 1vw, 18px);
  font-family: 'Instrument Serif', 'Playfair Display', serif;
  font-style: italic;
  font-weight: 400;
  font-size: clamp(30px, 4.6vw, 62px);
  letter-spacing: 0;
  color: #c4b5fd;
  vertical-align: 0.06em;
  white-space: nowrap;
}
.gp-desc {
  max-width: 620px;
  margin: 16px 0 0;
  font-size: 13px;
  line-height: 1.75;
  color: rgba(255, 255, 255, 0.86);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.gp-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 20px;
}
.gp-cta {
  padding: 11px 22px;
  border: none;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  font-family: inherit;
  color: #08080b;
  background: #ffffff;
  box-shadow: 0 15px 35px rgba(255, 255, 255, 0.22);
  cursor: pointer;
  transition:
    transform 0.35s var(--spring),
    box-shadow 0.35s var(--spring);
}
.gp-cta:hover {
  transform: translateY(-2px) scale(1.02);
  box-shadow: 0 18px 42px rgba(255, 255, 255, 0.3);
}
.gp-cta--ghost {
  color: var(--ink);
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.18);
  box-shadow: none;
  backdrop-filter: blur(10px);
}
.gp-cta--ghost:hover {
  background: rgba(255, 255, 255, 0.18);
  box-shadow: none;
}

/* 资料行：原来「资料速览」侧栏里的关键信息，压成一行不占版面 */
/* 成员名册（mockup：pt-6 + 顶部分隔线 + 一行 mono 说明 + 人名流） */
.gp-roster {
  position: relative;
  z-index: 3;
  padding-top: 24px;
  border-top: 1px solid var(--hair);
}
.gp-roster-list {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 24px;
}
.gp-roster-item {
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
  touch-action: manipulation;
  display: inline-flex;
  align-items: baseline;
  padding: 0;
  border: none;
  background: none;
  color: #ffffff;
  font-family: inherit;
  text-align: left;
  opacity: 0.55;
  cursor: pointer;
  transition: opacity 0.3s ease, color 0.3s ease;
}
.gp-roster-item:hover,
.gp-roster-item--on {
  opacity: 1;
}
.gp-roster-item--filter {
  opacity: 1;
  color: #c4b5fd;
  text-decoration: underline;
  text-underline-offset: 8px;
}
.gp-roster-item--former {
  opacity: 0.34;
}
.gp-roster-num {
  margin-right: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  color: #a78bfa;
}
.gp-roster-name {
  font-family: 'Instrument Serif', 'Playfair Display', 'Noto Serif SC', serif;
  font-style: italic;
  font-weight: 400;
  line-height: 1;
  letter-spacing: -0.025em;
}
.gp-roster-cn {
  margin-left: 4px;
  font-size: 12px;
  font-weight: 500;
  color: #d4d4d8;
}
/* 字号随人数分档（mockup：text-4xl/6xl · text-3xl/5xl · text-xl/3xl，sm=640 起放大） */
.gp-roster-list--s4 .gp-roster-name {
  font-size: 36px;
}
.gp-roster-list--s9 .gp-roster-name {
  font-size: 30px;
}
.gp-roster-list--s24 .gp-roster-name {
  font-size: 20px;
}
@media (min-width: 640px) {
  .gp-roster-list--s4 .gp-roster-name {
    font-size: 60px;
  }
  .gp-roster-list--s9 .gp-roster-name {
    font-size: 48px;
  }
  .gp-roster-list--s24 .gp-roster-name {
    font-size: 30px;
  }
}
.gp-roster-clear {
  margin-left: 4px;
  padding: 4px 10px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  background: none;
  color: var(--ink-2);
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  transition:
    background-color 0.25s ease,
    color 0.25s ease;
}
.gp-roster-clear:hover {
  background: #ffffff;
  color: #08080b;
}


/* ================= 吸顶胶囊 ================= */
.gp-rail-wrap {
  position: sticky;
  top: var(--sa-header-height, 77px);
  z-index: 20;
  scroll-margin-top: calc(var(--sa-header-height, 77px) + 12px);
}
.gp-rail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px var(--gp-gutter);
  border-top: 1px solid var(--sa-border-subtle);
  border-bottom: 1px solid var(--sa-border-subtle);
  /* 原来这里是 backdrop-filter: blur(16px)：吸顶栏每帧对滚动内容做模糊重采样，
     实测把滚动从 58fps / p95 16.7ms 拖到 50fps / p95 33.4ms。改成近实底色（视觉几乎无差） */
  background: color-mix(in oklab, var(--sa-bg) 97%, transparent);
}
@media (min-width: 1100px) {
  .gp-rail {
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
  }
}
.gp-rail-focus {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
}
.gp-rail-focus strong {
  color: var(--sa-text-primary);
}
.gp-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--sa-accent);
}
.gp-rail-scroll {
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.gp-rail-scroll::-webkit-scrollbar {
  display: none;
}
.gp-rail-track {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 999px;
  background: var(--sa-subtle);
}
.gp-rail-pill {
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 4px;
  border-radius: 999px;
  background: var(--sa-text-primary);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
  transition: transform 0.5s var(--spring), width 0.5s var(--spring);
  pointer-events: none;
  z-index: 0;
}
.gp-rail-btn {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 7px 14px;
  border: none;
  border-radius: 999px;
  background: none;
  color: var(--sa-text-secondary);
  font-family: inherit;
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
  transition: color 0.3s ease;
}
.gp-rail-btn:hover {
  color: var(--sa-text-primary);
}
.gp-rail-btn--active {
  color: var(--sa-bg);
  font-weight: 700;
}
.gp-rail-btn--active:hover {
  color: var(--sa-bg);
}
.gp-rail-count {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  font-style: normal;
  opacity: 0.6;
}

/* ================= 作品区：暗房追光 ================= */
.gp-works {
  padding: clamp(20px, 3vw, 34px) var(--gp-gutter) 80px;
}
.gp-works-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}
.gp-works-title {
  font-size: 11px;
  letter-spacing: 0.18em;
  color: var(--sa-text-tertiary);
}
.gp-works-title em {
  font-style: normal;
  letter-spacing: 0.02em;
  opacity: 0.7;
}
.gp-works-count {
  font-size: 11px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--sa-text-tertiary);
}

.gp-darkroom {
  display: grid;
  grid-template-columns: repeat(1, minmax(0, 1fr));
  /* 卡片不再有 8px 内距，行列间距补回来，否则封面会贴在一起 */
  gap: 22px 18px;
  padding: 4px 0;
}
@media (min-width: 640px) {
  .gp-darkroom {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (min-width: 1024px) {
  .gp-darkroom {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (min-width: 1360px) {
  .gp-darkroom {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
@media (min-width: 1760px) {
  .gp-darkroom {
    grid-template-columns: repeat(5, minmax(0, 1fr));
  }
}
@media (min-width: 2300px) {
  .gp-darkroom {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }
}

/* 业主 2026-11-13：取消「滑到某张卡，其余压暗去色」的追光效果。
   触屏上 :hover 会粘住，一碰就整片变灰，观感很脏。 */
.gp-item {
  position: relative;
  cursor: pointer;
  animation: gpCascade 0.6s var(--spring) both;
  /* 800+ 张卡时，离屏卡不再参与布局/绘制/合成（滚动 fps 与首屏长任务的主要改善点） */
  content-visibility: auto;
  contain-intrinsic-size: auto 260px;
  transition:
    opacity 0.45s var(--spring),
    filter 0.45s var(--spring),
    transform 0.5s var(--spring);
}
.gp-darkroom .gp-item {
  opacity: 1;
  filter: none;
}
.gp-darkroom .gp-item:hover {
  opacity: 1;
  filter: none;
  z-index: 5;
}
/* 分片渲染的哨兵：不可见的 1px 占位，滚到它附近就追加下一批（不改变视觉） */
.gp-sentinel {
  height: 1px;
}
/* 首批之后追加的卡片：不跑入场动画，直接呈现 */
.gp-item--static {
  animation: none;
  opacity: 1;
}

@keyframes gpCascade {
  from {
    opacity: 0;
    transform: translateY(22px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

.gp-card {
  position: relative;
  aspect-ratio: 16 / 10;
  border-radius: 24px;
  overflow: hidden;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  box-shadow: 0 18px 40px -22px rgba(0, 0, 0, 0.45);
  transition: border-color 0.35s ease, box-shadow 0.55s var(--spring);
}
.gp-item--short .gp-card {
  aspect-ratio: 9 / 16;
}
.gp-item:hover .gp-card {
  border-color: var(--sa-border);
  box-shadow: 0 30px 60px -18px rgba(0, 0, 0, 0.6);
}
.gp-card-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.9s var(--spring);
}
.gp-item:hover .gp-card-img {
  transform: scale(1.05);
}
.gp-card-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--sa-text-tertiary);
}
.gp-card-shade {
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(8, 8, 11, 0.78) 0%, rgba(8, 8, 11, 0.05) 52%, transparent 100%);
  pointer-events: none;
}
.gp-card-tag,
.gp-card-badge {
  position: absolute;
  top: 12px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  white-space: nowrap;
}
.gp-card-tag {
  left: 12px;
  color: #08080b;
  background: rgba(255, 255, 255, 0.94);
}
.gp-card-badge {
  right: 12px;
  color: #fff;
  background: color-mix(in oklab, var(--sa-accent) 78%, transparent);
  backdrop-filter: blur(8px);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.gp-card-spec {
  position: absolute;
  inset-inline: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  font-size: 10.5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: #f4f4f6;
  background: rgba(17, 18, 26, 0.94);
  border-top: 1px solid rgba(255, 255, 255, 0.14);
  backdrop-filter: blur(14px);
  transform: translateY(102%);
  transition: transform 0.45s var(--spring);
}
.gp-item:hover .gp-card-spec {
  transform: translateY(0);
}
.gp-spec-dot {
  color: #34d399;
}
.gp-spec-go {
  margin-left: auto;
  font-family: inherit;
  font-weight: 700;
  color: #fff;
}
.gp-card-title {
  margin: 12px 2px 0;
  font-family: 'Instrument Serif', 'Noto Serif SC', 'Songti SC', serif;
  font-size: clamp(17px, 1.5vw, 21px);
  line-height: 1.32;
  font-weight: 700;
  color: var(--sa-text-primary);
  transition: color 0.3s ease;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.gp-item:hover .gp-card-title {
  color: var(--sa-accent);
}
.gp-card-meta {
  margin: 6px 2px 0;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ================= 空态 / 错误 ================= */
.gp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 52vh;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}
.gp-empty--inline {
  min-height: 320px;
}
.gp-empty p {
  margin: 0;
}
.gp-empty-btn {
  padding: 8px 18px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 999px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}
.gp-empty-btn:hover {
  border-color: var(--sa-border);
}
.gp-error {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 16px;
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  font-size: 12px;
}

.gp-m-info,
.gp-m-brand {
  display: none;
}
/* 桌面端脚注有活动名，移动端换成短日期 */
.gc-res--short {
  display: none;
}

/* ============================================================
   移动端（≤768px）：按复刻稿重排
   60svh 暗调大图 → 下划线 Tab（简介 / 成员胶囊）→ 灰底胶囊分类 → 双列卡片
   ============================================================ */
@media (max-width: 768px) {
  /* ---- 首屏 ---- */
  .gp-hero {
    height: 66svh;
    min-height: 500px;
    justify-content: flex-end;
    gap: 0;
    padding-bottom: 16px;
  }
  /* 桌面端的居中大字 / 成员名册在移动端让位给 gp-m-info */
  .gp-hero-center,
  .gp-roster {
    display: none;
  }

  .gp-m-info {
    position: relative;
    z-index: 2;
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: 100%;
    min-width: 0;
  }
  /* 刊头烙印：移动端全站没有顶栏，这里补一枚与首页焦点海报同款的品牌字。
     hero 用负 margin 通顶（-顶栏高度），所以这里要把顶栏高度加回来，否则落到视口外。 */
  .gp-m-brand {
    position: absolute;
    top: calc(var(--sa-header-height, 77px) + 16px);
    left: var(--gp-gutter);
    z-index: 2;
    display: block;
    font-family: 'Playfair Display', 'Noto Serif SC', serif;
    font-size: 16px;
    font-weight: 900;
    letter-spacing: -0.02em;
    color: #fff;
    text-shadow: 0 2px 18px rgba(0, 0, 0, 0.45);
    pointer-events: none;
  }
  .gp-m-kicker {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 11px;
    color: rgba(255, 255, 255, 0.8);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .gp-m-dot {
    flex: 0 0 auto;
    width: 6px;
    height: 6px;
    border-radius: 999px;
    background: #34d399;
    box-shadow: 0 0 0 3px rgba(52, 211, 153, 0.18);
  }
  .gp-m-name {
    margin: 0;
    font-family: 'Playfair Display', 'Noto Serif SC', 'Songti SC', serif;
    font-size: 36px;
    font-weight: 900;
    line-height: 1;
    letter-spacing: -0.02em;
    color: #fff;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* ---- 下划线 Tab ---- */
  .gp-m-tabs {
    display: flex;
    gap: 20px;
    padding-top: 2px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.2);
  }
  .gp-m-tab {
    padding: 0 0 6px;
    border: none;
    border-bottom: 2px solid transparent;
    background: none;
    color: rgba(255, 255, 255, 0.5);
    font-family: inherit;
    font-size: 12px;
    font-weight: 500;
    line-height: 1.2;
    cursor: pointer;
    transition: color 0.2s ease, border-color 0.2s ease;
  }
  .gp-m-tab--on {
    border-bottom-color: #fff;
    color: #fff;
    font-weight: 700;
  }

  /* ---- ABOUT 简介 ---- */
  .gp-m-intro {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .gp-m-desc {
    margin: 0;
    padding-left: 10px;
    border-left: 2px solid rgba(255, 255, 255, 0.3);
    font-size: 11px;
    line-height: 1.75;
    color: rgba(255, 255, 255, 0.85);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .gp-m-meta-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding-left: 10px;
  }
  .gp-m-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 10px;
    color: rgba(255, 255, 255, 0.6);
    white-space: nowrap;
  }
  .gp-m-sep {
    opacity: 0.6;
  }
  .gp-m-expand {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 0;
    border: none;
    background: none;
    color: rgba(255, 255, 255, 0.85);
    font-family: inherit;
    font-size: 11px;
    font-weight: 500;
    white-space: nowrap;
    cursor: pointer;
  }
  .gp-m-caret {
    font-size: 13px;
    line-height: 1;
  }

  /* ---- MEMBERS 单行胶囊 ---- */
  .gp-m-caps {
    /* 胶囊高度跟着屏宽走；成员面板与简介面板等高后（62px）留了不少纵向余量，
       这里把它用掉——比原来 22px 头像大一圈，但仍能一屏看到 2~3 个。 */
    --gp-cap-h: clamp(38px, 11.2vw, 48px);
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 0;
    overflow-x: auto;
    scrollbar-width: none;
    -webkit-mask-image: linear-gradient(to right, transparent 0%, #000 4%, #000 93%, transparent 100%);
    mask-image: linear-gradient(to right, transparent 0%, #000 4%, #000 93%, transparent 100%);
  }
  .gp-m-caps::-webkit-scrollbar {
    display: none;
  }
  .gp-m-cap {
    display: inline-flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 7px;
    min-height: var(--gp-cap-h);
    padding: 2px 13px 2px 3px;
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.1);
    cursor: pointer;
    transition: background 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
  }
  .gp-m-cap:active {
    transform: scale(0.96);
  }
  .gp-m-cap--on {
    border-color: rgba(255, 255, 255, 0.42);
    background: rgba(255, 255, 255, 0.26);
  }
  .gp-m-cap-img {
    flex: 0 0 auto;
    width: calc(var(--gp-cap-h) - 10px);
    height: calc(var(--gp-cap-h) - 10px);
    border-radius: 999px;
    object-fit: cover;
    background: rgba(255, 255, 255, 0.16);
  }
  .gp-m-cap-img--ph {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    color: rgba(255, 255, 255, 0.8);
  }
  .gp-m-cap-en {
    font-family: 'Instrument Serif', Georgia, 'Times New Roman', serif;
    font-style: italic;
    font-size: clamp(15px, 4.6vw, 18px);
    line-height: 1;
    color: #fff;
  }
  .gp-m-cap-cn {
    font-size: clamp(10.5px, 3vw, 12px);
    line-height: 1;
    color: rgba(255, 255, 255, 0.5);
  }

  /* ---- 分类胶囊：贴页面底色，去掉桌面端的上下描边 ---- */
  .gp-rail {
    padding: 10px 14px 2px;
    border-top: none;
    border-bottom: none;
    background: var(--sa-bg);
  }
  /* 灰底胶囊框放在「滚动容器」上：滑块/按钮怎么滚，外框都完整不动。
     以前把外框放在 .gp-rail-track 上，而 track 只有一屏宽（width:100%），
     滑到后面的按钮时背景就断在半路了。 */
  .gp-rail-scroll {
    padding: 4px;
    border: none;
    border-radius: 999px;
    background: var(--sa-subtle);
  }
  .gp-rail-track {
    width: max-content;
    padding: 0;
    border: none;
    background: transparent;
  }
  /* track 没有 padding 了，滑块贴边对齐按钮本身 */
  .gp-rail-pill {
    top: 0;
    bottom: 0;
    left: 0;
  }
  .gp-rail-btn {
    /* 取消移动端的双击缩放延迟与点击高亮 */
    touch-action: manipulation;
    -webkit-tap-highlight-color: transparent;
  }

  /* ---- 作品区：双列白卡 ---- */
  .gp-works {
    padding: 12px 14px 64px;
  }
  .gp-darkroom {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 18px 12px;
    padding: 2px 0 0;
  }
  /* 复刻稿脚注没有分隔线，信息压得更紧 */
  .gp-item .gc-title {
    font-size: 11px;
    font-weight: 700;
    line-height: 1.35;
  }
  .gp-item .gc-foot {
    margin-top: 6px;
    padding-top: 0;
    border-top: none;
    gap: 6px;
  }
  /* 类型标签不再被压缩（曾把「粉丝直拍」挤成一个字） */
  .gp-item .gc-type {
    flex: 0 0 auto;
    max-width: 46%;
    font-size: 9px;
    padding: 1px 5px;
    border: none;
  }
  .gp-item .gc-nums {
    flex: 1 1 auto;
    min-width: 0;
    gap: 4px;
    font-size: 9px;
  }
  .gp-item .gc-res {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 9px;
    font-weight: 500;
  }
  .gp-item .gc-res--full {
    display: none;
  }
  .gp-item .gc-res--short {
    display: inline;
  }
}

/* ============================================================
   移动端：简介/成员等高等 + 毛玻璃简介弹窗
   ============================================================ */
@media (max-width: 768px) {
  /* 两个面板叠在同一格：容器高度 = 两者较高者（简介 2 行 + meta 行），
     这样切到成员 tab 时 hero 不会突然缩一截。 */
  .gp-m-stack {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    min-width: 0;
  }
  .gp-m-panel {
    grid-area: 1 / 1;
    min-width: 0;
  }
  .gp-m-panel.is-off {
    visibility: hidden;
    pointer-events: none;
  }
  /* 成员面板内容少，纵向居中，避免贴着 tab 显得空 */
  .gp-m-members {
    display: flex;
    flex-direction: column;
    justify-content: center;
  }

  /* ---- 简介弹窗：毛玻璃 ---- */
  .gp-bio-mask {
    position: fixed;
    inset: 0;
    z-index: 215;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    background: rgba(8, 10, 14, 0.5);
    -webkit-backdrop-filter: blur(18px) saturate(1.1);
    backdrop-filter: blur(18px) saturate(1.1);
  }
  .gp-bio-card {
    display: flex;
    flex-direction: column;
    width: min(92vw, 430px);
    max-height: 78svh;
    padding: 18px 18px 14px;
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 22px;
    background: rgba(22, 26, 34, 0.72);
    -webkit-backdrop-filter: blur(28px) saturate(1.25);
    backdrop-filter: blur(28px) saturate(1.25);
    box-shadow:
      0 28px 70px rgba(0, 0, 0, 0.55),
      inset 0 1px 0 rgba(255, 255, 255, 0.12);
    color: #fff;
  }
  .gp-bio-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .gp-bio-kicker {
    font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 10px;
    letter-spacing: 0.18em;
    color: rgba(255, 255, 255, 0.5);
  }
  .gp-bio-close {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.1);
    color: rgba(255, 255, 255, 0.85);
    font-family: inherit;
    font-size: 12px;
    line-height: 1;
    cursor: pointer;
  }
  .gp-bio-close:active {
    background: rgba(255, 255, 255, 0.22);
  }
  .gp-bio-title {
    margin: 10px 0 8px;
    font-family: 'Playfair Display', 'Noto Serif SC', serif;
    font-size: 22px;
    font-weight: 900;
    line-height: 1.15;
    letter-spacing: -0.01em;
  }
  .gp-bio-body {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    -webkit-overflow-scrolling: touch;
    padding-right: 2px;
    font-size: 12.5px;
    line-height: 1.85;
    color: rgba(255, 255, 255, 0.82);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .gp-bio-foot {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid rgba(255, 255, 255, 0.12);
    font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 10px;
    color: rgba(255, 255, 255, 0.45);
  }
  .gp-bio-sep {
    opacity: 0.6;
  }

  .gp-bio-pop-enter-active,
  .gp-bio-pop-leave-active {
    transition: opacity 0.22s ease;
  }
  .gp-bio-pop-enter-active .gp-bio-card,
  .gp-bio-pop-leave-active .gp-bio-card {
    transition: transform 0.28s var(--spring, cubic-bezier(0.16, 1, 0.3, 1));
  }
  .gp-bio-pop-enter-from,
  .gp-bio-pop-leave-to {
    opacity: 0;
  }
  .gp-bio-pop-enter-from .gp-bio-card,
  .gp-bio-pop-leave-to .gp-bio-card {
    transform: translateY(10px) scale(0.96);
  }
}
</style>
