<script setup lang="ts">
import { watch } from 'vue'
import SaSelect from '@/components/SaSelect.vue'
import type { SaOption } from '@/components/SaSelect.vue'
import RemoteSongSelect from '@/components/RemoteSongSelect.vue'
import { albumsApi } from '@/api/albums'
import { AddOutlined, DeleteOutlined } from '@/components/icons'

export interface TrackRow {
  key: string
  song_id: number | null
  album_ids: number[]
}

const props = withDefaults(
  defineProps<{
    tracks: TrackRow[]
    albumPresets?: SaOption[]
    allowCreateSong?: boolean
    allowCreateAlbum?: boolean
    /** 专辑远程搜索；第二参 = 所在曲目行的歌曲 id，用于「组合/艺人 - 专辑 - 歌曲」提示 */
    searchAlbums: (q: string, songId?: number | null) => Promise<SaOption[]>
    /** 专辑框占位文案（支持按歌名搜索的调用方写明「专辑名或歌名」） */
    albumPlaceholder?: string
  }>(),
  {
    albumPresets: () => [],
    allowCreateSong: true,
    allowCreateAlbum: true,
    albumPlaceholder: '这首歌所属专辑，可多选',
  },
)

const emit = defineEmits<{
  (e: 'update:tracks', value: TrackRow[]): void
  (e: 'create-album', rowIndex: number): void
}>()

function newKey(): string {
  return `t-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function patch(index: number, patchRow: Partial<TrackRow>) {
  const next = props.tracks.map((row, i) => (i === index ? { ...row, ...patchRow } : row))
  emit('update:tracks', next)
}

async function onSongChange(index: number, songId: number | null) {
  if (!songId) {
    patch(index, { song_id: null, album_ids: [] })
    return
  }
  let albumIds = [...(props.tracks[index]?.album_ids || [])]
  try {
    const albums = await albumsApi.songAlbums(songId)
    const existing = new Set(albumIds)
    for (const a of albums) {
      if (!existing.has(a.id)) albumIds.push(a.id)
    }
  } catch {
    /* 没有已关联专辑时保持空，让用户选 */
  }
  patch(index, { song_id: songId, album_ids: albumIds })
}

function addRow() {
  emit('update:tracks', [...props.tracks, { key: newKey(), song_id: null, album_ids: [] }])
}

function removeRow(index: number) {
  const next = props.tracks.filter((_, i) => i !== index)
  emit('update:tracks', next.length ? next : [{ key: newKey(), song_id: null, album_ids: [] }])
}

watch(
  () => props.tracks,
  (rows) => {
    if (!rows.length) {
      emit('update:tracks', [{ key: newKey(), song_id: null, album_ids: [] }])
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="track-list">
    <div v-for="(row, index) in tracks" :key="row.key" class="track-row">
      <div class="track-row-head">
        <span class="track-index">曲目 {{ index + 1 }}</span>
        <button class="track-remove" type="button" title="移除此曲" @click="removeRow(index)">
          <DeleteOutlined :size="14" />
        </button>
      </div>
      <div class="track-fields">
        <div class="track-field">
          <span class="track-label">歌曲</span>
          <RemoteSongSelect
            :model-value="row.song_id"
            :allow-create="allowCreateSong"
            placeholder="搜索歌曲名 / 别名 / 所属专辑"
            @update:model-value="(id) => onSongChange(index, id)"
          />
        </div>
        <div class="track-field">
          <span class="track-label">专辑</span>
          <SaSelect
            :model-value="row.album_ids"
            :fetch-options="(q) => searchAlbums(q, row.song_id)"
            :preset-options="albumPresets"
            :placeholder="albumPlaceholder"
            class="album-select"
            multiple
            @update:model-value="(ids) => patch(index, { album_ids: ids })"
          >
            <template v-if="allowCreateAlbum || $slots['album-action']" #action>
              <slot name="album-action" :index="index">
                <button class="track-add-album" type="button" @click="emit('create-album', index)">
                  新建专辑
                </button>
              </slot>
            </template>
          </SaSelect>
        </div>
      </div>
    </div>
    <button class="track-add" type="button" @click="addRow">
      <AddOutlined :size="14" />
      再加一首歌
    </button>
  </div>
</template>

<style scoped>
.track-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}
.track-row {
  padding: 10px 12px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 10px;
  background: var(--sa-hover);
}
.track-row-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.track-index {
  font-size: 12px;
  font-weight: 600;
  color: var(--sa-text-secondary);
}
.track-remove {
  border: none;
  background: transparent;
  color: var(--sa-text-tertiary);
  cursor: pointer;
  padding: 2px;
  display: inline-flex;
}
.track-remove:hover {
  color: var(--sa-danger, #e5484d);
}
.track-fields {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.track-label {
  display: block;
  font-size: 12px;
  color: var(--sa-text-tertiary);
  margin-bottom: 4px;
}
.track-add {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  height: 32px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px dashed var(--sa-border);
  background: transparent;
  color: var(--sa-text-secondary);
  cursor: pointer;
  font-size: 13px;
}
.track-add:hover {
  border-color: var(--sa-accent-border);
  color: var(--sa-accent);
}
.track-add-album {
  border: none;
  background: transparent;
  color: var(--sa-accent);
  cursor: pointer;
  font-size: 12px;
  padding: 0 4px;
}
</style>
