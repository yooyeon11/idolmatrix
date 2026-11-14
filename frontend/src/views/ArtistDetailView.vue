<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NSpin } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import VideoCard from '@/components/VideoCard.vue'
import VideoFilterBar from '@/components/VideoFilterBar.vue'
import PhotoGallery from '@/components/PhotoGallery.vue'
import { artistsApi } from '@/api/artists'
import { membershipsApi } from '@/api/memberships'
import { musicVideosApi } from '@/api/musicVideos'
import type { Artist, MembershipWithGroup, MusicVideo } from '@/types/models'
import { VIDEO_TYPE_LABEL } from '@/types/models'
import { formatDate } from '@/utils/format'
import { formatPositions } from '@/utils/positions'
import { artistPath, fetchByRouteParam, groupPath, videoPath } from '@/utils/routes'
import { MV_TYPES, LIVE_TYPES, videoHasType } from '@/utils/videoTypes'
import { useGridDensity } from '@/composables/useGridDensity'
import { useEntityDetail, ENTITY_TABS } from '@/composables/useEntityDetail'
import { useDetailPageTheme } from '@/composables/useDetailPageTheme'
import { heroImageWidth, IMG_W_CARD } from '@/utils/imageSizes'
import { MovieOutlined } from '@/components/icons'
import StageHero from '@/components/stage/StageHero.vue'
import StageRail from '@/components/stage/StageRail.vue'

const router = useRouter()
/* 默认落在「全部作品」：艺人页不再有概览分区（业主 2026-11-14：
   下半部分与组合详情页完全一致 —— 刊头之后直接进作品区） */
const activeTab = ref('all')
// listAll 全量加载、无分页：密度切换只改变网格列数
const { density } = useGridDensity()

const memberships = ref<MembershipWithGroup[]>([])
const portraitStamp = ref(Date.now())

// ===== 移动端设计稿（2026-11-13）：520px 刊头 / 双 Tab / 维度胶囊 =====
/** 刊头内的双 Tab：ABOUT 简介、AFFILIATIONS 所属组合 */
const mobileHeroTab = ref<'about' | 'affiliations'>('about')
const mobileHeroPill = computed(() =>
  [artist.value?.korean_name, heroGroupName.value].filter(Boolean).join(' · '),
)
const mobileStageName = computed(() => artist.value?.stage_name || artist.value?.name || '')
/** 所属组合：现役（母队在前、小分队在后）+ 历史组合，带头像与名字（组合页成员格同款形态） */
interface MobileAffil {
  uid: string
  id: number
  name: string
  avatar: string | null
  updated: string | null
  badge: string
  former: boolean
  active: boolean
}
const mobileAffiliations = computed<MobileAffil[]>(() => {
  const rows = memberships.value.map((m) => {
    const isRoot = !m.root_group_uid || m.root_group_uid === m.group_uid
    const former = m.status === 'Former'
    return {
      uid: m.group_uid || '',
      id: m.group_id,
      name: m.group_chinese_name || m.group_name || '',
      avatar: m.group_avatar_path || null,
      updated: m.group_updated_at || null,
      badge: former ? '历史' : isRoot ? '母队' : '小分队',
      former,
      active: !former,
    }
  })
  const active = rows.filter((r) => r.active)
  const former = rows.filter((r) => r.former)
  active.sort((a, b) => (a.badge === '母队' ? -1 : 0) - (b.badge === '母队' ? -1 : 0))
  return [...active, ...former]
})
function mobileAffilAvatarUrl(g: { id: number; updated: string | null }) {
  return `/api/groups/${g.id}/avatar?w=96&v=${encodeURIComponent(g.updated || '')}`
}
/** 与组合详情页同源的维度胶囊计数（去掉「概览」） */
/** 桌面维度栏：与移动端 pills、组合页一样不含「概览」 */
const deskTabs = computed(() => ENTITY_TABS.filter((t) => t.key !== 'overview'))

const mobileDims = computed(() => {
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
        return null
    }
  }
  return ENTITY_TABS.filter((t) => t.key !== 'overview').map((t) => ({
    key: t.key,
    label: t.label,
    count: countOf(t.key),
  }))
})

/** 移动端作品网格：直接复用组合详情页的卡片口径（封面 / 类型 / 场次·日期） */
const brokenMobileThumbs = ref<Set<number>>(new Set())
function mobileThumb(v: MusicVideo): string {
  if (brokenMobileThumbs.value.has(v.id)) return ''
  return musicVideosApi.thumbnailUrl(v.id, v.file_hash || v.file_size, IMG_W_CARD)
}
function onMobileThumbError(id: number) {
  brokenMobileThumbs.value = new Set(brokenMobileThumbs.value).add(id)
}
function mobileTag(v: MusicVideo): string {
  return VIDEO_TYPE_LABEL[v.video_type] || v.video_type || '作品'
}
function mobileMeta(v: MusicVideo): string {
  return [v.event_name, formatDate(v.performance_date || v.published_date)]
    .filter((x) => x && x !== '--')
    .join(' · ')
}

