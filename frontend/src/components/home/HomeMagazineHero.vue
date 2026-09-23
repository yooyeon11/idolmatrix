<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { HeroStageItem } from '@/api/home'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'
import {
  MovieOutlined,
  ChevronRightOutlined,
  InstagramIcon,
  YoutubeIcon,
  TiktokIcon,
  XIcon,
  GlobeIcon,
  BilibiliIcon,
  WeiboIcon,
  FacebookIcon,
} from '@/components/icons'
import { extractPanelTint, type PanelTint } from '@/utils/heroPalette'
import { IMG_W_FULL, withImageWidth } from '@/utils/imageSizes'
import { useSettingsStore } from '@/stores/settings'

const props = defineProps<{
  pool: HeroStageItem[]
}>()

const emit = defineEmits<{
  open: [item: HeroStageItem]
}>()

// 优先图（头像 / 横幅海报）来自「基础设置 · 外观与显示」。
// ⚠ 这是 **PC 刊头专用** 分支 —— 移动端走 `HomeCinemaHero`，不读该设置。
const settings = useSettingsStore()

const ROTATE_MS = 5000
const featuredIndex = ref(0)
const broken = ref(false)
const retry = ref(0)
const hovered = ref(false)
const contentReady = ref(true)
const prefersReducedMotion = ref(false)
let rotateTimer: ReturnType<typeof setTimeout> | undefined
let contentDelayTimer: ReturnType<typeof setTimeout> | undefined
let touchX = 0
let touchY = 0

const featured = computed(() => props.pool[featuredIndex.value] ?? null)

watch(
  () => props.pool,
  () => {
    featuredIndex.value = 0
    broken.value = false
    retry.value = 0
    scheduleRotate()
  },
)

watch(
  featured,
  () => {
    broken.value = false
    retry.value = 0
    contentReady.value = false
    clearTimeout(contentDelayTimer)
    contentDelayTimer = setTimeout(() => {
      contentReady.value = true
    }, prefersReducedMotion.value ? 0 : 60)
  },
  { immediate: true },
)

function scheduleRotate() {
  clearTimeout(rotateTimer)
  if (props.pool.length < 2) return
  rotateTimer = setTimeout(() => {
    if (!hovered.value && !document.hidden) {
      featuredIndex.value = (featuredIndex.value + 1) % props.pool.length
    }
    scheduleRotate()
  }, ROTATE_MS)
}

function goIndex(i: number) {
  if (i < 0 || i >= props.pool.length) return
  featuredIndex.value = i
  scheduleRotate()
}

/** 刊头用最短的标志名：IU / IRENE / IVE */
function displayName(item: HeroStageItem) {
  const stage = (item.stage_name || '').trim()
  if (stage) return stage
  const name = (item.name || '').trim()
  if (name) return name
  return (item.chinese_name || '').trim()
}

/** 副标题：英文名优先（Lee Ji-eun / Irene Bae），否则韩/中文名 */
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
  const n = name.trim()
  const wide = /[\u3400-\u9fff\uac00-\ud7af]/.test(n)
  if (wide && n.length >= 5) return 'mag-name--long'
  if (n.length <= 5) return 'mag-name--xl'
  if (n.length >= 10) return 'mag-name--long'
  return ''
}

const SOCIAL_ORDER = [
  'instagram',
  'youtube',
  'tiktok',
  'x',
  'website',
  'bilibili',
  'weibo',
  'facebook',
] as const
const SOCIAL_LABEL: Record<(typeof SOCIAL_ORDER)[number], string> = {
  instagram: 'Instagram',
  youtube: 'YouTube',
  tiktok: 'TikTok',
  x: 'X',
  website: '官网',
  bilibili: 'Bilibili',
  weibo: '微博',
  facebook: 'Facebook',
}
const SOCIAL_ICON = {
  instagram: InstagramIcon,
  youtube: YoutubeIcon,
  tiktok: TiktokIcon,
  x: XIcon,
  website: GlobeIcon,
  bilibili: BilibiliIcon,
  weibo: WeiboIcon,
  facebook: FacebookIcon,
}

const socialLinks = computed(() => {
  const raw = featured.value?.social_media
  if (!raw || typeof raw !== 'object') return [] as { key: string; label: string; url: string }[]
  const map: Record<string, string> = {}
  for (const [k, v] of Object.entries(raw)) {
    let key = String(k || '').trim().toLowerCase()
    if (key === 'twitter') key = 'x'
    if (key === 'official' || key === 'official_site' || key === 'homepage' || key === 'site') {
      key = 'website'
    }
    const url = String(v || '').trim()
    if (!key || !url || map[key]) continue
    map[key] = url
  }
  const out: { key: (typeof SOCIAL_ORDER)[number]; label: string; url: string }[] = []
  for (const key of SOCIAL_ORDER) {
    if (!map[key]) continue
    out.push({ key, label: SOCIAL_LABEL[key], url: map[key] })
    if (out.length >= 3) break
  }
  return out
})

