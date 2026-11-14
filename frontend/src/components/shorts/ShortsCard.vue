<script setup lang="ts">
/**
 * 短视频卡片（复刻 gemini-code 样板的 9:16 画框）
 *
 * 结构：9:16 画框（封面 + 悬停浮现的三角功能胶囊）→ 两行标题 → 等宽参数行（时长 ● 清晰度 ● 日期）。
 * 动效：悬停上浮 2px / 描边提亮 / 封面 1.03 缓放；胶囊从下往上 6px 弹入（0.24s spring）。
 * 选中态（沉浸分屏正在播放的那张）：描边 + 1.5px 外环。
 */
import { computed } from 'vue'
import type { MusicVideo } from '@/types/models'
import { musicVideosApi } from '@/api/musicVideos'
import { formatDate, formatDuration } from '@/utils/format'

const props = defineProps<{
  video: MusicVideo
  /** 沉浸分屏里当前正在播放的那一张 */
  active?: boolean
}>()

const emit = defineEmits<{
  open: []
  next: []
  artist: [uid: string]
}>()

const poster = computed(() =>
  musicVideosApi.thumbnailUrl(props.video.id, props.video.file_hash || props.video.file_size, 640),
)

const durationLabel = computed(() => formatDuration(props.video.duration))

// 清晰度角标：按短边口径，和库里转码决策保持一致
const resLabel = computed(() => {
  const h = props.video.height ?? 0
  const w = props.video.width ?? 0
  const shortSide = Math.min(h, w)
  if (!shortSide) return ''
  if (shortSide >= 2160) return '4K'
  if (shortSide >= 1440) return '2K'
  if (shortSide >= 1080) return '1080P'
  return '720P'
})

const dateLabel = computed(() => {
  const d = formatDate(props.video.performance_date || props.video.release_date)
  return d === '--' ? '' : d
})

const artistUid = computed(() => props.video.artists?.[0]?.uid ?? '')
</script>

<template>
  <article
    class="shorts-card"
    :class="{ 'is-active': active }"
    role="button"
    tabindex="0"
    @click="emit('open')"
    @keydown.enter.prevent="emit('open')"
    @keydown.space.prevent="emit('open')"
  >
    <div class="shorts-frame">
      <img class="frame-img" :src="poster" alt="" loading="lazy" decoding="async" />

      <!-- 悬停浮现的功能胶囊 -->
      <div class="hover-dock" @click.stop>
        <button
          v-if="artistUid"
          class="dock-btn"
          type="button"
          title="艺人档案"
          @click="emit('artist', artistUid)"
        >
          👤
        </button>
        <button class="dock-btn" type="button" title="下一条" @click="emit('next')">↺</button>
        <button class="dock-btn dock-btn--play" type="button" title="展开播放" @click="emit('open')">
          ▶
        </button>
      </div>
    </div>

    <h3 class="card-title" :title="video.name">{{ video.name }}</h3>

    <div class="card-foot">
      <span class="foot-dur">{{ durationLabel }}</span>
      <div class="foot-right">
        <span v-if="resLabel" class="foot-dot">●</span>
        <span v-if="resLabel" class="foot-res">{{ resLabel }}</span>
        <template v-if="dateLabel">
          <span class="foot-dot">●</span>
          <span>{{ dateLabel }}</span>
        </template>
      </div>
    </div>
  </article>
</template>

<style scoped>
.shorts-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  cursor: pointer;
}
.shorts-frame {
  position: relative;
  aspect-ratio: 9 / 16;
  border-radius: 14px;
  overflow: hidden;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  transition:
    transform 0.28s cubic-bezier(0.16, 1, 0.3, 1),
    box-shadow 0.28s cubic-bezier(0.16, 1, 0.3, 1),
    border-color 0.28s ease;
}
.shorts-card:hover .shorts-frame {
  transform: translateY(-2px);
  border-color: var(--sa-border);
  box-shadow: 0 14px 30px -8px rgba(0, 0, 0, 0.35);
}
.shorts-card.is-active .shorts-frame {
  border-color: var(--sa-text-primary);
  box-shadow: 0 0 0 1.5px var(--sa-text-primary);
}
.frame-img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}
.shorts-card:hover .frame-img {
  transform: scale(1.03);
}

/* 悬停胶囊：从下往上 6px 弹入 */
.hover-dock {
  position: absolute;
  left: 50%;
  bottom: 14px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 9999px;
  background: rgba(0, 0, 0, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.2);
  backdrop-filter: blur(12px);
  opacity: 0;
  transform: translate(-50%, 6px) scale(0.96);
  transition:
    opacity 0.2s ease,
    transform 0.24s cubic-bezier(0.16, 1, 0.3, 1);
  pointer-events: none;
}
.shorts-card:hover .hover-dock,
.shorts-card:focus-visible .hover-dock {
  opacity: 1;
  transform: translate(-50%, 0) scale(1);
  pointer-events: auto;
}
.dock-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 9999px;
  background: transparent;
  color: #fff;
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
  transition: background-color 0.15s ease, transform 0.15s ease;
}
.dock-btn:hover {
  background: rgba(255, 255, 255, 0.2);
}
.dock-btn--play {
  background: #fff;
  color: #0b0b0f;
  font-size: 9px;
  font-weight: 700;
}
.dock-btn--play:hover {
  transform: scale(1.06);
  background: #fff;
}

.card-title {
  margin: 10px 2px 0;
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.45;
  color: var(--sa-text-primary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  transition: color 0.18s ease;
}
.shorts-card:hover .card-title {
  color: var(--sa-accent);
}
.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--sa-border-subtle);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--sa-text-tertiary);
}
.foot-dur {
  color: var(--sa-text-secondary);
  font-weight: 500;
}
.foot-right {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}
.foot-res {
  font-size: 10px;
  font-weight: 600;
  color: var(--sa-text-secondary);
}
.foot-dot {
  font-size: 8px;
  opacity: 0.5;
}
</style>
