<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { HeroStageItem } from '@/api/home'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'
import { withImageWidth, heroImageWidth } from '@/utils/imageSizes'
import { nameSizeTier } from '@/utils/heroTitle'
import ThemeToggle from '@/components/ThemeToggle.vue'

const props = defineProps<{
  pool: HeroStageItem[]
  index: number
  failed?: boolean
  /** false = 轮动由父级统一驱动（首页左右联动），本组件不计时 */
  auto?: boolean
}>()

const emit = defineEmits<{
  'update:index': [value: number]
  open: [item: HeroStageItem]
  retry: []
}>()

// ===== 焦点轮播：定时自动切换，悬停/触摸暂停，手动切换后重置计时 =====
const ROTATE_MS = 6000
const featured = computed(() => props.pool[props.index] ?? null)
const hovered = ref(false)
const broken = ref(false)
const retry = ref(0)
let timer: ReturnType<typeof setTimeout> | undefined
let reducedMotion = false

watch(
  featured,
  () => {
    broken.value = false
    retry.value = 0
  },
)

function schedule() {
  clearTimeout(timer)
  // 父级接管时（auto=false）完全不计时，避免与父级时钟叠加成双倍速度
  if (props.auto === false) return
  if (props.pool.length < 2 || reducedMotion) return
  timer = setTimeout(() => {
    if (!hovered.value && !document.hidden) go(props.index + 1)
    schedule()
  }, ROTATE_MS)
}

watch(() => props.pool.length, schedule)
watch(() => props.auto, schedule)

function go(i: number) {
  if (!props.pool.length) return
  emit('update:index', (i + props.pool.length) % props.pool.length)
  schedule()
}

onMounted(() => {
  reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  schedule()
})
onBeforeUnmount(() => clearTimeout(timer))

// ===== 展示信息 =====
/** 封面用最短标志名：IU / IRENE / IVE，与旧刊头同一口径 */
function displayName(item: HeroStageItem) {
  const stage = (item.stage_name || '').trim()
  if (stage) return stage
  const name = (item.name || '').trim()
  if (name) return name
  return (item.chinese_name || '').trim()
}

const featuredNameClass = computed(() => {
  const tier = nameSizeTier(featured.value ? displayName(featured.value) : '')
  return tier === 'base' ? '' : `spot-name--${tier}`
})

/** 左列为竖版通顶海报：头像（竖构图）优先，横幅兜底 */

/** 预加载下一张海报：避免切轮动时白屏/顿挫 */
function preloadNext() {
  if (props.pool.length < 2) return
  const next = props.pool[(props.index + 1) % props.pool.length]
  const url = next.has_avatar
    ? (next.type === 'artist' ? artistsApi.avatarUrl(next.id) : groupsApi.avatarUrl(next.id))
    : ''
  if (!url) return
  const img = new Image()
  img.decoding = 'async'
  img.src = withImageWidth(`${url}?t=${Date.parse(next.updated_at || '') || 0}`, heroImageWidth())
}

watch(
  () => props.index,
  () => {
    preloadNext()
  },
)
onMounted(preloadNext)

function imgUrl(item: HeroStageItem) {
  if (broken.value) return ''
  const bust = `?t=${Date.parse(item.updated_at || '') || 0}&r=${retry.value}`
  const isArtist = item.type === 'artist'
  let url = ''
  if (item.has_avatar) {
    url = isArtist ? artistsApi.avatarUrl(item.id) : groupsApi.avatarUrl(item.id)
  } else if (item.has_banner) {
    url = isArtist ? artistsApi.bannerUrl(item.id) : groupsApi.bannerUrl(item.id)
  }
  return url ? withImageWidth(url + bust, heroImageWidth()) : ''
}

function imgStyle(item: HeroStageItem) {
  const x = item.focus_x
  const y = item.focus_y
  return {
    objectPosition: `${((x ?? 0.5) * 100).toFixed(1)}% ${((y ?? 0.32) * 100).toFixed(1)}%`,
  }
}

function onImgError() {
  if (retry.value < 1) retry.value += 1
  else broken.value = true
}

const initial = computed(() =>
  ((featured.value ? displayName(featured.value) : '') || '?').trim().charAt(0).toUpperCase(),
)