const {
  entity: artist,
  loading,
  notFound,
  videosError,
  retryVideos,
  longVideos,
  shortVideos,
  activeVideos,
  videoSongId,
  videoSearchText,
  videoSortKey,
  videoSongOptions,
  displayedVideos,
  hasVideoFilters,
  resetVideoFilters,
  isPhotoTab,
  photoSection,
} = useEntityDetail<Artist>(
  {
    fetchEntity: (param) => fetchByRouteParam(param, artistsApi.getByUid, artistsApi.get),
    entityPath: artistPath,
    fetchVideos: (a) => musicVideosApi.listAll({ artist_id: a.id, ingestion_status: 'library' }),
    fetchExtras: async (a, isCurrent) => {
      const ms = await membershipsApi.byArtist(a.id).catch(() => [] as MembershipWithGroup[])
      if (isCurrent()) memberships.value = ms
    },
  },
  activeTab,
)

/** 大字上方第一行（对齐组合页 kicker）：艺名 · 韩文名 · 所属组合 */
const heroKicker = computed(() => {
  const a = artist.value
  if (!a) return ''
  const parts: string[] = []
  for (const v of [a.stage_name, a.korean_name]) {
    if (v && !parts.includes(v)) parts.push(v)
  }
  const m = activeMembership.value
  const group =
    m?.root_group_chinese_name ||
    m?.root_group_name ||
    m?.group_chinese_name ||
    m?.group_name ||
    ''
  if (group && !parts.includes(group)) parts.push(group)
  return parts.join(' · ')
})

/** 大字上方第二行：加入日期 · 担当（不再重复作品数） */
const heroEyebrow = computed(() => heroGroupDetail.value)

const heroName = computed(() =>
  artist.value?.chinese_name ||
  artist.value?.stage_name ||
  artist.value?.korean_name ||
  artist.value?.name ||
  '',
)
const heroAvatarSrc = computed(() =>
  artist.value?.avatar_path
    ? `${artistsApi.avatarUrl(artist.value.id)}?t=${portraitStamp.value}`
    : '',
)
const heroBannerSrc = computed(() =>
  artist.value?.banner_path
    ? `${artistsApi.bannerUrl(artist.value.id)}?t=${portraitStamp.value}`
    : '',
)
const { pageStyle } = useDetailPageTheme({
  entityId: () => artist.value?.id ?? 0,
  hasAvatar: () => !!artist.value?.avatar_path,
  hasBanner: () => !!artist.value?.banner_path,
  avatarSrc: () => heroAvatarSrc.value,
  bannerSrc: () => heroBannerSrc.value,
})
/**
 * PC 整页气氛层取图：横幅优先、无横幅回退封面。
 * 轻模糊铺满视口顶部，统一取 480px 变体即可。
 */
const heroBgSrc = computed(() => {
  const a = artist.value
  if (!a) return ''
  const base = a.banner_path
    ? artistsApi.bannerUrl(a.id)
    : a.avatar_path
      ? artistsApi.avatarUrl(a.id)
      : ''
  return base ? `${base}?w=${heroImageWidth()}&t=${portraitStamp.value}` : ''
})
const activeMembership = computed(
  () => memberships.value.find((x) => x.status === 'Active') || memberships.value[0] || null,
)
const heroGroupName = computed(() => {
  const m = activeMembership.value
  if (!m) return ''
  // 优先显示母队（完整体型），小分队自动溯源到 top-level 母队
  return (
    m.root_group_chinese_name ||
    m.root_group_name ||
    m.group_chinese_name ||
    m.group_name ||
    ''
  )
})
const heroGroupUid = computed(
  () => activeMembership.value?.root_group_uid || activeMembership.value?.group_uid || '',
)
const heroGroupDetail = computed(() => {
  const m = activeMembership.value
  if (!m) return ''
  const parts: string[] = []
  if (m.join_date) parts.push(`${m.join_date} 加入`)
  if (m.positions?.length) parts.push(formatPositions(m.positions))
  if (m.status === 'Former') parts.push('已退出')
  return parts.join(' · ')
})

const worksMeta = computed(() => `${longVideos.value.length}部作品`)

const bio = computed(() => (artist.value?.description || '').replace(/\{BR\}/g, '\n'))

// ===== 资料速览侧滑卡（只新增，不改动页面其他区域） =====
const dossierOpen = ref(false)
const dossierLatin = computed(
  () =>
    artist.value?.english_name ||
    artist.value?.stage_name ||
    artist.value?.name ||
    '',
)
function onDossierKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && dossierOpen.value) dossierOpen.value = false
}

/** 档案式日期：2022.07.15 */
function dotDate(v?: string | null): string {
  const s = (v || '').slice(0, 10)
  return s ? s.replace(/-/g, '.') : ''
}

/** 月/日分界 → 该月所属星座；未到分界日算上一个星座 */
const ZODIAC_CUTS: [number, number, string][] = [
  [1, 20, '水瓶座'],
  [2, 19, '双鱼座'],
  [3, 21, '白羊座'],
  [4, 20, '金牛座'],
  [5, 21, '双子座'],
  [6, 22, '巨蟹座'],
  [7, 23, '狮子座'],
  [8, 23, '处女座'],
  [9, 23, '天秤座'],
  [10, 24, '天蝎座'],
  [11, 23, '射手座'],
  [12, 22, '摩羯座'],
]
function zodiacOf(v?: string | null): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v || '')
  if (!m) return ''
  const month = Number(m[2])
  const day = Number(m[3])
  if (!month || !day) return ''
  const cut = ZODIAC_CUTS[month - 1]
  return day < cut[1] ? ZODIAC_CUTS[(month + 10) % 12][2] : cut[2]
}

