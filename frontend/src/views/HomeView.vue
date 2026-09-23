<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { musicVideosApi } from '@/api/musicVideos'
import { artistsApi } from '@/api/artists'
import { homeApi, type HeroStageItem } from '@/api/home'
import type { MusicVideo, Artist } from '@/types/models'
import { formatDate, formatVideoSongs } from '@/utils/format'
import { LIVE_TYPES } from '@/utils/videoTypes'
import { artistPath, groupPath, videoPath } from '@/utils/routes'
import { IMG_W_CARD } from '@/utils/imageSizes'
import VideoCard from '@/components/VideoCard.vue'
import {
  MovieOutlined,
} from '@/components/icons'
import SaHeader from '@/components/SaHeader.vue'
import HomeCinemaHero from '@/components/home/HomeCinemaHero.vue'
import HomeMagazineHero from '@/components/home/HomeMagazineHero.vue'
import { useSettingsStore } from '@/stores/settings'

const router = useRouter()
const message = useMessage()
const settings = useSettingsStore()

const MOBILE_QUERY = '(max-width: 768px)'
const isMobile = ref(
  typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches,
)
let mobileMq: MediaQueryList | undefined
function onMobileMqChange() {
  isMobile.value = !!mobileMq?.matches
}

// 现场/舞蹈类视频类型（首页"近期现场视频"板块）：与详情页「现场 · 直拍」同一份口径
const LIVE_VIDEO_TYPES = LIVE_TYPES

const liveVideos = ref<MusicVideo[]>([])
const mvVideos = ref<MusicVideo[]>([])
const shortVideos = ref<MusicVideo[]>([])
const artists = ref<Artist[]>([])
const loading = ref(false)
const brokenThumbs = ref<Set<number>>(new Set())
const brokenAvatars = ref<Set<number>>(new Set())
const avatarRetry = ref<Record<number, number>>({})
// 各板块本次加载是否失败：失败显示重试入口，成功但无数据才显示「暂无」
const loadFailed = ref({ live: false, mv: false, shorts: false, artists: false })

// ===== 沉浸模式：Billboard 轮播 =====
const HERO_SLIDE_MS = 7000
const STAGE_SLIDE_MS = 5000
const heroPool = ref<MusicVideo[]>([])
const heroIndex = ref(0)
const heroPaused = ref(false)
let heroTimer: ReturnType<typeof setTimeout> | undefined
let heroTouchX = 0
let heroTouchY = 0
// ===== 沉浸模式：艺人/组合主舞台轮播 =====
const stagePool = ref<HeroStageItem[]>([])
const stageIndex = ref(0)
let stageTimer: ReturnType<typeof setTimeout> | undefined

// 手机 1:1 顶栏：有头像池就用艺人/组合；空池才回退视频轮播
const showArtistStage = computed(() => stagePool.value.length > 0)

function scheduleStage() {
  clearTimeout(stageTimer)
  if (stagePool.value.length < 2) return
  stageTimer = setTimeout(() => {
    if (!heroPaused.value && !document.hidden) {
      stageIndex.value = (stageIndex.value + 1) % stagePool.value.length
    }
    scheduleStage()
  }, STAGE_SLIDE_MS)
}

function nextStage() {
  if (!stagePool.value.length) return
  stageIndex.value = (stageIndex.value + 1) % stagePool.value.length
  scheduleStage()
}

function prevStage() {
  if (!stagePool.value.length) return
  stageIndex.value = (stageIndex.value - 1 + stagePool.value.length) % stagePool.value.length
  scheduleStage()
}

function goStage(item: HeroStageItem) {
  router.push(item.type === 'artist' ? artistPath(item.uid) : groupPath(item.uid))
}

function goHeroIndex(i: number) {
  if (showArtistStage.value) {
    if (i < 0 || i >= stagePool.value.length) return
    stageIndex.value = i
    scheduleStage()
    return
  }
  if (i < 0 || i >= heroPool.value.length) return
  heroIndex.value = i
  scheduleHero()
}

function scheduleHero() {
  clearTimeout(heroTimer)
  if (heroPool.value.length < 2) return
  heroTimer = setTimeout(() => {
    if (!heroPaused.value && !document.hidden) {
      heroIndex.value = (heroIndex.value + 1) % heroPool.value.length
    }
    scheduleHero()
  }, HERO_SLIDE_MS)
}

function nextHero() {
  if (!heroPool.value.length) return
  heroIndex.value = (heroIndex.value + 1) % heroPool.value.length
  scheduleHero()
}

function prevHero() {
  if (!heroPool.value.length) return
  heroIndex.value = (heroIndex.value - 1 + heroPool.value.length) % heroPool.value.length
  scheduleHero()
}

