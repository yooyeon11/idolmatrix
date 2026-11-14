<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { MusicVideo } from '@/types/models'
import { musicVideosApi } from '@/api/musicVideos'
import { artistPath, videoPath } from '@/utils/routes'
import { IMG_W_CARD } from '@/utils/imageSizes'
import { resolutionLabel } from '@/utils/resolution'
import { formatDate, formatDuration, initialOf } from '@/utils/format'
import { MovieOutlined, SettingsOutlined } from '@/components/icons'
import ThemeToggle from '@/components/ThemeToggle.vue'
import { useSettingsStore } from '@/stores/settings'
import { MIRROR_MODE } from '@/utils/mirror'

export interface HomeIdolCard {
  id: number
  uid: string
  name: string
  /** 所属组合名（无在团组合时留空，卡片不再显示视频数） */
  sub: string
  avatar: string
  updated: string
}

const props = defineProps<{
  idols: HomeIdolCard[]
  /** 当前轮动到第几页（0 起）：每页 GALLERY_SIZE 位艺人 */
  page?: number
  videos: MusicVideo[]
  artistTotal: number
  videoTotal: number
  loading: boolean
  failed: { idols: boolean; videos: boolean }
}>()

const emit = defineEmits<{
  retry: []
}>()

// ===== 艺人立绘墙：每页 4 位（2×2，设计稿口径），由父级首页时钟统一驱动 =====
const GALLERY_SIZE = 4
const galleryPage = computed(() => Math.max(0, props.page ?? 0))
const slotCount = computed(() => Math.min(GALLERY_SIZE, props.idols.length))
const visibleIdols = computed(() => {
  const start = galleryPage.value * GALLERY_SIZE
  const page = props.idols.slice(start, start + GALLERY_SIZE)
  // 最后一页不足 4 位时，用队首补位，避免出现空格子
  while (page.length < slotCount.value) {
    page.push(props.idols[page.length % Math.max(1, props.idols.length)])
  }
  return page
})

const router = useRouter()
const settings = useSettingsStore()

// ===== 顶部 mini 导航：沿用原有顶栏那套入口，只是换了位置与样式 =====
const navLinks = [
  { label: '浏览', to: '/browse' },
  { label: '组合', to: '/groups' },
  { label: '艺人', to: '/artists' },
  { label: '短视频', to: '/shorts' },
  { label: '歌曲', to: '/songs' },
  { label: '收藏', to: '/collections' },
  { label: '统计', to: '/stats' },
  { label: '博主', to: '/uploaders' },
]

// ===== 板块 01：焦点艺人立绘 =====
function goIdol(idol: HomeIdolCard) {
  router.push(artistPath(idol.uid))
}

const brokenAvatars = ref<Set<number>>(new Set())
function onAvatarError(id: number) {
  brokenAvatars.value = new Set(brokenAvatars.value).add(id)
}
function avatarSrc(idol: HomeIdolCard) {
  if (brokenAvatars.value.has(idol.id)) return ''
  const t = Date.parse(idol.updated || '') || 0
  return `${idol.avatar}?t=${t}&w=${IMG_W_CARD}`
}

// ===== 板块 02：最新入库视频 =====
function goVideo(mv: MusicVideo) {
  router.push(videoPath(mv.uid))
}

const brokenThumbs = ref<Set<number>>(new Set())
function onThumbError(id: number) {
  brokenThumbs.value = new Set(brokenThumbs.value).add(id)
}
function thumbUrl(mv: MusicVideo) {
  if (!settings.showCovers || brokenThumbs.value.has(mv.id)) return ''
  return musicVideosApi.thumbnailUrl(mv.id, mv.file_hash || mv.file_size, IMG_W_CARD)
}

/** 来源 chip：原始上传者优先 */
function sourceLabel(mv: MusicVideo) {
  return (mv.original_uploader || '').trim()
}