interface DossierCell {
  key: string
  val: string
  wide?: boolean
  hl?: boolean
}

const dossierCells = computed<DossierCell[]>(() => {
  const a = artist.value
  if (!a) return []
  const cells: DossierCell[] = []
  const birth = dotDate(a.birth_date)
  if (birth) cells.push({ key: '出生日期', val: birth })
  const zodiac = zodiacOf(a.birth_date)
  if (zodiac) cells.push({ key: '星座', val: zodiac })
  const debut = dotDate(a.debut_date)
  if (debut) cells.push({ key: '出道日期', val: debut })
  if (a.occupation) cells.push({ key: '职业', val: a.occupation })
  if (a.birth_place) cells.push({ key: '出生地', val: a.birth_place, wide: true })
  return cells
})

/** 队内担当：拆成 chips，避免「队长 / 主rapper / 领舞 / 门面」挤成一串。
 *  多组合/小分队时优先取**母队**那条的担当（activeMembership 只取第一条 Active，可能是小分队）。 */
const dossierPositions = computed<string[]>(() => {
  const root = memberships.value.find((m) => !m.root_group_uid && m.positions?.length)
  if (root?.positions?.length) return root.positions
  for (const m of memberships.value) {
    if (m.positions?.length) return m.positions
  }
  return []
})

interface DossierExperience {
  name: string
  date: string
  /** true = 母队/主体组合；false = 小分队/企划（缩进展示） */
  root: boolean
}

/** 艺人经历：**一条一行**（真实数据里可有 8 条组合/企划，挤一行会糊成一片） */
const dossierExperience = computed<DossierExperience[]>(() => {
  const seen = new Set<string>()
  const rows: { name: string; date: string; root: boolean; sort: string }[] = []
  for (const m of memberships.value) {
    const name = m.group_chinese_name || m.group_name || ''
    if (!name || seen.has(name)) continue
    seen.add(name)
    rows.push({
      name,
      date: dotDate(m.join_date),
      root: !m.root_group_uid,
      sort: m.join_date || '9999-99-99',
    })
  }
  rows.sort((a, b) => a.sort.localeCompare(b.sort))
  return rows.map(({ name, date, root }) => ({ name, date, root }))
})

/** 母队加入日期（资料卡「所属团体」磁贴副标题） */
const rootGroupJoin = computed(() => {
  const root = memberships.value.find((m) => !m.root_group_uid && m.join_date)
  return root ? dotDate(root.join_date) : ''
})

// ===== 手势侧滑：右缘左滑打开 / 面板右滑关闭（触屏跟手，鼠标按位移判定） =====
const swipeP = ref(1) // 1 = 完全打开，0 = 完全收起
const swiping = ref(false)
let swipeMode: 'open' | 'close' | null = null
let swipeStartX = 0
let swipeStartY = 0
let swipeWidth = 460

const drawerStyle = computed(() =>
  swiping.value ? { transform: `translateX(${(1 - swipeP.value) * 100}%)`, transition: 'none' } : {},
)

function drawerWidth(): number {
  const el = document.querySelector('.ds-drawer')
  return el ? el.getBoundingClientRect().width : 460
}

function onTouchStart(e: TouchEvent) {
  const t = e.touches[0]
  if (!t) return
  swipeWidth = drawerWidth() || 460
  if (!dossierOpen.value && t.clientX >= window.innerWidth - 26) {
    swipeMode = 'open'
    swipeP.value = 0
  } else if (dossierOpen.value && t.clientX >= window.innerWidth - swipeWidth) {
    swipeMode = 'close'
    swipeP.value = 1
  } else {
    swipeMode = null
    return
  }
  swipeStartX = t.clientX
  swipeStartY = t.clientY
  swiping.value = true
}

function onTouchMove(e: TouchEvent) {
  if (!swipeMode) return
  const t = e.touches[0]
  if (!t) return
  const dx = t.clientX - swipeStartX
  const dy = t.clientY - swipeStartY
  // 竖滑优先：判定为纵向滚动就放弃手势，交还给页面
  if (Math.abs(dy) > Math.abs(dx) * 1.4) {
    swipeMode = null
    swiping.value = false
    swipeP.value = dossierOpen.value ? 1 : 0
    return
  }
  e.preventDefault()
  const base = swipeMode === 'open' ? 0 : 1
  swipeP.value = Math.min(1, Math.max(0, base - dx / swipeWidth))
}

function onTouchEnd() {
  if (!swipeMode) return
  dossierOpen.value = swipeMode === 'open' ? swipeP.value > 0.28 : swipeP.value > 0.72
  swipeMode = null
  swiping.value = false
}

// 鼠标从右缘往左拖：位移 > 60px 视为打开（与参考稿一致）
let mouseDownX = 0
let mouseFromEdge = false
function onMouseDown(e: MouseEvent) {
  mouseDownX = e.clientX
  mouseFromEdge = !dossierOpen.value && e.clientX >= window.innerWidth - 40
}
function onMouseUp(e: MouseEvent) {
  if (!mouseFromEdge) return
  mouseFromEdge = false
  if (e.clientX - mouseDownX < -60) dossierOpen.value = true
}

// 触控板横向滑动：向右扫关闭
function onWheel(e: WheelEvent) {
  if (dossierOpen.value && e.deltaX < -40 && Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
    dossierOpen.value = false
  }
}