/** 刊头源图：默认正方形头像特写；「基础设置」选优先横幅海报时改为有横幅就用横幅。
 *  两个方向都保留回退（有横幅无头像 / 有头像无横幅都能出图），全无素材则返回空串走占位块。
 *  按展示宽度出变体（源图小则回退原图）。 */
function imgUrl(item: HeroStageItem) {
  if (broken.value) return ''
  const bust = `?t=${Date.parse(item.updated_at || '') || 0}&r=${retry.value}`
  const bannerFirst = settings.homeHeroImage === 'banner'
  const isArtist = item.type === 'artist'
  let url = ''
  if (bannerFirst && item.has_banner) {
    url = isArtist ? artistsApi.bannerUrl(item.id) : groupsApi.bannerUrl(item.id)
  } else if (item.has_avatar) {
    url = isArtist ? artistsApi.avatarUrl(item.id) : groupsApi.avatarUrl(item.id)
  } else if (item.has_banner) {
    url = isArtist ? artistsApi.bannerUrl(item.id) : groupsApi.bannerUrl(item.id)
  }
  // 刊头图片区约 845 CSS px（@2x 需 ~1690），取满屏档；调色抽样复用同一 URL，不额外发请求
  return url ? withImageWidth(url + bust, IMG_W_FULL) : ''
}

function imgStyle(item: HeroStageItem) {
  const x = item.focus_x
  const y = item.focus_y
  return {
    objectPosition: `${((x ?? 0.5) * 100).toFixed(1)}% ${((y ?? 0.32) * 100).toFixed(1)}%`,
  }
}

const panelTint = ref<PanelTint | null>(null)
const cardStyle = computed(() => ({
  '--mag-panel': panelTint.value?.panel ?? '0, 0, 0',
  '--mag-accent': panelTint.value?.accent ?? '200, 200, 200',
}))

watch(
  () => (featured.value ? imgUrl(featured.value) : ''),
  async (src) => {
    if (!src) {
      panelTint.value = null
      return
    }
    const tintSrc = `${src}${src.includes('?') ? '&' : '?'}w=160`
    const tint = await extractPanelTint(tintSrc, 'right')
    if (featured.value && imgUrl(featured.value) === src) panelTint.value = tint
  },
  { immediate: true },
)

function onImgError() {
  if (retry.value < 1) retry.value += 1
  else broken.value = true
}

const avatarPalettes = [
  ['#2a2140', '#15151b'],
  ['#22242a', '#15151b'],
  ['#2a2220', '#15151b'],
  ['#1e2a2e', '#15151b'],
  ['#241e2e', '#15151b'],
]
function placeholderStyle(id: number) {
  const [c1, c2] = avatarPalettes[id % avatarPalettes.length]
  return { background: `linear-gradient(135deg, ${c1}, ${c2})` }
}

function onOpen(item: HeroStageItem) {
  emit('open', item)
}

function onMediaKey(e: KeyboardEvent, item: HeroStageItem) {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    onOpen(item)
  }
}

function onTouchStart(e: TouchEvent) {
  touchX = e.touches[0].clientX
  touchY = e.touches[0].clientY
  hovered.value = true
}

function onTouchEnd(e: TouchEvent) {
  hovered.value = false
  const dx = e.changedTouches[0].clientX - touchX
  const dy = e.changedTouches[0].clientY - touchY
  if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy) && props.pool.length > 1) {
    const n = props.pool.length
    const next = dx < 0 ? (featuredIndex.value + 1) % n : (featuredIndex.value - 1 + n) % n
    goIndex(next)
  }
}

onMounted(() => {
  prefersReducedMotion.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  scheduleRotate()
})
onBeforeUnmount(() => {
  clearTimeout(rotateTimer)
  clearTimeout(contentDelayTimer)
})
</script>