/** 元信息行：艺人/组合 · 日期 · 时长 */
function videoMeta(mv: MusicVideo): string {
  const parts: string[] = []
  const g = mv.groups?.[0]
  const a = mv.artists?.[0]
  const who = g
    ? g.chinese_name || g.name
    : a
      ? a.chinese_name || a.stage_name || a.name
      : mv.subject_artist_name
  if (who) parts.push(who)
  const d = formatDate(mv.release_date || mv.published_date)
  if (d !== '--') parts.push(d)
  parts.push(formatDuration(mv.duration))
  return parts.join(' · ')
}
</script>

<template>
  <div class="feed">
    <!-- 右上 mini 导航条：左链接 + 右主题/设置 -->
    <nav class="feed-nav" aria-label="首页导航">
      <div class="feed-nav-links" data-sa-nav>
        <router-link
          v-for="link in navLinks"
          :key="link.to"
          class="feed-nav-link"
          :to="link.to"
          active-class="feed-nav-link--active"
        >
          {{ link.label }}
        </router-link>
      </div>
      <div class="feed-nav-actions" data-sa-nav-actions>
        <ThemeToggle />
        <button
          v-if="!MIRROR_MODE"
          class="feed-nav-btn"
          type="button"
          aria-label="设置"
          title="设置"
          @click="router.push('/settings')"
        >
          <SettingsOutlined :size="15" />
        </button>
      </div>
    </nav>

    <!-- 板块 01：焦点艺人 -->
    <section class="feed-section">
      <div class="section-head">
        <div>
          <span class="section-kicker">01 // FOCUS IDOL ROSTER</span>
          <h2 class="section-title">焦点艺人</h2>
        </div>
        <a
          class="section-more"
          href="/artists"
          @click.prevent="router.push('/artists')"
        >
          查看全部艺人 →
        </a>
      </div>

      <!-- 立绘墙：每页 4 位（2×2），槽位固定、槽内交叉淡入 -->
      <div v-if="idols.length" class="idol-grid">
        <div
          v-for="(idol, i) in visibleIdols"
          :key="i"
          class="idol-slot"
          :class="{ 'idol-slot--drop': i % 2 === 1 }"
          :style="{ '--swap-delay': `${i * 45}ms` }"
        >
          <Transition name="idol-swap">
            <button
              :key="`${idol.id}-${galleryPage}`"
              class="idol-card"
              type="button"
              @click="goIdol(idol)"
            >
              <img
                v-if="avatarSrc(idol)"
                class="idol-img"
                :src="avatarSrc(idol)"
                :alt="idol.name"
                loading="lazy"
                decoding="async"
                @error="onAvatarError(idol.id)"
              />
              <div v-else class="idol-img idol-img--placeholder">
                {{ initialOf(idol.name) }}
              </div>
              <div class="idol-shade" />
              <div class="idol-info">
                <div class="idol-name">{{ idol.name }}</div>
                <div v-if="idol.sub" class="idol-sub">{{ idol.sub }}</div>
              </div>
            </button>
          </Transition>
        </div>
      </div>
      <div v-else-if="failed.idols" class="empty-block">
        加载失败
        <button class="empty-retry" type="button" @click="emit('retry')">重试</button>
      </div>
      <div v-else-if="!loading" class="empty-block">暂无艺人数据</div>
    </section>

    <!-- 板块 02：舞台精选 -->
    <section class="feed-section">
      <div class="section-head">
        <div>
          <span class="section-kicker">02 // LATEST STAGE ARCHIVE</span>
          <h2 class="section-title">舞台精选</h2>
        </div>
        <a
          class="section-more"
          href="/browse"
          @click.prevent="router.push('/browse')"
        >
          全部 {{ videoTotal }} 部 →
        </a>
      </div>

      <!-- 设计稿双列一排 2 张，第 2 张向下错位 -->
      <div v-if="videos.length" class="video-grid">
        <a
          v-for="(mv, i) in videos.slice(0, 2)"
          :key="mv.id"
          class="video-card"
          :class="{ 'video-card--drop': i % 2 === 1 }"
          :href="videoPath(mv.uid)"
          @click.prevent="goVideo(mv)"
        >
          <div class="video-cover">
            <img
              v-if="thumbUrl(mv)"
              class="video-img"
              :src="thumbUrl(mv)"
              :alt="mv.name"
              loading="lazy"
              decoding="async"
              @error="onThumbError(mv.id)"
            />
            <div v-else class="video-img video-img--placeholder">
              <MovieOutlined :size="26" />
            </div>
            <span v-if="sourceLabel(mv)" class="video-source">{{ sourceLabel(mv) }}</span>
            <span v-if="resolutionLabel(mv.width, mv.height) !== '--'" class="video-res">
              {{ resolutionLabel(mv.width, mv.height) }}
            </span>
          </div>
          <h3 class="video-title" :title="mv.name">{{ mv.name }}</h3>
          <p class="video-meta">{{ videoMeta(mv) }}</p>
        </a>
      </div>
      <div v-else-if="failed.videos" class="empty-block">
        加载失败
        <button class="empty-retry" type="button" @click="emit('retry')">重试</button>
      </div>
      <div v-else-if="!loading" class="empty-block">暂无入库视频</div>
    </section>
  </div>