onMounted(() => {
  window.addEventListener('keydown', onDossierKey)
  window.addEventListener('touchstart', onTouchStart, { passive: true })
  window.addEventListener('touchmove', onTouchMove, { passive: false })
  window.addEventListener('touchend', onTouchEnd)
  window.addEventListener('touchcancel', onTouchEnd)
  window.addEventListener('mousedown', onMouseDown)
  window.addEventListener('mouseup', onMouseUp)
  window.addEventListener('wheel', onWheel, { passive: true })
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onDossierKey)
  window.removeEventListener('touchstart', onTouchStart)
  window.removeEventListener('touchmove', onTouchMove)
  window.removeEventListener('touchend', onTouchEnd)
  window.removeEventListener('touchcancel', onTouchEnd)
  window.removeEventListener('mousedown', onMouseDown)
  window.removeEventListener('mouseup', onMouseUp)
  window.removeEventListener('wheel', onWheel)
})

function goVideo(uid: string) {
  router.push(videoPath(uid))
}

function goGroup(uid?: string | null) {
  if (!uid) return
  router.push(groupPath(uid))
}

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/')
}

async function onPortraitApplied() {
  if (!artist.value) return
  try {
    artist.value = await artistsApi.get(artist.value.id)
    portraitStamp.value = Date.now()
  } catch {
    /* 裁切已成功，刷新失败时下次进入页面会看到 */
  }
}
</script>

