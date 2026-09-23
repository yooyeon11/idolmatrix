<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { HeroStageItem } from '@/api/home'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'
import { musicVideosApi } from '@/api/musicVideos'
import type { MusicVideo } from '@/types/models'
import { MovieOutlined, ChevronRightOutlined } from '@/components/icons'
import { useSettingsStore } from '@/stores/settings'
import { extractPanelTint, type PanelTint } from '@/utils/heroPalette'
import { nameSizeTier } from '@/utils/heroTitle'
import { IMG_W_FULL, IMG_W_TINT, withImageWidth } from '@/utils/imageSizes'

const props = defineProps<{
  stagePool: HeroStageItem[]
  stageIndex: number
  heroPool: MusicVideo[]
  heroIndex: number
  showArtistStage: boolean
}>()

const emit = defineEmits<{
  next: []
  prev: []
  goIndex: [i: number]
  openStage: [item: HeroStageItem]
  openVideo: [mv: MusicVideo]
  touchstart: [e: TouchEvent]
  touchend: [e: TouchEvent]
}>()

const settings = useSettingsStore()
const stageBroken = ref<Set<string>>(new Set())
const stageRetry = ref<Record<string, number>>({})
const brokenThumbs = ref<Set<number>>(new Set())

function stageKey(item: HeroStageItem) {
  return `${item.type}-${item.id}`
}

/**
 * 顶栏图片 URL。width 为展示宽度档位（默认满屏档）：
 * 头像/横幅原图可能是几千像素，而这里只展示一屏宽（手机 DPR3 ≈ 1170），
 * 按档出变体；服务端只缩不放，源图小则自动回退原图。
 * 取色调色另走 IMG_W_TINT，避免为采样 48×48 去拉大图。
 */
function stageImgUrl(item: HeroStageItem, width: number = IMG_W_FULL) {
  if (stageBroken.value.has(stageKey(item))) return ''
  const bust = `?t=${Date.parse(item.updated_at || '') || 0}&r=${stageRetry.value[stageKey(item)] || 0}`
  // 手机 1:1 顶栏优先正方形头像
  let url = ''
  if (item.has_avatar) {
    url = item.type === 'artist' ? artistsApi.avatarUrl(item.id) : groupsApi.avatarUrl(item.id)
  } else if (item.has_banner) {
    url = item.type === 'artist' ? artistsApi.bannerUrl(item.id) : groupsApi.bannerUrl(item.id)
  }
  return url ? withImageWidth(url + bust, width) : ''
}

const prevStageIndex = computed(() =>
  props.stagePool.length
    ? (props.stageIndex - 1 + props.stagePool.length) % props.stagePool.length
    : 0,
)
const nextStageIndex = computed(() =>
  props.stagePool.length ? (props.stageIndex + 1) % props.stagePool.length : 0,
)
function stageShouldLoad(i: number) {
  return i === props.stageIndex || i === nextStageIndex.value || i === prevStageIndex.value
}

function stageImgStyle(item: HeroStageItem) {
  const x = item.focus_x ?? 0.5
  const y = item.focus_y ?? 0.32
  return {
    objectPosition: `${(x * 100).toFixed(1)}% ${(Math.max(0.18, y * 0.88) * 100).toFixed(1)}%`,
  }
}

function onStageImgError(item: HeroStageItem) {
  const key = stageKey(item)
  if ((stageRetry.value[key] || 0) < 1) {
    stageRetry.value = { ...stageRetry.value, [key]: (stageRetry.value[key] || 0) + 1 }
  } else {
    stageBroken.value = new Set(stageBroken.value).add(key)
  }
}

/** 与 PC 刊头一致：大字用艺名，下面才是中文/本名 */
function displayName(item: HeroStageItem) {
  const stage = (item.stage_name || '').trim()
  if (stage) return stage
  const name = (item.name || '').trim()
  if (name) return name
  return (item.chinese_name || '').trim()
}

function subtitle(item: HeroStageItem) {
  const headline = displayName(item).toLowerCase()
  const candidates = [item.english_name, item.korean_name, item.chinese_name, item.name]
  for (const c of candidates) {
    const t = (c || '').trim()
    if (t && t.toLowerCase() !== headline) return t
  }
  return ''
}

function nameClass(name: string) {
  // 分档阈值与详情页手机端大字共用（utils/heroTitle），只在这里映射成轮播的类名
  const tier = nameSizeTier(name)
  return tier === 'base' ? '' : `cinema-title--${tier}`
}

const currentStage = computed(() => props.stagePool[props.stageIndex] ?? null)
const currentSlide = computed(() => props.heroPool[props.heroIndex] ?? null)

const panelTint = ref<PanelTint | null>(null)
const cinemaStyle = computed(() => ({
  '--mag-panel': panelTint.value?.panel ?? '0, 0, 0',
  '--mag-accent': panelTint.value?.accent ?? '200, 200, 200',
}))