</template>

<style scoped>
.feed {
  --spring: cubic-bezier(0.22, 1, 0.36, 1);
  --feed-serif: 'Playfair Display', 'Noto Serif SC', 'Songti SC', 'SimSun', serif;
  min-width: 0;
}

/* ===== 顶部 mini 导航条 ===== */
.feed-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--sa-border-subtle);
  font-size: 12px;
}
.feed-nav-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}
.feed-nav-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 8px;
  background: none;
  color: var(--sa-text-secondary);
  cursor: pointer;
  transition:
    color 0.15s ease,
    background-color 0.15s ease;
}
.feed-nav-btn:hover {
  color: var(--sa-text-primary);
  background: var(--sa-subtle);
}
.feed-nav-links {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 22px;
  font-weight: 600;
}
.feed-nav-link--active {
  color: var(--sa-text-primary);
}
.feed-nav-link {
  color: var(--sa-text-secondary);
  text-decoration: none;
  transition: color 0.15s ease;
}
.feed-nav-link:hover {
  color: var(--sa-text-primary);
}

/* ===== 通用板块头 ===== */
.feed-section {
  margin-top: 64px;
}
.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
}
.section-kicker {
  display: block;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--sa-accent);
}
.section-title {
  margin: 4px 0 0;
  font-family: var(--feed-serif);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--sa-text-primary);
}
.section-more {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--sa-text-secondary);
  text-decoration: none;
  transition: color 0.15s ease;
}
.section-more:hover {
  color: var(--sa-accent);
}

.empty-block {
  padding: 36px 0;
  text-align: center;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}
.empty-retry {
  margin-left: 8px;
  padding: 4px 14px;
  border-radius: 999px;
  border: 1px solid var(--sa-border);
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 12px;
  cursor: pointer;
}

/* ===== 板块 01：错位立绘画廊 ===== */
.idol-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
/* 固定槽位：轮动时槽位不动，只有槽内的卡片交叉淡入，避免整行重排 */
.idol-slot {
  position: relative;
  min-width: 0;
}
/* 与左侧焦点海报同一套时长/缓动，整页看起来是一次动作。
   注意：.idol-card 自带 transition（box-shadow/transform），这里用更高优先级覆盖，
   否则 opacity 不会参与过渡、transform 也会被 0.45s spring 抢走。 */