<template>
  <div
    class="mv-page"
    :class="{ 'mv-page--atmosphere': !!artist }"
    :style="pageStyle"
  >
    <SaHeader v-if="!artist" />
    <SaHeader v-else overlay />


    <!-- 通顶刊头：与组合详情页同一套 StageHero（遮罩 / 大字 / 斜体拉丁名） -->
    <StageHero
      v-if="artist"
      :image="heroBgSrc"
      :name="heroName"
      :kicker="heroKicker"
      :eyebrow="heroEyebrow"
      :description="bio"
    >
      <template #actions>
        <!-- 方案一（画报级抽屉）：左「查看组合」描边胶囊，右「资料速览」发光胶囊 -->
        <button
          v-if="heroGroupUid"
          class="stage-cta stage-cta--outline"
          type="button"
          @click="goGroup(heroGroupUid)"
        >
          查看{{ heroGroupName ? ` ${heroGroupName}` : '所属组合' }}
        </button>
        <button class="stage-cta stage-cta--glow" type="button" @click="dossierOpen = true">
          <span class="stage-cta-glyph" aria-hidden="true">ⓘ</span>
          <span>资料速览</span>
        </button>
      </template>

      <!-- 移动端：设计稿的 520px 刊头（桌面不渲染） -->
      <template #mobile>
        <div class="a-m-pill">
          <i class="a-m-dot" aria-hidden="true" />
          <span>{{ mobileHeroPill || 'ARTIST' }}</span>
        </div>
        <h1 class="a-m-title">
          {{ mobileStageName }}
          <span class="a-m-sub">{{ heroName }}</span>
        </h1>

        <div class="a-m-tabs">
          <button
            type="button"
            :class="{ 'a-m-tab--on': mobileHeroTab === 'about' }"
            @click="mobileHeroTab = 'about'"
          >ABOUT 艺人档案</button>
          <button
            type="button"
            :class="{ 'a-m-tab--on': mobileHeroTab === 'affiliations' }"
            @click="mobileHeroTab = 'affiliations'"
          >AFFILIATIONS 所属组合 ({{ mobileAffiliations.length }})</button>
        </div>

        <p v-if="mobileHeroTab === 'about'" class="a-m-intro">{{ bio || '暂无简介' }}</p>
        <!-- 所属组合：与组合详情页「组合内艺人」同款（头像 + 名字），母队 / 小分队 / 历史依次排列 -->
        <div v-else class="a-m-affil">
          <button
            v-for="g in mobileAffiliations"
            :key="`affil-${g.uid || g.id}`"
            type="button"
            class="a-m-affil-card"
            :class="{ 'a-m-affil-card--former': g.former }"
            @click="g.uid && goGroup(g.uid)"
          >
            <span class="a-m-affil-avatar">
              <img v-if="g.avatar" :src="mobileAffilAvatarUrl(g)" alt="" loading="lazy" />
              <template v-else>{{ g.name.slice(0, 1) }}</template>
            </span>
            <span class="a-m-affil-name">{{ g.name }}</span>
            <i class="a-m-affil-badge">{{ g.badge }}</i>
          </button>
          <p v-if="!mobileAffiliations.length" class="a-m-intro">暂无所属组合数据</p>
        </div>

        <div class="a-m-foot">
          <span>{{ heroEyebrow || '艺人档案' }}</span>
          <button type="button" class="a-m-expand" @click="dossierOpen = true">资料速览 ›</button>
        </div>
      </template>
    </StageHero>

    <!-- 吸顶胶囊栏：白色滑块随选中项滑动（无「概览」档，与组合页同构） -->
    <StageRail v-if="artist && !notFound" :items="deskTabs" v-model="activeTab" />

    <!-- 移动端维度胶囊：与组合详情页同源计数（桌面隐藏） -->
    <div v-if="artist && !notFound" class="a-m-pills">
      <button
        v-for="d in mobileDims"
        :key="d.key"
        type="button"
        class="a-m-filter"
        :class="{ 'a-m-filter--on': activeTab === d.key }"
        @click="activeTab = d.key"
      >
        {{ d.label }}<i v-if="d.count != null">{{ d.count }}</i>
      </button>
    </div>
    <div class="mv-container">
      <n-spin :show="loading">
        <div v-if="notFound" class="empty-state">
          <MovieOutlined :size="40" />
          <p>艺人不存在或已删除</p>
          <button class="empty-back" @click="goBack">返回首页</button>
        </div>

        <div v-else-if="artist">
          <div v-if="videosError && !isPhotoTab" class="load-error">
            <span>{{ videosError }}</span>
            <button class="load-error-retry" type="button" @click="retryVideos">重试</button>
          </div>

          <PhotoGallery
            v-else-if="isPhotoTab && artist"
            owner-type="artist"
            :owner-id="artist.id"
            :section="photoSection"
            @portrait-applied="onPortraitApplied"
          />

          <div v-else class="video-section">
            <VideoFilterBar
              v-if="activeVideos.length"
              class="a-desktop-filter"
              :count="displayedVideos.length"
              :songs="videoSongOptions"
              v-model:song-id="videoSongId"
              v-model:sort="videoSortKey"
              v-model:query="videoSearchText"
            />
            <div
              v-if="displayedVideos.length"
              class="video-grid a-desktop-grid"
              :class="[`density-${density}`, { 'video-grid--portrait': activeTab === 'shorts' }]"
            >
              <VideoCard
                v-for="(v, i) in displayedVideos"
                :key="v.id"
                :video="v"
                :index="i"
                :portrait="activeTab === 'shorts'"
              />
            </div>
            <!-- 移动端作品网格：沿用组合详情页的 gallery-card 口径，不用设计稿的横向条卡 -->
            <div v-if="displayedVideos.length" class="a-m-works">
              <article
                v-for="v in displayedVideos"
                :key="`m-${v.id}`"
                class="a-m-work"
                @click="goVideo(v.uid)"
              >
                <div class="a-m-work-cover">
                  <img
                    v-if="mobileThumb(v)"
                    :src="mobileThumb(v)"
                    alt=""
                    loading="lazy"
                    @error="onMobileThumbError(v.id)"
                  />
                  <span v-else class="a-m-work-placeholder"><MovieOutlined :size="26" /></span>
                </div>
                <h3 class="a-m-work-title">{{ v.name }}</h3>
                <div class="a-m-work-foot">
                  <span class="a-m-work-type">{{ mobileTag(v) }}</span>
                  <span class="a-m-work-meta">{{ mobileMeta(v) || '—' }}</span>
                </div>
              </article>
            </div>
            <div v-else-if="activeVideos.length && hasVideoFilters" class="empty-state">
              <MovieOutlined :size="32" />
              <p>没有符合筛选条件的作品</p>
              <button class="empty-back" type="button" @click="resetVideoFilters">清除筛选</button>
            </div>
            <div v-else class="empty-state">
              <MovieOutlined :size="32" />
              <p>该分类下暂无作品</p>
            </div>
          </div>
        </div>
      </n-spin>
    </div>

    <transition name="ds-fade">
      <div
        v-if="dossierOpen || swiping"
        class="ds-scrim"
        :style="swiping ? { opacity: String(swipeP) } : undefined"
        @click="dossierOpen = false"
      />
    </transition>

    <aside
      v-if="artist && !notFound"
      class="ds-drawer"
      :class="{ 'is-open': dossierOpen }"
      :style="drawerStyle"
      aria-label="资料速览"
    >
      <div class="ds-head">
        <button class="ds-close" type="button" title="关闭（Esc）" @click="dossierOpen = false">
          ✕
        </button>
      </div>

      <div class="ds-body">
        <div class="ds-ident">
          <h2 class="ds-name">{{ heroName }}</h2>
          <div class="ds-roman">{{ dossierLatin }}</div>
        </div>

        <div v-if="dossierCells.length || dossierPositions.length" class="ds-metrics">
          <div
            v-for="cell in dossierCells"
            :key="cell.key"
            class="ds-cell"
            :class="{ 'ds-cell--wide': cell.wide }"
          >
            <span class="ds-key">{{ cell.key }}</span>
            <span class="ds-val" :class="{ 'ds-val--hl': cell.hl }">{{ cell.val }}</span>
          </div>
          <div v-if="dossierPositions.length" class="ds-cell ds-cell--wide">
            <span class="ds-key">队内担当</span>
            <span class="ds-tags">
              <span v-for="pos in dossierPositions" :key="pos" class="ds-pill">{{ pos }}</span>
            </span>
          </div>
        </div>

        <!-- 演艺经历：叙事时间线（方案一的 timeline-stream 口径） -->
        <div v-if="dossierExperience.length" class="ds-section">演艺经历</div>
        <div v-if="dossierExperience.length" class="ds-timeline">
          <div v-for="exp in dossierExperience" :key="exp.name" class="ds-node">
            <div class="ds-node-date">
              {{ exp.date || '日期未标注' }}{{ exp.root ? '' : ' · 企划 / 小分队' }}
            </div>
            <div class="ds-node-body">{{ exp.name }}</div>
          </div>
        </div>

        <!-- 所属团体磁贴：直接进组合页 -->
        <template v-if="heroGroupUid">
          <div class="ds-section">所属团体</div>
          <button class="ds-group-card" type="button" @click="goGroup(heroGroupUid)">
            <span class="ds-group-main">
              <span class="ds-group-title">{{ heroGroupName || '所属组合' }}</span>
              <span class="ds-group-sub">
                {{ rootGroupJoin ? `${rootGroupJoin} 加入` : '查看团体主页' }}
              </span>
            </span>
            <span class="ds-group-arrow" aria-hidden="true">›</span>
          </button>
        </template>
      </div>

      <div class="ds-foot">
        <span>IDOLMATRIX DB // ARCHIVE</span>
        <span>{{ worksMeta }}</span>
      </div>
    </aside>
  </div>
