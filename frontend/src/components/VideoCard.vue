<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { MovieOutlined } from '@/components/icons'
import { musicVideosApi } from '@/api/musicVideos'
import { useSettingsStore } from '@/stores/settings'
import { formatDuration, formatDate } from '@/utils/format'
import { videoPath } from '@/utils/routes'
import { IMG_W_CARD } from '@/utils/imageSizes'
import { VIDEO_TYPE_LABEL, type MusicVideo } from '@/types/models'

const props = defineProps<{
  video: MusicVideo
  index?: number
  portrait?: boolean
  /** 隐藏类型徽标：短视频列表等已能表达类型的场景，避免「个人直拍」标签重复 */
  hideType?: boolean
}>()

const router = useRouter()
const failed = ref<Set<number>>(new Set())
const settings = useSettingsStore()

const palettes: [string, string][] = [
  ['#f472b6', '#8b5cf6'],
  ['#fb923c', '#ef4444'],
  ['#22d3ee', '#3b82f6'],
  ['#a3e635', '#16a34a'],
  ['#facc15', '#f97316'],
  ['#e879f9', '#6366f1'],
]

function coverStyle(i: number) {
  const [c1, c2] = palettes[(i + (props.index || 0)) % palettes.length]
  return { background: `linear-gradient(135deg, ${c1}, ${c2})` }
}

function onImgError(id: number) {
  failed.value.add(id)
  failed.value = new Set(failed.value)
}

function go(e: MouseEvent) {
  // 带修饰键/非左键的点击交还浏览器（新标签页打开），普通左键才走站内路由
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return
  e.preventDefault()
  router.push(videoPath(props.video.uid))
}
</script>

<template>
  <a
    class="video-card"
    :class="{ 'video-card--portrait': portrait }"
    :href="videoPath(props.video.uid)"
    @click="go"
  >
    <div class="card-cover">
      <img
        v-if="settings.showCovers && !failed.has(video.id)"
        :src="musicVideosApi.thumbnailUrl(video.id, video.file_hash || video.file_size, IMG_W_CARD)"
        alt=""
        loading="lazy"
        @error="onImgError(video.id)"
      />
      <div v-else class="cover-placeholder" :style="coverStyle(video.id)">
        <MovieOutlined :size="26" />
      </div>
      <span v-if="video.duration" class="card-duration">{{ formatDuration(video.duration) }}</span>
    </div>
    <div class="card-title">{{ video.name }}</div>
    <div class="card-footer">
      <span v-if="!hideType" class="badge">{{ VIDEO_TYPE_LABEL[video.video_type] || video.video_type }}</span>
      <span v-if="video.performance_date || video.release_date" class="card-date">
        {{ formatDate(video.performance_date || video.release_date) }}
      </span>
    </div>
  </a>
</template>

<style scoped>
.video-card {
  display: block;
  cursor: pointer;
  width: var(--video-card-width, 224px);
  flex-shrink: 0;
  text-decoration: none;
  color: inherit;
}
.card-cover {
  position: relative;
  aspect-ratio: 16 / 10;
  border-radius: 12px;
  overflow: hidden;
  background: var(--sa-subtle);
}
.video-card--portrait {
  width: 100%;
}
.video-card--portrait .card-cover {
  aspect-ratio: 9 / 16;
}
.card-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.3s;
}
.video-card:hover .card-cover img {
  transform: scale(1.05);
}
.cover-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.75);
}
.card-duration {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  line-height: 1.6;
}
.card-title {
  margin-top: 10px;
  font-size: 14px;
  font-weight: 500;
  color: var(--sa-text-primary);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-footer {
  margin-top: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
}
.badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 8px;
  border-radius: 9999px;
  font-size: 11px;
  font-weight: 500;
  color: var(--sa-accent);
  background: var(--sa-accent-subtle);
  /* 徽标文字不允许折行（中文任意字符处都可能断行，窄卡会一字一行竖排） */
  white-space: nowrap;
  flex-shrink: 0;
  min-width: 0;
}
.card-date {
  color: var(--sa-text-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
/* 手机端竖屏窄卡片放不下类型标签（“个人直拍”会被挤成一字一行竖排），不展示类型 */
@media (max-width: 640px) {
  .video-card--portrait .badge {
    display: none;
  }
}
</style>
