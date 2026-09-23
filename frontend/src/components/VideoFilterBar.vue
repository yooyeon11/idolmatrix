<script setup lang="ts">
import { computed } from 'vue'
import type { DropdownOption } from 'naive-ui'
import SaSelect from '@/components/SaSelect.vue'
import { MusicNoteOutlined, SortOutlined } from '@/components/icons'
import type { VideoSongOption, VideoSortKey } from '@/composables/useEntityDetail'
import { VIDEO_SORT_OPTIONS } from '@/composables/useEntityDetail'

/**
 * 艺人/组合详情页视频区工具栏（与照片墙 photo-toolbar 同口径）：
 * 左侧数量徽标「N 个」，右侧歌曲筛选 + 搜索 + 排序。
 * 桌面端：SaSelect 下拉；移动端（≤768px）：歌曲/排序收成图标按钮（n-dropdown，
 * 与浏览页 head-icon 同款），搜索保持输入框。
 */
const props = defineProps<{
  /** 筛选后的展示数量 */
  count: number
  /** 歌曲筛选候选（当前实体视频里出现过的歌曲） */
  songs: VideoSongOption[]
  songId: number | null
  sort: VideoSortKey
  query: string
}>()

const emit = defineEmits<{
  (e: 'update:songId', v: number | null): void
  (e: 'update:sort', v: VideoSortKey): void
  (e: 'update:query', v: string): void
}>()

const sortOptions = VIDEO_SORT_OPTIONS.map((o) => ({ label: o.label, value: o.key }))

/* ---- 移动端图标下拉 ---- */
const songDropdownOptions = computed<DropdownOption[]>(() => [
  { label: props.songId == null ? '✓ 全部歌曲' : '全部歌曲', key: 'all' },
  ...props.songs.map((s) => ({
    label: props.songId === s.value ? `✓ ${s.label}` : s.label,
    key: String(s.value),
  })),
])

const sortDropdownOptions = computed<DropdownOption[]>(() =>
  VIDEO_SORT_OPTIONS.map((o) => ({
    label: props.sort === o.key ? `✓ ${o.label}` : o.label,
    key: o.key,
  })),
)

const sortLabel = computed(
  () => VIDEO_SORT_OPTIONS.find((o) => o.key === props.sort)?.label ?? '排序',
)
const songLabel = computed(() =>
  props.songId == null
    ? '按歌曲筛选'
    : (props.songs.find((s) => s.value === props.songId)?.label ?? '按歌曲筛选'),
)

function onSongSelect(key: string | number) {
  emit('update:songId', key === 'all' ? null : Number(key))
}
</script>

<template>
  <div class="video-toolbar">
    <div class="video-toolbar-meta">{{ count }} 个</div>
    <div class="video-toolbar-filters">
      <SaSelect
        class="video-toolbar-song"
        :model-value="songId"
        :options="songs"
        placeholder="按歌曲筛选"
        clearable
        filterable
        @update:model-value="emit('update:songId', $event as number | null)"
      />
      <div class="video-toolbar-search">
        <input
          :value="query"
          class="video-toolbar-search-input"
          type="text"
          placeholder="搜标题 / 歌曲 / 艺人"
          maxlength="100"
          @input="emit('update:query', ($event.target as HTMLInputElement).value)"
        />
        <button
          v-if="query"
          class="video-toolbar-search-clear"
          type="button"
          aria-label="清空搜索"
          @click="emit('update:query', '')"
        >×</button>
      </div>
      <SaSelect
        class="video-toolbar-sort"
        :model-value="sort"
        :options="sortOptions"
        placeholder="排序"
        :clearable="false"
        @update:model-value="emit('update:sort', $event as VideoSortKey)"
      />
      <!-- 移动端：歌曲筛选 / 排序收成图标下拉（浏览页 head-icon 同款） -->
      <div class="video-toolbar-icons">
        <n-dropdown
          :options="songDropdownOptions"
          trigger="click"
          placement="bottom-end"
          @select="onSongSelect"
        >
          <button
            class="video-toolbar-icon"
            type="button"
            :title="songLabel"
            :aria-label="songLabel"
          >
            <MusicNoteOutlined :size="16" />
          </button>
        </n-dropdown>
        <n-dropdown
          :options="sortDropdownOptions"
          trigger="click"
          placement="bottom-end"
          @select="emit('update:sort', $event as VideoSortKey)"
        >
          <button
            class="video-toolbar-icon"
            type="button"
            :title="`排序：${sortLabel}`"
            :aria-label="`排序：${sortLabel}`"
          >
            <SortOutlined :size="16" />
          </button>
        </n-dropdown>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 与照片墙 photo-toolbar 同口径：数量徽标居左、筛选控件居右、可换行 */
.video-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
  margin: 0 0 14px;
}
.video-toolbar-meta {
  margin: 0;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.video-toolbar-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.video-toolbar-song {
  width: 190px;
}
.video-toolbar-sort {
  width: 130px;
}
.video-toolbar-search {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.video-toolbar-search-input {
  height: 36px;
  width: 200px;
  padding: 0 26px 0 12px;
  border: 1px solid var(--sa-border-subtle, var(--sa-border));
  border-radius: 8px;
  background: var(--sa-elevated, var(--sa-bg));
  color: var(--sa-text-primary);
  font-size: 13px;
}
.video-toolbar-search-input::placeholder {
  color: var(--sa-text-tertiary);
}
.video-toolbar-search-input:focus {
  outline: none;
  border-color: var(--sa-accent);
}
.video-toolbar-search-clear {
  position: absolute;
  right: 7px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: 0;
  border-radius: 9999px;
  background: var(--sa-hover);
  color: var(--sa-text-secondary);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}
/* 图标按钮（移动端才显示），高度与搜索框一致 */
.video-toolbar-icons {
  display: none;
}
.video-toolbar-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s, background 0.2s;
}
.video-toolbar-icon:hover {
  color: var(--sa-text-primary);
}
@media (max-width: 768px) {
  /* 手机端：数量徽标 + 搜索 + 图标同一行，不换行 */
  .video-toolbar {
    flex-wrap: nowrap;
  }
  .video-toolbar-meta {
    flex: none;
    white-space: nowrap;
  }
  .video-toolbar-filters {
    width: auto;
    flex: 1;
    min-width: 0;
    flex-wrap: nowrap;
  }
  /* 歌曲/排序下拉收成图标 */
  .video-toolbar-song,
  .video-toolbar-sort {
    display: none;
  }
  .video-toolbar-icons {
    display: flex;
    align-items: center;
    gap: 6px;
    order: 2;
    flex: none;
  }
  .video-toolbar-search {
    flex: 1 1 auto;
    min-width: 0;
    order: 1;
  }
  .video-toolbar-search-input {
    width: 100%;
  }
}
</style>