</template>

<style scoped src="./entity-detail.css"></style>

<style scoped>
/* 舞台刊头里的动作按钮（slot 内容归属父组件作用域） */
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
/* 刊头动作胶囊（方案一）：左侧描边、右侧发光 */
.stage-cta {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 22px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: #fff;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.2;
  cursor: pointer;
  transition: transform 0.25s ease, background-color 0.25s ease, border-color 0.25s ease,
    box-shadow 0.25s ease;
}
.stage-cta:hover {
  transform: translateY(-2px);
}
.stage-cta--outline {
  background: rgba(255, 255, 255, 0.05);
  -webkit-backdrop-filter: blur(12px);
  backdrop-filter: blur(12px);
  border-color: rgba(255, 255, 255, 0.16);
}
.stage-cta--outline:hover {
  background: rgba(255, 255, 255, 0.12);
  border-color: rgba(255, 255, 255, 0.35);
}
.stage-cta--glow {
  background: linear-gradient(135deg, rgba(37, 99, 235, 0.25) 0%, rgba(96, 165, 250, 0.15) 100%);
  border-color: rgba(96, 165, 250, 0.4);
  color: #93c5fd;
  box-shadow: 0 0 20px rgba(37, 99, 235, 0.2);
}
.stage-cta--glow:hover {
  background: linear-gradient(135deg, rgba(37, 99, 235, 0.4) 0%, rgba(96, 165, 250, 0.3) 100%);
  box-shadow: 0 0 25px rgba(37, 99, 235, 0.35);
}
.stage-cta-glyph {
  font-size: 14px;
  line-height: 1;
  opacity: 0.9;
}
.stage-group-detail {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.6);
}

/* ===== 资料速览：方案一「画报级毛玻璃悬浮抽屉」 =====
   入口在刊头按钮组里，展开后从右侧滑出 440px 玻璃面板；
   参数走「一整块矩阵」（不是一堆小灰框），经历走叙事时间线。 */
.ds-scrim {
  position: fixed;
  inset: 0;
  z-index: 190;
  background: rgba(0, 0, 0, 0.5);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
}
.ds-fade-enter-active,
.ds-fade-leave-active {
  transition: opacity 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}
.ds-fade-enter-from,
.ds-fade-leave-to {
  opacity: 0;
}

.ds-drawer {
  position: fixed;
  top: 0;
  right: 0;
  z-index: 191;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  width: 440px;
  max-width: 90vw;
  height: 100vh;
  height: 100dvh;
  padding: 32px 30px;
  border-left: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(13, 16, 22, 0.78);
  -webkit-backdrop-filter: blur(36px);
  backdrop-filter: blur(36px);
  box-shadow: -15px 0 50px rgba(0, 0, 0, 0.65);
  color: #fff;
  touch-action: pan-y;
  transform: translateX(100%);
  transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1);
  will-change: transform;
}
.ds-drawer.is-open {
  transform: translateX(0);
}

/* 只剩关闭按钮：绝对定位到右上角，不再占一整行（原先是 kicker + 关闭同一行） */
.ds-head {
  position: absolute;
  top: 26px;
  right: 26px;
  z-index: 2;
  display: flex;
  justify-content: flex-end;
}
.ds-close {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.06);
  color: #9ca3ae;
  font-family: inherit;
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  transition: background-color 0.2s ease, color 0.2s ease;
}
.ds-close:hover {
  background: rgba(255, 255, 255, 0.15);
  color: #fff;
}

.ds-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 0;
  overflow-y: auto;
  padding: 22px 0 16px;
  scrollbar-width: none;
}
.ds-body::-webkit-scrollbar {
  display: none;
}

/* 姓名：大衬线 + 罗马副标题 */
.ds-ident {
  padding-bottom: 22px;
}
.ds-name {
  margin: 0;
  padding-right: 40px;
  font-family: 'Playfair Display', 'Noto Serif SC', Georgia, serif;
  font-size: 32px;
  font-weight: 900;
  line-height: 1.1;
  letter-spacing: -0.5px;
  color: #fff;
}
.ds-roman {
  margin-top: 4px;
  font-size: 12px;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: #656b77;
}

/* 参数矩阵：整块一个容器，内部两列（不再每个指标一个小灰框） */
.ds-metrics {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px 18px;
  padding: 18px 20px;
  margin-bottom: 26px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.025);
}
.ds-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}
.ds-cell--wide {
  grid-column: span 2;
}
.ds-key {
  font-size: 11px;
  color: #656b77;
}
.ds-val {
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.45;
  color: #f5f6f8;
  word-break: break-word;
}
.ds-val--hl {
  color: #93c5fd;
}
.ds-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.ds-pill {
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.08);
  font-size: 11px;
  color: #d1d5db;
}