<template>
  <section
    v-if="featured"
    class="mag-wrap"
    :class="{ 'mag-reduced': prefersReducedMotion }"
    @mouseenter="hovered = true"
    @mouseleave="hovered = false"
    @touchstart.passive="onTouchStart"
    @touchend.passive="onTouchEnd"
  >
    <div
      class="mag-card"
      role="button"
      tabindex="0"
      :style="cardStyle"
      :aria-label="`进入 ${displayName(featured)} 资料页`"
      @click="onOpen(featured)"
      @keydown="onMediaKey($event, featured)"
    >
      <div class="mag-media">
        <Transition name="mag-cross">
          <img
            v-if="imgUrl(featured)"
            :key="`${featured.type}-${featured.id}-${retry}`"
            :src="imgUrl(featured)!"
            :alt="displayName(featured)"
            class="mag-img"
            :style="imgStyle(featured)"
            loading="eager"
            fetchpriority="high"
            @error="onImgError"
          />
          <div
            v-else
            :key="`ph-${featured.type}-${featured.id}`"
            class="mag-img mag-placeholder"
            :style="placeholderStyle(featured.id)"
          >
            <MovieOutlined :size="36" />
          </div>
        </Transition>
      </div>

      <!-- 磨砂层是 .mag-media 的**兄弟**、直接铺满整卡（不再被 66% 的媒体盒裁住）：
           见 <style> 里 .mag-scrim 的注释 —— 为的是消灭 66% 处那条内部竖直边界。 -->
      <div class="mag-scrim" aria-hidden="true" />

      <div class="mag-copy" :class="{ 'mag-copy--ready': contentReady }">
        <h1 class="mag-name" :class="nameClass(displayName(featured))">
          {{ displayName(featured) }}
        </h1>
        <p v-if="subtitle(featured)" class="mag-sub">{{ subtitle(featured) }}</p>
        <button class="mag-cta" type="button" @click.stop="onOpen(featured)">
          <span>进入资料页</span>
          <ChevronRightOutlined :size="16" />
        </button>
        <div v-if="socialLinks.length" class="mag-social" @click.stop>
          <a
            v-for="s in socialLinks"
            :key="s.key"
            class="mag-social-link"
            :href="s.url"
            target="_blank"
            rel="noopener noreferrer"
            :title="s.label"
            :aria-label="s.label"
            @click.stop
          >
            <component :is="SOCIAL_ICON[s.key as keyof typeof SOCIAL_ICON]" :size="16" />
          </a>
        </div>
      </div>

      <div
        v-if="pool.length > 1"
        class="mag-dots"
        role="tablist"
        aria-label="精选轮播"
        @click.stop
      >
        <button
          v-for="(item, i) in pool"
          :key="`${item.type}-${item.id}`"
          type="button"
          class="mag-dot"
          :class="{ 'mag-dot--on': i === featuredIndex }"
          :aria-label="`切换到 ${displayName(item)}`"
          :aria-selected="i === featuredIndex"
          role="tab"
          @click.stop="goIndex(i)"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.mag-wrap {
  position: relative;
  width: 100%;
  max-width: 1280px;
  margin: 0 auto;
  padding: 16px 32px 28px;
  box-sizing: border-box;
  /* 首页轮播 hero 区**不跟随**全站换字体（阿里巴巴普惠体）——
     这里显式锁定「系统字体栈」；刊头大字另有 Playfair 衬线栈，见 .mag-name。
     注：栈首原为 'Lato'，但项目里从未注册过该家族名（vfonts 注册的是 v-sans），
     它一直是哑声明，实际落到的就是下面这套系统字体，故直接写明。 */
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
    'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

.mag-card {
  --mag-panel: 0, 0, 0;
  --mag-accent: 200, 200, 200;
  position: relative;
  width: 100%;
  aspect-ratio: 2.45 / 1;
  min-height: 300px;
  max-height: 440px;
  border-radius: 16px;
  overflow: hidden;
  background: rgb(var(--mag-panel));
  cursor: pointer;
  isolation: isolate;
  transition: background-color 0.45s ease;
}

/* 媒体盒铺满整卡，照片宽度改由 .mag-img 的 66% 决定。
   ⚠ 为什么不保持「66% 宽的盒子 + overflow:hidden」：那条 66% 的右缘就是一条**内部竖直边界**，
   而 66% 落在分数像素上（1212 × 0.66 = 799.92 → 边在 831.92px），安卓平板上实测会沿它渲出
   一条 1px 亮线（照片在暗面板上一路渗出来，观感就是「照片和文字之间一条白色竖条」）。
   现在把边界让给卡片自己的圆角裁切（见 .mag-card 的 overflow/radius），内部不再有硬边。 */
.mag-media {
  position: absolute;
  inset: 0;
  background: rgb(var(--mag-panel));
}

.mag-img {
  position: absolute;
  left: 0;
  top: 0;
  width: 66%;
  height: 100%;
  object-fit: cover;
  object-position: center 32%;
  display: block;
}

.mag-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.35);
}

/* 只在肖像右缘溶入刊头底，避免把脸涂暗。
   ⚠ 这条渐变现在是**铺满整卡**的（元素挂在 .mag-card 下，不在 .mag-media 里）：百分比 =
   原先「媒体盒内」的 62% / 84% / 100% × 66%，所以落点像素与旧版完全一致（100% 即照片右缘 66%）。
   末尾补一段到 100% 的实心面板色，把照片右缘（含任何 1px 渗色）彻底盖死。 */