// ===== 触摸滑动切换（横向交给轮播，纵向留给页面滚动）=====
let touchX = 0
let touchY = 0
function onTouchStart(e: TouchEvent) {
  touchX = e.touches[0].clientX
  touchY = e.touches[0].clientY
  hovered.value = true
}
function onTouchEnd(e: TouchEvent) {
  hovered.value = false
  const dx = e.changedTouches[0].clientX - touchX
  const dy = e.changedTouches[0].clientY - touchY
  if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy)) {
    go(dx < 0 ? props.index + 1 : props.index - 1)
  }
}
</script>

<template>
  <div
    class="spot"
    @mouseenter="hovered = true"
    @mouseleave="hovered = false"
    @touchstart.passive="onTouchStart"
    @touchend.passive="onTouchEnd"
  >
    <template v-if="featured">
      <img
        v-if="imgUrl(featured)"
        :key="featured.id"
        :src="imgUrl(featured)"
        :style="imgStyle(featured)"
        class="spot-img"
        alt=""
        @error="onImgError"
      />
      <div v-else class="spot-img spot-img--placeholder">{{ initial }}</div>
      <div class="spot-shade" />

      <!-- 左上角品牌烙印；移动端把主题切换收进海报右上角，
           避免隐藏重复导航行时连主题入口一起丢掉 -->
      <div class="spot-top">
        <span class="spot-brand">IDOLMATRIX.</span>
        <div class="spot-theme">
          <ThemeToggle />
        </div>
      </div>

      <!-- 左下角巨幅海报信息 -->
      <div :key="featured.id" class="spot-bottom">
        <h1 class="spot-name" :class="featuredNameClass">{{ displayName(featured) }}</h1>
        <p v-if="featured.description" class="spot-desc">
          {{ featured.description }}
        </p>
        <div class="spot-actions">
          <button class="spot-cta" type="button" @click="emit('open', featured)">
            进入{{ featured.type === 'group' ? '组合' : '艺人' }}档案 →
          </button>
          <div v-if="pool.length > 1" class="spot-dots" role="tablist" aria-label="切换焦点海报">
            <button
              v-for="(p, i) in pool"
              :key="p.id"
              class="spot-dot"
              :class="{ 'is-on': i === index }"
              type="button"
              role="tab"
              :aria-selected="i === index"
              :aria-label="`切换到第 ${i + 1} 张焦点海报`"
              @click="go(i)"
            />
          </div>
        </div>
      </div>
    </template>

    <!-- 无焦点数据：失败给重试，否则给空态 -->
    <div v-else class="spot-empty">
      <span class="spot-empty-brand">IDOLMATRIX.</span>
      <p v-if="failed" class="spot-empty-text">
        焦点海报加载失败
        <button class="spot-empty-retry" type="button" @click="emit('retry')">重试</button>
      </p>
      <p v-else-if="!pool.length" class="spot-empty-text">暂无焦点艺人/组合</p>
    </div>
  </div>
</template>

<style scoped>
/* 封面叠在照片上的文字不受站点主题影响（图片已压暗），用局部变量收口 */
.spot {
  --hc-fg: #ffffff;
  --hc-sub: rgba(255, 255, 255, 0.78);
  --hc-faint: rgba(255, 255, 255, 0.55);
  --hc-glass: rgba(255, 255, 255, 0.14);
  --hc-line: rgba(255, 255, 255, 0.24);
  --hc-serif: 'Playfair Display', 'Noto Serif SC', 'Songti SC', 'SimSun', serif;
  --spring: cubic-bezier(0.22, 1, 0.36, 1);

  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #101014;
  color: var(--hc-fg);
}

.spot-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  animation: spot-img-in 0.6s var(--spring) both;
  transition: transform 0.75s var(--spring);
}
.spot:hover .spot-img {
  transform: scale(1.05);
}
@keyframes spot-img-in {
  from {
    opacity: 0;
    transform: scale(1.04);
  }
  to {
    opacity: 1;
  }
}
.spot-img--placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--hc-serif);
  font-size: 96px;
  font-weight: 700;
  color: var(--hc-faint);
  background: linear-gradient(160deg, #23232c 0%, #101014 100%);
}