const tintSrc = computed(() => {
  if (props.showArtistStage) {
    const item = currentStage.value
    if (!item) return ''
    return stageImgUrl(item, IMG_W_TINT)
  }
  const mv = currentSlide.value
  if (!mv) return ''
  const u = thumbUrl(mv)
  return u ? `${u}${u.includes('?') ? '&' : '?'}w=160` : ''
})

watch(
  tintSrc,
  async (src) => {
    if (!src) {
      panelTint.value = null
      return
    }
    const tint = await extractPanelTint(src, 'bottom')
    if (tintSrc.value === src) panelTint.value = tint
  },
  { immediate: true },
)

function heroImgStyle(mv: MusicVideo) {
  const x = mv.focus_x ?? 0.5
  const y = mv.focus_y ?? 0.5
  return {
    objectPosition: `${(x * 100).toFixed(1)}% ${(y * 100).toFixed(1)}%`,
  }
}

function thumbUrl(mv: MusicVideo) {
  if (!settings.showCovers || brokenThumbs.value.has(mv.id)) return undefined
  return musicVideosApi.thumbnailUrl(mv.id, mv.file_hash || mv.file_size, IMG_W_FULL)
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

let swipeUntil = 0
function onOpenStage() {
  if (Date.now() < swipeUntil) return
  if (currentStage.value) emit('openStage', currentStage.value)
}

function onTouchEndWrap(e: TouchEvent) {
  swipeUntil = Date.now() + 500
  emit('touchend', e)
}
</script>

<template>
  <section
    class="cinema-wrap"
    @touchstart.passive="emit('touchstart', $event)"
    @touchend.passive="onTouchEndWrap"
  >
    <!-- 艺人/组合影院海报墙 -->
    <div
      v-if="showArtistStage"
      class="cinema-hero"
      role="button"
      tabindex="0"
      :style="cinemaStyle"
      @click="onOpenStage"
      @keydown.enter.prevent="onOpenStage"
      @keydown.space.prevent="onOpenStage"
    >
      <div
        v-for="(item, i) in stagePool"
        :key="stageKey(item)"
        class="cinema-slide"
        :class="{ 'cinema-slide--active': i === stageIndex }"
        :aria-hidden="i !== stageIndex"
      >
        <img
          v-if="stageImgUrl(item) && stageShouldLoad(i)"
          :src="stageImgUrl(item)!"
          :alt="displayName(item)"
          class="cinema-img"
          :class="{ 'cinema-img--avatar': item.has_avatar }"
          :style="stageImgStyle(item)"
          loading="eager"
          :fetchpriority="i === stageIndex ? 'high' : 'low'"
          @error="onStageImgError(item)"
        />
        <div v-else class="cinema-img cinema-placeholder" :style="avatarStyle(item.id)">
          <MovieOutlined :size="48" />
        </div>
        <div class="cinema-scrim" />
      </div>

      <div v-if="currentStage" class="cinema-copy">
        <h1 class="cinema-title" :class="nameClass(displayName(currentStage))">
          {{ displayName(currentStage) }}
        </h1>
        <p v-if="subtitle(currentStage)" class="cinema-subname">
          {{ subtitle(currentStage) }}
        </p>
        <button class="cinema-cta" type="button" @click.stop="onOpenStage">
          <span>进入资料页</span>
          <ChevronRightOutlined :size="16" />
        </button>
      </div>
      <div
        v-if="stagePool.length > 1"
        class="cinema-dots"
        role="tablist"
        aria-label="精选轮播"
        @click.stop
      >
        <button
          v-for="(item, i) in stagePool"
          :key="stageKey(item)"
          type="button"
          class="cinema-dot"
          :class="{ 'cinema-dot--on': i === stageIndex }"
          :aria-label="`切换到 ${displayName(item)}`"
          :aria-selected="i === stageIndex"
          role="tab"
          @click.stop="emit('goIndex', i)"
        />
      </div>
    </div>

    <!-- 视频轮播回退 -->
    <div v-else class="cinema-hero" :style="cinemaStyle">
      <div
        v-for="(mv, i) in heroPool"
        :key="mv.id"
        class="cinema-slide"
        :class="{ 'cinema-slide--active': i === heroIndex }"
        :aria-hidden="i !== heroIndex"
      >
        <img
          v-if="thumbUrl(mv)"
          :src="thumbUrl(mv)!"
          :alt="mv.name"
          class="cinema-img"
          :style="heroImgStyle(mv)"
          loading="eager"
          @error="onThumbError(mv)"
        />
        <div v-else class="cinema-img cinema-placeholder" :style="avatarStyle(mv.id)">
          <MovieOutlined :size="48" />
        </div>
        <div class="cinema-scrim" />
      </div>

      <div v-if="currentSlide" class="cinema-copy">
        <h1 class="cinema-title" :class="nameClass(currentSlide.name)">
          {{ currentSlide.name }}
        </h1>
        <div class="cinema-actions">
          <button class="cinema-cta" type="button" @click.stop="emit('openVideo', currentSlide)">
            <span>立即播放</span>
            <ChevronRightOutlined :size="16" />
          </button>
        </div>
      </div>
      <div
        v-if="heroPool.length > 1"
        class="cinema-dots"
        role="tablist"
        aria-label="视频轮播"
        @click.stop
      >
        <button
          v-for="(mv, i) in heroPool"
          :key="mv.id"
          type="button"
          class="cinema-dot"
          :class="{ 'cinema-dot--on': i === heroIndex }"
          :aria-label="`切换到 ${mv.name}`"
          :aria-selected="i === heroIndex"
          role="tab"
          @click.stop="emit('goIndex', i)"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.cinema-wrap {
  width: 100%;
  padding: 0 0 24px;
  /* 首页轮播 hero 区**不跟随**全站换字体（阿里巴巴普惠体）——
     这里显式锁定「系统字体栈」；刊头大字另有 Playfair 衬线栈，见 .cinema-title。
     注：栈首原为 'Lato'，但项目里从未注册过该家族名（vfonts 注册的是 v-sans），
     它一直是哑声明，实际落到的就是下面这套系统字体，故直接写明。 */
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
    'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.cinema-hero {
  --mag-panel: 0, 0, 0;
  position: relative;
  width: 100%;
  /* 1:1 正方形，顶部多出安全区给刘海/状态栏叠压 */
  height: calc(100vw + env(safe-area-inset-top, 0px));
  overflow: hidden;
  background: rgb(var(--mag-panel));
  cursor: pointer;
}
.cinema-slide {
  position: absolute;
  inset: 0;
  opacity: 0;
  transition: opacity 0.7s ease;
  pointer-events: none;
}
.cinema-slide--active {
  opacity: 1;
}
.cinema-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.cinema-img--avatar {
  object-fit: cover;
}
.cinema-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.35);
}
.cinema-scrim {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to top,
    rgb(var(--mag-panel)) 0%,
    rgba(var(--mag-panel), 0.72) 10%,
    rgba(var(--mag-panel), 0.28) 24%,
    rgba(var(--mag-panel), 0) 38%
  );
}
.cinema-copy {
  position: absolute;
  left: 20px;
  right: 20px;
  bottom: 36px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  color: #fff;
  z-index: 2;
}
.cinema-title {
  font-family: 'Playfair Display', 'Iowan Old Style', Palatino, 'Songti SC',
    'Noto Serif SC', Georgia, serif;
  font-size: clamp(32px, 10vw, 44px);
  font-weight: 500;
  line-height: 1.05;
  letter-spacing: 0.04em;
  margin: 0 0 4px;
  max-width: 100%;
  overflow-wrap: anywhere;
}
.cinema-title--xl {
  font-size: clamp(36px, 11vw, 48px);
  font-weight: 400;
  letter-spacing: 0.06em;
}
.cinema-title--long {
  font-size: clamp(24px, 7vw, 32px);
  font-weight: 500;
  letter-spacing: 0.01em;
}
.cinema-subname {
  font-family: 'Playfair Display', Palatino, 'Songti SC', Georgia, serif;
  font-size: 15px;
  font-weight: 400;
  letter-spacing: 0.02em;
  color: rgba(255, 255, 255, 0.88);
  margin: 0;
}
.cinema-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
}
.cinema-cta {
  pointer-events: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 14px;
  padding: 9px 21px;
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 999px;
  background: transparent;
  color: #fff;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}
.cinema-cta:hover,
.cinema-cta:focus-visible {
  background: #fff;
  color: #111;
  border-color: #fff;
  outline: none;
}
.cinema-dots {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 12px;
  z-index: 3;
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 8px;
  pointer-events: none;
}
.cinema-dot {
  pointer-events: auto;
  width: 7px;
  height: 7px;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.38);
  cursor: pointer;
  transition: width 0.2s ease, background 0.2s ease;
}
.cinema-dot--on {
  width: 18px;
  background: #fff;
}

@media (display-mode: standalone) {
  .cinema-scrim {
    background:
      linear-gradient(
        to bottom,
        rgba(0, 0, 0, 0.44) 0%,
        rgba(0, 0, 0, 0.26) 24px,
        rgba(0, 0, 0, 0.12) 48px,
        rgba(0, 0, 0, 0.05) 72px,
        rgba(0, 0, 0, 0) 100px
      ),
      linear-gradient(
        to top,
        rgb(var(--mag-panel)) 0%,
        rgba(var(--mag-panel), 0.72) 10%,
        rgba(var(--mag-panel), 0.28) 24%,
        rgba(var(--mag-panel), 0) 38%
      );
  }
}

@media (prefers-reduced-motion: reduce) {
  .cinema-slide {
    transition: none;
  }
}
</style>