.mag-scrim {
  position: absolute;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  background: linear-gradient(
    to right,
    rgba(0, 0, 0, 0) 0%,
    rgba(0, 0, 0, 0) 40.92%,
    rgba(var(--mag-panel), 0.55) 55.44%,
    rgb(var(--mag-panel)) 66%,
    rgb(var(--mag-panel)) 100%
  );
}

.mag-copy {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 2;
  width: 38%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 28px 36px 48px 8px;
  color: #fff;
  pointer-events: none;
  opacity: 0;
  transition: opacity 220ms ease;
}

.mag-copy--ready {
  opacity: 1;
}

.mag-reduced .mag-copy {
  transition: opacity 160ms ease;
}
.mag-reduced .mag-card {
  transition: none;
}

.mag-name {
  font-family: 'Playfair Display', 'Iowan Old Style', Palatino, 'Palatino Linotype',
    'Songti SC', 'Noto Serif SC', Georgia, serif;
  font-size: clamp(40px, 4.6vw, 72px);
  font-weight: 500;
  /* Playfair 的 J/y/g 降部会探出字身；0.95 + overflow:hidden 会把它们剪半 */
  line-height: 1.15;
  padding: 0.08em 0;
  letter-spacing: 0.04em;
  margin: 0 0 4px;
  color: #fff;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mag-name--xl {
  font-size: clamp(56px, 7vw, 92px);
  font-weight: 400;
  letter-spacing: 0.06em;
}

.mag-name--long {
  font-size: clamp(28px, 3.6vw, 48px);
  letter-spacing: 0.01em;
  white-space: normal;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.mag-sub {
  font-family: 'Playfair Display', 'Iowan Old Style', Palatino, 'Songti SC',
    'Noto Serif SC', Georgia, serif;
  font-size: clamp(16px, 1.5vw, 22px);
  font-weight: 400;
  color: rgba(255, 255, 255, 0.88);
  margin: 0 0 28px;
  line-height: 1.35;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mag-cta {
  pointer-events: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 21px;
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 999px;
  background: transparent;
  color: #fff;
  font-family: inherit;
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.2;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.mag-cta:hover,
.mag-cta:focus-visible {
  background: #fff;
  color: #111;
  border-color: #fff;
  outline: none;
}

.mag-social {
  pointer-events: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  margin-top: 14px;
}

.mag-social-link {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  color: rgba(255, 255, 255, 0.55);
  border-radius: 999px;
  text-decoration: none;
  transition: color 0.15s ease, background 0.15s ease;
}

.mag-social-link:hover,
.mag-social-link:focus-visible {
  color: #fff;
  background: rgba(255, 255, 255, 0.1);
  outline: none;
}

.mag-dots {
  position: absolute;
  right: 0;
  width: 38%;
  bottom: 16px;
  z-index: 3;
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 8px;
  pointer-events: none;
}

.mag-dot {
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

.mag-dot--on {
  width: 18px;
  background: #fff;
}

.mag-dot:hover:not(.mag-dot--on) {
  background: rgba(255, 255, 255, 0.62);
}

.mag-cross-enter-active,
.mag-cross-leave-active {
  transition: opacity 320ms ease;
}
.mag-cross-enter-from,
.mag-cross-leave-to {
  opacity: 0;
}
.mag-reduced .mag-cross-enter-active,
.mag-reduced .mag-cross-leave-active {
  transition: opacity 180ms ease;
}

@media (max-width: 1100px) {
  .mag-copy,
  .mag-dots {
    width: 42%;
  }
  .mag-img {
    width: 64%;
  }
  /* 照片宽度变 64% → 渐变落点同步（62/84/100% × 64%） */
  .mag-scrim {
    background: linear-gradient(
      to right,
      rgba(0, 0, 0, 0) 0%,
      rgba(0, 0, 0, 0) 39.68%,
      rgba(var(--mag-panel), 0.55) 53.76%,
      rgb(var(--mag-panel)) 64%,
      rgb(var(--mag-panel)) 100%
    );
  }
  .mag-copy {
    padding: 22px 24px 44px 8px;
  }
  .mag-name--xl {
    font-size: clamp(48px, 6.4vw, 76px);
  }
}

@media (max-width: 900px) {
  .mag-card {
    aspect-ratio: 16 / 7;
    max-height: 360px;
  }
  .mag-copy,
  .mag-dots {
    width: 44%;
  }
  .mag-copy {
    padding: 18px 20px 40px 8px;
  }
  .mag-cta {
    padding: 9px 18px;
    font-size: 12.5px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .mag-cta {
    transition: none;
  }
}
</style>