/* 底部强压暗 + 顶部轻压暗，保证两个角落的文字可读 */
.spot-shade {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to top,
    rgba(0, 0, 0, 0.94) 0%,
    rgba(0, 0, 0, 0.45) 34%,
    rgba(0, 0, 0, 0.12) 58%,
    rgba(0, 0, 0, 0.42) 100%
  );
  pointer-events: none;
}

.spot-top {
  position: absolute;
  top: 32px;
  left: 36px;
  right: 36px;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.spot-brand {
  font-family: var(--hc-serif);
  font-size: 22px;
  font-weight: 900;
  letter-spacing: -0.02em;
}
/* 桌面端主题入口仍在首页导航行，海报上不重复显示 */
.spot-theme {
  display: none;
}
.spot-theme :deep(.theme-toggle) {
  color: rgba(255, 255, 255, 0.78);
}
.spot-theme :deep(.theme-toggle:hover) {
  color: #ffffff;
}

.spot-bottom {
  position: absolute;
  left: 36px;
  right: 36px;
  bottom: 36px;
  z-index: 2;
  animation: spot-rise 0.55s var(--spring) both;
}
@keyframes spot-rise {
  from {
    opacity: 0;
    transform: translateY(14px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.spot-name {
  margin: 0 0 10px;
  font-family: var(--hc-serif);
  font-size: 44px;
  font-weight: 700;
  line-height: 1.05;
  letter-spacing: -0.01em;
  text-shadow: 0 2px 24px rgba(0, 0, 0, 0.35);
}
.spot-name--xl {
  font-size: 56px;
}
.spot-name--long {
  font-size: 34px;
}

.spot-desc {
  margin: 0 0 22px;
  max-width: 30em;
  font-size: 13px;
  line-height: 1.7;
  color: var(--hc-sub);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.spot-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.spot-cta {
  padding: 12px 22px;
  border: none;
  border-radius: 999px;
  background: #ffffff;
  color: #0a0a0c;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition:
    transform 0.3s var(--spring),
    box-shadow 0.3s ease;
}
.spot-cta:hover {
  transform: scale(1.04);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
}
/* 焦点海报切换：光点（替代原来的文字按钮） */
.spot-dots {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 2px;
}
.spot-dot {
  width: 7px;
  height: 7px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.42);
  cursor: pointer;
  transition:
    width 0.24s var(--spring, cubic-bezier(0.16, 1, 0.3, 1)),
    background-color 0.24s ease;
}
.spot-dot:hover {
  background: rgba(255, 255, 255, 0.72);
}
.spot-dot.is-on {
  width: 18px;
  background: #fff;
}

.spot-switch {
  padding: 12px 18px;
  border-radius: 999px;
  border: 1px solid var(--hc-line);
  background: var(--hc-glass);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  color: var(--hc-fg);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition:
    background 0.25s ease,
    transform 0.3s var(--spring);
}
.spot-switch:hover {
  background: rgba(255, 255, 255, 0.24);
  transform: scale(1.03);
}

.spot-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  background: linear-gradient(160deg, #1b1b22 0%, #101014 100%);
}
.spot-empty-brand {
  font-family: var(--hc-serif);
  font-size: 28px;
  font-weight: 900;
  color: var(--hc-faint);
}
.spot-empty-text {
  margin: 0;
  font-size: 13px;
  color: var(--hc-sub);
}
.spot-empty-retry {
  margin-left: 8px;
  padding: 4px 14px;
  border-radius: 999px;
  border: 1px solid var(--hc-line);
  background: var(--hc-glass);
  color: var(--hc-fg);
  font-size: 12px;
  cursor: pointer;
}

@media (max-width: 768px) {
  .spot-top {
    top: 20px;
    left: 20px;
    right: 20px;
  }
  .spot-theme {
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .spot-bottom {
    left: 20px;
    right: 20px;
    bottom: 22px;
  }
  .spot-brand {
    font-size: 18px;
  }
  .spot-name {
    font-size: 36px;
  }
  .spot-name--xl {
    font-size: 44px;
  }
  .spot-name--long {
    font-size: 28px;
  }
  .spot-desc {
    -webkit-line-clamp: 2;
    margin-bottom: 16px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .spot-img,
  .spot-bottom {
    animation: none;
  }
  .spot-img,
  .spot-cta,
  .spot-switch {
    transition: none;
  }
  .spot:hover .spot-img {
    transform: none;
  }
}
</style>