.idol-slot > .idol-card.idol-swap-enter-active,
.idol-slot > .idol-card.idol-swap-leave-active {
  transition:
    opacity 0.46s cubic-bezier(0.22, 0.61, 0.36, 1),
    transform 0.46s cubic-bezier(0.22, 0.61, 0.36, 1);
  transition-delay: var(--swap-delay, 0ms);
  will-change: opacity, transform;
}
.idol-slot > .idol-card.idol-swap-leave-active {
  position: absolute;
  inset: 0;
}
.idol-slot > .idol-card.idol-swap-enter-from {
  opacity: 0;
  transform: translateY(12px) scale(0.982);
}
.idol-slot > .idol-card.idol-swap-leave-to {
  opacity: 0;
  transform: translateY(-8px) scale(0.982);
}
.idol-card {
  position: relative;
  display: block;
  width: 100%;
  aspect-ratio: 3 / 4.2;
  padding: 0;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 16px;
  overflow: hidden;
  background: var(--sa-subtle);
  cursor: pointer;
  text-align: left;
  box-shadow: var(--sa-surface-shadow);
  transition:
    box-shadow 0.3s ease,
    transform 0.45s var(--spring);
}
.idol-card:hover {
  box-shadow: var(--sa-surface-shadow-hover);
  transform: translateY(-4px);
}
.idol-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.75s var(--spring);
}
.idol-card:hover .idol-img {
  transform: scale(1.06);
}
.idol-img--placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 40px;
  font-weight: 700;
  color: var(--sa-text-tertiary);
  background: linear-gradient(160deg, var(--sa-hover), var(--sa-subtle));
}
.idol-shade {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to top,
    rgba(0, 0, 0, 0.82) 0%,
    rgba(0, 0, 0, 0.25) 34%,
    transparent 58%
  );
  pointer-events: none;
}
.idol-info {
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 12px;
  color: #ffffff;
}
.idol-name {
  font-size: 14px;
  font-weight: 700;
  line-height: 1.3;
}
.idol-sub {
  margin-top: 2px;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.72);
}

/* ===== 板块 02：最新入库大画幅卡片 ===== */
.video-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 22px;
}
.video-card {
  display: block;
  text-decoration: none;
}
.video-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  margin-bottom: 10px;
  border-radius: 16px;
  border: 1px solid var(--sa-border-subtle);
  overflow: hidden;
  background: var(--sa-subtle);
  box-shadow: var(--sa-surface-shadow);
}
.video-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.75s var(--spring);
}
.video-card:hover .video-img {
  transform: scale(1.06);
}
.video-img--placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--sa-text-tertiary);
}
.video-source {
  position: absolute;
  top: 12px;
  left: 12px;
  max-width: 60%;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(10, 10, 14, 0.6);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: #ffffff;
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.video-res {
  position: absolute;
  top: 12px;
  right: 12px;
  padding: 4px 8px;
  border-radius: 999px;
  background: #ffffff;
  color: #0a0a0c;
  font-size: 10px;
  font-weight: 800;
}
.video-title {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.4;
  color: var(--sa-text-primary);
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
  transition: color 0.15s ease;
}
.video-card:hover .video-title {
  color: var(--sa-accent);
}
.video-meta {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}

/* ===== 断点：桌面 4 列立绘（设计稿口径：一屏 4 张）+ 双列视频，偶数位向下错位 ===== */
@media (min-width: 1024px) {
  .idol-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px;
  }
  .idol-slot--drop {
    margin-top: 26px;
  }
}
@media (min-width: 768px) and (max-width: 1023px) {
  .idol-grid {
    /* 平板 2×2：4 位一页正好铺满，不出现 3+1 的孤行 */
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 13px;
  }
}
@media (min-width: 768px) {
  .video-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 26px;
  }
  .video-card--drop {
    margin-top: 24px;
  }
}

@media (max-width: 768px) {
  /* 移动端由底部 BottomNav 承担入口，顶部不再保留一条重复导航；
     去掉导航后让第一块内容紧接焦点海报（仅保留 .home-feed 的 24px 上内边距）。 */
  .feed-nav {
    display: none;
  }
  .feed-section:first-of-type {
    margin-top: 0;
  }
}

@media (max-width: 480px) {
  .section-title {
    font-size: 20px;
  }
  .section-more {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .idol-swap-enter-active,
  .idol-swap-leave-active {
    transition: none;
  }
  .idol-img,
  .video-img,
  .idol-card,
  .video-title {
    transition: none;
  }
  .idol-card:hover .idol-img,
  .video-card:hover .video-img,
  .idol-card:hover {
    transform: none;
  }
}
</style>