/* 分节标题：左侧 3×12 主题色竖条 */
.ds-section {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 0.5px;
}
.ds-section::before {
  content: '';
  width: 3px;
  height: 12px;
  border-radius: 2px;
  background: #60a5fa;
}

/* 演艺经历：叙事时间线 */
.ds-timeline {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-bottom: 26px;
  padding-left: 20px;
}
.ds-timeline::before {
  content: '';
  position: absolute;
  left: 5px;
  top: 6px;
  bottom: 6px;
  width: 1px;
  background: rgba(255, 255, 255, 0.12);
}
.ds-node {
  position: relative;
}
.ds-node::before {
  content: '';
  position: absolute;
  left: -19px;
  top: 5px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #60a5fa;
  border: 2px solid #0d1016;
}
.ds-node-date {
  margin-bottom: 2px;
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10px;
  letter-spacing: 0.5px;
  color: #60a5fa;
}
.ds-node-body {
  font-size: 12.5px;
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.85);
}

/* 所属团体磁贴 */
.ds-group-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 12px 16px;
  border: 1px solid rgba(96, 165, 250, 0.25);
  border-radius: 12px;
  background: rgba(37, 99, 235, 0.08);
  color: inherit;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.2s ease, transform 0.2s ease;
}
.ds-group-card:hover {
  background: rgba(37, 99, 235, 0.16);
  transform: translateY(-1px);
}
.ds-group-main {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}
.ds-group-title {
  font-size: 13px;
  font-weight: 700;
  color: #fff;
}
.ds-group-sub {
  font-size: 11px;
  color: #656b77;
}
.ds-group-arrow {
  flex: none;
  font-size: 16px;
  line-height: 1;
  color: #60a5fa;
}

.ds-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10.5px;
  color: rgba(255, 255, 255, 0.35);
}

@media (max-width: 768px) {
  .ds-drawer {
    width: 100vw;
    max-width: 100vw;
    padding: 22px 18px 18px;
  }
  .ds-name {
    font-size: 26px;
  }
  .ds-metrics {
    gap: 12px 14px;
    padding: 15px 16px;
    margin-bottom: 22px;
  }
}

/* ================= 移动端设计稿（2026-11-13） ================= */
.a-m-pill,
.a-m-title,
.a-m-tabs,
.a-m-intro,
.a-m-affil,
.a-m-foot,
.a-m-pills {
  display: none;
}