function onHeroTouchStart(e: TouchEvent) {
  heroTouchX = e.touches[0].clientX
  heroTouchY = e.touches[0].clientY
  heroPaused.value = true
}

function onHeroTouchEnd(e: TouchEvent) {
  heroPaused.value = false
  const dx = e.changedTouches[0].clientX - heroTouchX
  const dy = e.changedTouches[0].clientY - heroTouchY
  // 横向滑动切换；纵向滑动交给页面滚动
  if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy)) {
    const next = showArtistStage.value ? nextStage : nextHero
    const prev = showArtistStage.value ? prevStage : prevHero
    if (dx < 0) next()
    else prev()
    return
  }
}

onBeforeUnmount(() => {
  clearTimeout(heroTimer)
  clearTimeout(stageTimer)
  mobileMq?.removeEventListener('change', onMobileMqChange)
})

async function load() {
  loading.value = true
  loadFailed.value = { live: false, mv: false, shorts: false, artists: false }
  try {
    // PC 杂志刊头 + 手机 1:1 头像都要艺人/组合池（必须有头像）
    const heroId = settings.heroVideoId
    const poolIds = settings.heroVideoIds
    const [l, m, s, a, h, poolRes, stageRes] = await Promise.all([
      musicVideosApi
        .list({ video_types: LIVE_VIDEO_TYPES.join(','), page_size: 8, ingestion_status: 'library', is_short: false })
        .catch(() => {
          loadFailed.value.live = true
          return null
        }),
      musicVideosApi
        .list({ video_types: 'OfficialMV', page_size: 8, ingestion_status: 'library', is_short: false })
        .catch(() => {
          loadFailed.value.mv = true
          return null
        }),
      musicVideosApi
        .list({ page_size: 6, ingestion_status: 'library', is_short: true })
        .catch(() => {
          loadFailed.value.shorts = true
          return null
        }),
      artistsApi
        .list({ page_size: 4, only_with_videos: true })
        .catch(() => {
          loadFailed.value.artists = true
          return null
        }),
      heroId ? musicVideosApi.get(heroId).catch(() => null) : Promise.resolve(null),
      Promise.all(
        (poolIds.length ? poolIds : heroId ? [heroId] : []).map((id) =>
          musicVideosApi.get(id).catch(() => null),
        ),
      ),
      homeApi.heroStage(8).catch(() => null),
    ])
    liveVideos.value = l?.items ?? []
    mvVideos.value = m?.items ?? []
    shortVideos.value = s?.items ?? []
    artists.value = a?.items ?? []

    // 艺人/组合主舞台池：随机返回，为空则回退视频轮播
    stagePool.value = stageRes?.items ?? []
    stageIndex.value = 0
    scheduleStage()

    // 轮播池：设置页多选；未配置时回退「主打视频 + 直拍」
    let pool = poolRes.filter((v): v is MusicVideo => v !== null)
    if (!pool.length) {
      const fallback: MusicVideo[] = []
      if (h) fallback.push(h)
      const fancam =
        l?.items.find((v) => v.video_type === 'Fancam') ?? l?.items[0] ?? null
      if (fancam && (!h || fancam.id !== h.id)) fallback.push(fancam)
      pool = fallback
    }
    heroPool.value = pool
    heroIndex.value = 0
    scheduleHero()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

// 现场 / MV 两个板块结构相同，循环渲染
const posterSections = computed(() => [
  {
    title: '近期现场视频',
    empty: '暂无现场视频',
    failed: loadFailed.value.live,
    videos: liveVideos.value,
  },
  {
    title: '近期音乐录影带',
    empty: '暂无音乐录影带',
    failed: loadFailed.value.mv,
    videos: mvVideos.value,
  },
])

// 点击跳转视频详情页
function goVideo(mv: { uid: string }) {
  router.push(videoPath(mv.uid))
}

function goArtist(a: { uid: string }) {
  router.push(artistPath(a.uid))
}

function scrollTop() {
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function thumbUrl(mv: MusicVideo) {
  if (!settings.showCovers || brokenThumbs.value.has(mv.id)) return undefined
  return musicVideosApi.thumbnailUrl(mv.id, mv.file_hash || mv.file_size, IMG_W_CARD)
}
function onThumbError(mv: MusicVideo) {
  brokenThumbs.value = new Set(brokenThumbs.value).add(mv.id)
}

const avatarPalettes = [
  ['#2a2140', '#15151b'],
  ['#22242a', '#15151b'],
  ['#2a2220', '#15151b'],
  ['#1e2a2e', '#15151b'],
  ['#241e2e', '#15151b'],
]
function avatarStyle(id: number) {
  const [c1, c2] = avatarPalettes[id % avatarPalettes.length]
  return { background: `linear-gradient(135deg, ${c1}, ${c2})` }
}
function initialOf(name?: string | null) {
  return (name || '?').trim().charAt(0).toUpperCase()
}

function artistAvatarSrc(a: Artist) {
  if (!a.avatar_path || brokenAvatars.value.has(a.id)) return ''
  // updated_at 作为缓存键：仅在头像/资料更新后失效，平时命中浏览器缓存；
  // w=320 缩放变体 + 失败重试计数（r），外网慢速链路下降低失败面
  const retry = avatarRetry.value[a.id] || 0
  return `${artistsApi.avatarUrl(a.id)}?t=${Date.parse(a.updated_at || '') || 0}&r=${retry}&w=320`
}
function onAvatarError(id: number) {
  if ((avatarRetry.value[id] || 0) < 1) {
    avatarRetry.value = { ...avatarRetry.value, [id]: (avatarRetry.value[id] || 0) + 1 }
  } else {
    brokenAvatars.value = new Set(brokenAvatars.value).add(id)
  }
}

onMounted(() => {
  mobileMq = window.matchMedia(MOBILE_QUERY)
  isMobile.value = mobileMq.matches
  mobileMq.addEventListener('change', onMobileMqChange)
  void load()
})
</script>

<template>
  <div class="sa-home">
    <!-- 顶栏 -->
    <SaHeader />

    <main>
      <!-- 手机：原来的 1:1 头像顶栏 -->
      <div
        v-if="isMobile"
        @mouseenter="heroPaused = true"
        @mouseleave="heroPaused = false"
      >
        <HomeCinemaHero
          :stage-pool="stagePool"
          :stage-index="stageIndex"
          :hero-pool="heroPool"
          :hero-index="heroIndex"
          :show-artist-stage="showArtistStage"
          @next="showArtistStage ? nextStage() : nextHero()"
          @prev="showArtistStage ? prevStage() : prevHero()"
          @open-stage="goStage"
          @open-video="goVideo"
          @go-index="goHeroIndex"
          @touchstart="onHeroTouchStart"
          @touchend="onHeroTouchEnd"
        />
      </div>

      <!-- PC：杂志刊头（无素材时优雅降级，仅展示下方内容行） -->
      <HomeMagazineHero
        v-else-if="stagePool.length"
        :pool="stagePool"
        @open="goStage"
      />

      <div
        class="container-sa"
        :class="{ 'container-sa--mag': !isMobile && stagePool.length }"
      >
        <!-- 近期现场视频 / 近期音乐录影带：结构相同，循环渲染 -->
        <section v-for="sec in posterSections" :key="sec.title" class="sa-section">
          <div class="section-head">
            <h2 class="section-title">{{ sec.title }}</h2>
          </div>

          <div v-if="sec.videos.length" class="poster-grid">
            <a
              v-for="mv in sec.videos"
              :key="mv.id"
              class="poster-card"
              :href="videoPath(mv.uid)"
              @click.prevent="goVideo(mv)"
            >
              <div class="poster-cover">
                <img
                  v-if="thumbUrl(mv)"
                  :src="thumbUrl(mv)!"
                  :alt="mv.name"
                  class="poster-img"
                  loading="lazy"
                  @error="onThumbError(mv)"
                />
                <div v-else class="poster-img poster-placeholder">
                  <MovieOutlined :size="26" />
                </div>
              </div>
              <div class="poster-title" :title="mv.name">{{ mv.name }}</div>
              <div class="poster-meta">
                <span>{{ formatDate(mv.release_date || mv.published_date) }}</span>
                <span v-if="formatVideoSongs(mv.songs, mv.song_name, mv.song_chinese_name)">
                  {{ formatVideoSongs(mv.songs, mv.song_name, mv.song_chinese_name) }}
                </span>
              </div>
            </a>
          </div>
          <div v-else-if="sec.failed" class="empty-block">
            加载失败
            <button class="empty-retry" @click="load">重试</button>
          </div>
          <div v-else-if="!loading" class="empty-block">{{ sec.empty }}</div>
        </section>

        <!-- 近期短视频 -->
        <section v-if="loadFailed.shorts" class="sa-section">
          <div class="section-head">
            <h2 class="section-title">近期短视频</h2>
          </div>
          <div class="empty-block">
            加载失败
            <button class="empty-retry" @click="load">重试</button>
          </div>
        </section>
        <section v-else-if="shortVideos.length" class="sa-section">
          <div class="section-head">
            <h2 class="section-title">近期短视频</h2>
            <button class="section-more" type="button" @click="router.push('/shorts')">查看全部</button>
          </div>
          <div class="shorts-grid">
            <VideoCard
              v-for="(mv, i) in shortVideos"
              :key="mv.id"
              :video="mv"
              :index="i"
              portrait
              hideType
            />
          </div>
        </section>

        <!-- 艺人 -->
        <section class="sa-section">
          <div class="section-head">
            <h2 class="section-title">值得关注的艺人</h2>
          </div>

          <div v-if="loadFailed.artists" class="empty-block">
            加载失败
            <button class="empty-retry" @click="load">重试</button>
          </div>
          <div v-else-if="artists.length" class="artist-grid">
            <button
              v-for="a in artists"
              :key="a.id"
              class="artist-capsule"
              @click="goArtist(a)"
            >
              <div class="artist-avatar" :style="avatarStyle(a.id)">
                <img
                  v-if="artistAvatarSrc(a)"
                  class="artist-avatar-img"
                  :src="artistAvatarSrc(a)"
                  alt=""
                  loading="lazy"
                  decoding="async"
                  @error="onAvatarError(a.id)"
                />
                <template v-else>{{ initialOf(a.chinese_name || a.name) }}</template>
              </div>
              <div class="artist-info">
                <div class="artist-name">{{ a.name }}</div>
                <div class="artist-meta">{{ a.chinese_name || a.stage_name || '艺人' }}</div>
              </div>
            </button>
          </div>
          <div v-else-if="!loading" class="empty-block">暂无艺人数据</div>
        </section>
      </div>
    </main>

    <footer class="sa-footer">
      <div class="container-sa footer-inner">
        <span>idolMatrix · 本地自建媒体管理</span>
        <button class="footer-home" @click="scrollTop">返回顶部</button>
      </div>
    </footer>
  </div>
</template>

<style scoped>
/* ===== StarAtlas 风格设计令牌（全局主题提供 --sa-*） ===== */
.sa-home {
  min-height: 100vh;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
  font-size: 14px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

.container-sa {
  width: 100%;
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px;
}
@media (max-width: 768px) {
  .container-sa {
    padding: 0 12px;
  }
}

/* 杂志 Hero 后：去掉多余顶距，让「近期视频」进入首屏（间距由 Hero 底边 24–32px 承担） */
.container-sa--mag {
  padding-top: 0;
}
.container-sa--mag > .sa-section:first-child {
  margin-top: 0;
}

/* ===== Section ===== */
.sa-section {
  margin-bottom: 64px;
}
.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.section-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--sa-text-primary);
}
.section-more {
  border: none;
  background: none;
  padding: 0;
  font-size: 13px;
  color: var(--sa-text-secondary);
  cursor: pointer;
}
.section-more:hover {
  color: var(--sa-accent);
}
.shorts-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
@media (min-width: 768px) {
  .shorts-grid {
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 20px;
  }
}
.shorts-grid :deep(.video-card) {
  width: 100%;
}

/* ===== Poster ===== */
.poster-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 20px;
}
@media (min-width: 768px) {
  .poster-grid {
    grid-template-columns: repeat(4, 1fr);
  }
}
.poster-card {
  display: block;
  min-width: 0;
}
.poster-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 8px;
  overflow: hidden;
  background: var(--sa-subtle);
  margin-bottom: 12px;
}
.poster-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: filter 0.2s;
}
.poster-card:hover .poster-img {
  filter: brightness(1.05);
}
.poster-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--sa-text-tertiary);
}
.poster-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--sa-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.poster-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
}
.poster-meta span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ===== Artist ===== */
.artist-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}
@media (min-width: 768px) {
  .artist-grid {
    grid-template-columns: repeat(4, 1fr);
  }
}
.artist-capsule {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  transition: all 0.15s;
  min-width: 0;
  width: 100%;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.artist-capsule:hover {
  background: var(--sa-subtle);
  border-color: var(--sa-border);
}
.artist-avatar {
  width: 44px;
  height: 44px;
  border-radius: 9999px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--sa-text-secondary);
  font-size: 16px;
  font-weight: 600;
  overflow: hidden;
}
.artist-avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.artist-info {
  min-width: 0;
}
.artist-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--sa-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.artist-meta {
  font-size: 11px;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ===== Empty / Footer ===== */
.empty-block {
  padding: 40px 0;
  text-align: center;
  color: var(--sa-text-secondary);
  font-size: 13px;
}
.empty-block a {
  color: var(--sa-accent);
}
.empty-retry {
  margin-left: 10px;
  padding: 3px 12px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 999px;
  background: transparent;
  color: var(--sa-accent);
  font-size: 12px;
  cursor: pointer;
}
.empty-retry:hover {
  filter: brightness(1.2);
}
.sa-footer {
  border-top: 1px solid var(--sa-border-subtle);
  padding: 24px 0;
}
.footer-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--sa-text-tertiary);
  font-size: 12px;
}
.footer-home {
  background: none;
  border: none;
  color: var(--sa-text-secondary);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}
.footer-home:hover {
  color: var(--sa-text-primary);
}
</style>