@media (max-width: 768px) {
  .mv-page {
    min-height: 100vh;
    background: var(--sa-bg);
    color: var(--sa-text-primary);
  }
  /* 桌面版吸顶 Tab 与右侧抽屉把手在移动端让位给胶囊栏 / 刊头里的入口 */
  .mv-page :deep(.stage-rail),
  .ds-dock {
    display: none;
  }
  .mv-container {
    padding: 0;
    background: var(--sa-bg);
  }
  .mv-page :deep(.n-spin-container),
  .mv-page :deep(.n-spin-content) {
    background: var(--sa-bg);
  }

  /* --- 520px 刊头内容 --- */
  .a-m-pill {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 6px;
    font-size: 11px;
    color: #d1d5db;
  }
  .a-m-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #10b981;
    box-shadow: 0 0 6px #10b981;
  }
  .a-m-title {
    display: block;
    margin: 0 0 16px;
    font-family: 'Noto Serif SC', Georgia, serif;
    font-size: 40px;
    font-weight: 900;
    line-height: 1.05;
    color: #fff;
  }
  .a-m-sub {
    margin-left: 8px;
    font-family: var(--font-sans, system-ui, sans-serif);
    font-size: 13px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.7);
    vertical-align: 0.25em;
  }
  .a-m-tabs {
    display: flex;
    align-items: center;
    gap: 20px;
    margin-bottom: 12px;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.15);
  }
  .a-m-tabs button {
    position: relative;
    padding: 0;
    border: 0;
    background: transparent;
    color: rgba(255, 255, 255, 0.45);
    font-size: 12px;
    font-weight: 700;
    white-space: nowrap;
    cursor: pointer;
  }
  .a-m-tabs button.a-m-tab--on {
    color: #fff;
  }
  .a-m-tabs button.a-m-tab--on::after {
    content: '';
    position: absolute;
    bottom: -11px;
    left: 0;
    right: 0;
    height: 2px;
    background: #fff;
  }
  .a-m-intro {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    min-height: 38px;
    margin: 0 0 10px;
    font-size: 12px;
    line-height: 1.6;
    color: rgba(255, 255, 255, 0.78);
  }
  /* 所属组合：与组合详情页「成员胶囊」同款 —— 横向药丸，里面是头像 + 名字 */
  .a-m-affil {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 0;
    margin-bottom: 10px;
    overflow-x: auto;
    scrollbar-width: none;
    -webkit-mask-image: linear-gradient(to right, transparent 0%, #000 4%, #000 93%, transparent 100%);
    mask-image: linear-gradient(to right, transparent 0%, #000 4%, #000 93%, transparent 100%);
  }
  .a-m-affil::-webkit-scrollbar {
    display: none;
  }
  .a-m-affil-card {
    display: inline-flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 7px;
    height: 40px;
    padding: 2px 13px 2px 3px;
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.1);
    color: #fff;
    cursor: pointer;
    transition: background 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
  }
  .a-m-affil-card:active {
    transform: scale(0.96);
  }
  .a-m-affil-avatar {
    display: flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 auto;
    width: 34px;
    height: 34px;
    border-radius: 999px;
    overflow: hidden;
    background: rgba(255, 255, 255, 0.16);
    color: rgba(255, 255, 255, 0.85);
    font-size: 14px;
    font-weight: 700;
  }
  .a-m-affil-avatar img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .a-m-affil-name {
    max-width: 132px;
    font-size: 13.5px;
    font-weight: 600;
    color: #fff;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .a-m-affil-badge {
    font-style: normal;
    font-size: 9.5px;
    color: rgba(255, 255, 255, 0.55);
    white-space: nowrap;
  }
  .a-m-affil-card--former .a-m-affil-avatar {
    filter: grayscale(1);
    opacity: 0.65;
  }
  .a-m-affil-card--former .a-m-affil-name {
    color: rgba(255, 255, 255, 0.6);
  }
  .a-m-foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    font-size: 11px;
    color: rgba(255, 255, 255, 0.55);
  }
  .a-m-foot > span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .a-m-expand {
    flex-shrink: 0;
    padding: 0;
    border: 0;
    background: transparent;
    color: rgba(255, 255, 255, 0.9);
    font-size: 11px;
    font-weight: 500;
    cursor: pointer;
  }

  /* --- 维度胶囊（沿用组合页计数，配色按设计稿） --- */
  .a-m-pills {
    display: flex;
    gap: 8px;
    padding: 16px 16px 12px;
    overflow-x: auto;
    scrollbar-width: none;
    background: var(--sa-bg);
  }
  .a-m-pills::-webkit-scrollbar {
    display: none;
  }
  .a-m-filter {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
    padding: 6px 14px;
    border: 1px solid var(--sa-border-subtle);
    border-radius: 999px;
    background: var(--sa-subtle);
    color: var(--sa-text-secondary);
    font-size: 12px;
    font-weight: 500;
    white-space: nowrap;
    cursor: pointer;
  }
  .a-m-filter i {
    font-style: normal;
    opacity: 0.7;
    font-size: 11px;
  }
  .a-m-filter--on {
    background: var(--sa-text-primary);
    color: var(--sa-bg);
    font-weight: 700;
  }

  /* --- 作品区：沿用项目既有 VideoCard 网格，只换承载底色 --- */
  .video-section,
  .empty-state {
    padding: 0 16px 24px;
    background: var(--sa-bg);
    color: var(--sa-text-primary);
  }
  .mv-page :deep(.video-grid) {
    padding-bottom: 12px;
  }

  /* --- 完整档案：抽屉改成半屏底部弹层 --- */
  .ds-drawer {
    position: fixed;
    top: auto !important;
    right: 0 !important;
    bottom: 0;
    left: 0 !important;
    width: 100% !important;
    max-width: 100% !important;
    max-height: 80vh;
    overflow-y: auto;
    border-radius: 20px 20px 0 0;
    border-top: 1px solid var(--sa-border-subtle);
    /* 底部弹层沿用桌面抽屉的深色毛玻璃（本身就是覆盖层，两种主题下都可读） */
    background: rgba(13, 16, 22, 0.86);
    transform: translateY(100%) !important;
    transition: transform 0.3s cubic-bezier(0.2, 0.8, 0.2, 1);
    z-index: 201;
  }
  .ds-drawer.is-open {
    transform: translateY(0) !important;
  }
  /* 拖拽提示条：绝对定位居中（放在 .ds-head 里会变成 flex item、被挤到左侧） */
  .ds-drawer::before {
    content: '';
    position: absolute;
    top: 8px;
    left: 50%;
    width: 36px;
    height: 4px;
    border-radius: 2px;
    background: rgba(255, 255, 255, 0.18);
    transform: translateX(-50%);
  }
  .ds-head {
    top: 18px;
    right: 18px;
  }
  .ds-scrim {
    z-index: 200;
    background: rgba(0, 0, 0, 0.75);
  }
}



/* 移动端作品网格：与组合详情页 gallery-card 同一套观感 */
.a-m-works {
  display: none;
}
@media (max-width: 768px) {
  .a-desktop-filter,
  .a-desktop-grid,
  .mv-page :deep(.a-desktop-filter),
  .mv-page :deep(.a-desktop-grid) {
    display: none !important;
  }
  .a-m-works {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .a-m-work {
    min-width: 0;
    cursor: pointer;
  }
  .a-m-work-cover {
    position: relative;
    width: 100%;
    aspect-ratio: 16 / 9;
    overflow: hidden;
    border-radius: 10px;
    background: var(--sa-subtle);
    border: 1px solid var(--sa-border-subtle);
  }
  .a-m-work-cover img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .a-m-work-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    height: 100%;
    color: var(--sa-text-tertiary);
  }
  .a-m-work-title {
    margin: 8px 0 4px;
    font-size: 13px;
    font-weight: 600;
    line-height: 1.35;
    color: var(--sa-text-primary);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .a-m-work-foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    font-size: 11px;
    color: var(--sa-text-secondary);
  }
  .a-m-work-type {
    color: #60a5fa;
    white-space: nowrap;
  }
  .a-m-work-meta {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

</style>
