<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useDialog, useMessage } from 'naive-ui'
import SaSelect from '@/components/SaSelect.vue'
import { photosApi } from '@/api/photos'
import { useSettingsStore } from '@/stores/settings'
import type {
  MtPhotosAlbum,
  PhotoOwnerType,
  PhotoScanResult,
  PhotoSection,
  PhotoSource,
} from '@/types/models'
import { formatDateTime } from '@/utils/format'
import { ChevronRightOutlined, FolderOutlined, RefreshOutlined } from '@/components/icons'
import FolderBrowserModal from '@/components/FolderBrowserModal.vue'

const props = defineProps<{
  ownerType: PhotoOwnerType
  ownerId: number
}>()

const message = useMessage()
const dialog = useDialog()
const settings = useSettingsStore()
const sources = ref<PhotoSource[]>([])
const loading = ref(false)
const saving = ref(false)
const scanningId = ref<number | null>(null)
const unbindingId = ref<number | null>(null)
const section = ref<PhotoSection>('official')
const albums = ref<MtPhotosAlbum[]>([])
const albumId = ref<number | null>(null)
const albumsLoading = ref(false)
const bindMode = ref<'album' | 'folder'>('album')
const browserShow = ref(false)

const mtReady = computed(
  () =>
    settings.mtphotos.enabled &&
    !!(settings.mtphotos.base_url || '').trim() &&
    (!!(settings.mtphotos.api_key || '').trim() || !!settings.mtphotos.api_key_set),
)
const isWall = computed(() => section.value === 'wall')
const useMtAlbum = computed(() => isWall.value || bindMode.value === 'album')
const browserMode = computed(() => 'mt' as const)
const bindModeOptions = [
  { label: 'MT 相册', value: 'album' },
  { label: 'MT 文件夹', value: 'folder' },
]

const sectionOptions = [
  { label: '官方', value: 'official' },
  { label: '粉丝', value: 'fan' },
  { label: '照片墙', value: 'wall' },
]

const sectionLabel: Record<string, string> = {
  official: '官方',
  fan: '粉丝',
  wall: '照片墙',
}

// 相册已绑定标记（与后端查重粒度一致：owner 级）
const boundAlbumIds = computed(
  () =>
    new Set(
      sources.value
        .filter(
          (s) =>
            s.provider === 'mtphotos' && !(s.folder_path || '').includes('/folder/'),
        )
        .map((s) => String(s.external_id)),
    ),
)
const albumOptions = computed(() =>
  albums.value.map((a) => {
    const bound = boundAlbumIds.value.has(String(a.id))
    return {
      label: `${a.name}（${a.count}）${bound ? ' · 已绑定' : ''}`,
      value: a.id,
      disabled: bound,
    }
  }),
)
const canBind = computed(() => {
  if (saving.value) return false
  return mtReady.value && albumId.value != null
})

async function loadSources() {
  if (!props.ownerId) return
  loading.value = true
  try {
    sources.value = await photosApi.sources(props.ownerType, props.ownerId)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function loadAlbums() {
  if (!mtReady.value) {
    albums.value = []
    return
  }
  albumsLoading.value = true
  try {
    albums.value = await photosApi.mtAlbums()
  } catch (e) {
    albums.value = []
    message.error((e as Error).message)
  } finally {
    albumsLoading.value = false
  }
}

function openBrowser() {
  if (bindMode.value === 'folder' && !mtReady.value) {
    message.warning('请先在「基础设置 → MT Photos」填写地址和 API Key 并启用')
    return
  }
  browserShow.value = true
}

async function bindFolder() {
  saving.value = true
  try {
    if (albumId.value == null) {
      message.warning('请选择 MT Photos 相册')
      return
    }
    await photosApi.bind({
      owner_type: props.ownerType,
      owner_id: props.ownerId,
      section: isWall.value ? 'wall' : section.value,
      provider: 'mtphotos',
      mt_kind: 'album',
      external_id: String(albumId.value),
    })
    albumId.value = null
    message.success(
      isWall.value
        ? '已绑定 MT Photos 相册，打开照片墙即可浏览'
        : '已绑定 MT Photos 相册，打开对应分区即可按帖子浏览',
    )
    await loadSources()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

function scanSummary(r: PhotoScanResult) {
  const parts = [`${r.files_seen} 个文件`]
  if (r.created) parts.push(`新增 ${r.created}`)
  if (r.updated) parts.push(`更新 ${r.updated}`)
  if (r.restored) parts.push(`恢复 ${r.restored}`)
  if (r.removed) parts.push(`移除 ${r.removed}`)
  if (r.posts) parts.push(`${r.posts} 帖`)
  if (r.thumbs_queued) parts.push(`缩略图排队 ${r.thumbs_queued}`)
  parts.push(`${(r.elapsed_ms / 1000).toFixed(1)}s`)
  return parts.join(' · ')
}

async function scanSource(item: PhotoSource) {
  scanningId.value = item.id
  try {
    const r = await photosApi.scan(item.id)
    const summary = `扫描完成：${scanSummary(r)}`
    if (r.warning) {
      message.warning(`${summary}。${r.warning}`)
    } else {
      message.success(summary)
    }
    await loadSources()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    scanningId.value = null
  }
}

async function scanAll() {
  if (!sources.value.length) return
  for (const item of sources.value) {
    await scanSource(item)
  }
}

function isMt(item: PhotoSource) {
  return item.provider === 'mtphotos'
}

function isMtFolder(item: PhotoSource) {
  return isMt(item) && (item.folder_path || '').includes('/folder/')
}

function sourceTitle(item: PhotoSource) {
  if (isMt(item)) {
    if (item.label) return item.label
    return isMtFolder(item)
      ? `MT 文件夹 #${item.external_id || item.id}`
      : `MT 相册 #${item.external_id || item.id}`
  }
  return item.folder_path
}

async function unbindSource(item: PhotoSource) {
  dialog.warning({
    title: '解除绑定',
    content: isMt(item)
      ? '会解除与 MT Photos 的关联，MT Photos 里的文件不会删除。确定解除？'
      : '会清除该文件夹的照片索引，本地文件不会删除。照片 uid 也会一并失效。确定解除？',
    positiveText: '解除',
    negativeText: '取消',
    onPositiveClick: async () => {
      unbindingId.value = item.id
      try {
        await photosApi.unbind(item.id)
        message.success('已解除绑定（本地文件未改动）')
        await loadSources()
      } catch (e) {
        message.error((e as Error).message)
      } finally {
        unbindingId.value = null
      }
    },
  })
}

onMounted(() => {
  void loadSources()
  void loadAlbums()
})
watch(
  () => [props.ownerType, props.ownerId],
  () => {
    void loadSources()
  },
)
watch(
  () => [section.value, bindMode.value, mtReady.value],
  () => {
    if (isWall.value) bindMode.value = 'album'
    if (useMtAlbum.value) void loadAlbums()
  },
)
</script>

<template>
  <div class="photo-folders">
    <p class="photo-folders-hint">
      官方/粉丝绑 MT Photos 的相册/文件夹。目录里有 Instagram 导出的 txt 就会按帖子展示（文案+多图）。多个博主混在同一目录或分子目录都可以；绑文件夹时在浏览器里勾选「含子目录」。照片墙仍绑相册，按单张浏览。
    </p>

    <div class="photo-folders-add">
      <SaSelect v-model="section" :options="sectionOptions" style="width: 108px" />
      <SaSelect v-if="!isWall" v-model="bindMode" :options="bindModeOptions" style="width: 132px" />
      <template v-if="useMtAlbum">
        <SaSelect
          v-model="albumId"
          :options="albumOptions"
          :loading="albumsLoading"
          :disabled="!mtReady"
          filterable
          placeholder="选择 MT Photos 相册（已绑定的不可重复选择）"
          style="flex: 1 1 200px; min-width: 0"
        />
        <button class="sa-btn-add" type="button" :disabled="!canBind || saving" @click="bindFolder">
          {{ saving ? '绑定中…' : '绑定' }}
        </button>
      </template>
      <template v-else>
        <button type="button" class="folder-pick-bar" @click="openBrowser">
          <FolderOutlined :size="15" />
          <span class="folder-pick-text"> 浏览 MT Photos 文件夹并绑定… </span>
          <ChevronRightOutlined :size="14" />
        </button>
      </template>
    </div>
    <p v-if="!mtReady" class="sa-empty">
      请先在「基础设置 → MT Photos」填写地址和 API Key 并启用。
    </p>

    <div v-if="loading" class="sa-empty">加载中…</div>
    <div v-else-if="!sources.length" class="sa-empty">尚未绑定图片文件夹</div>
    <div v-else class="photo-folders-list">
      <div class="photo-folders-toolbar">
        <span>已绑定 {{ sources.length }} 个文件夹</span>
        <button
          v-if="sources.length"
          class="photo-folders-scan-all"
          type="button"
          :disabled="scanningId != null"
          @click="scanAll"
        >
          <RefreshOutlined :size="13" />
          全部扫描
        </button>
      </div>
      <div v-for="item in sources" :key="item.id" class="sa-row photo-folder-row">
        <FolderOutlined :size="16" />
        <div class="photo-folder-meta">
          <div class="photo-folder-path">{{ sourceTitle(item) }}</div>
          <div class="photo-folder-sub">
            {{ sectionLabel[item.section] || item.section }}
            <template v-if="isMtFolder(item)"> · MT 文件夹</template>
            <template v-else-if="isMt(item)"> · MT 相册</template>
            · {{ item.photo_count }} 张
            <template v-if="item.recursive"> · 含子目录</template>
            <template v-if="item.last_scanned_at">
              · 上次 {{ formatDateTime(item.last_scanned_at) }}
            </template>
            <template v-else> · 尚未扫描</template>
          </div>
        </div>
        <div class="photo-folder-actions">
          <button
            class="sa-btn-ghost"
            type="button"
            :disabled="scanningId != null && scanningId !== item.id"
            @click="scanSource(item)"
          >
            {{ scanningId === item.id ? '处理中…' : isMt(item) ? '同步' : '扫描' }}
          </button>
          <button
            class="sa-btn-ghost is-danger"
            type="button"
            :disabled="unbindingId === item.id"
            @click="unbindSource(item)"
          >
            {{ unbindingId === item.id ? '解除中…' : '解除' }}
          </button>
        </div>
      </div>
    </div>

    <FolderBrowserModal
      v-model:show="browserShow"
      :mode="browserMode"
      :owner-type="ownerType"
      :owner-id="ownerId"
      :section="section"
      :sources="sources"
      @bound="loadSources"
    />
  </div>
</template>

<style scoped>
/* 下拉 / 按钮 / 结果行 / 空态统一用全局口径（styles/db-controls.css）：
   .sa-select · .sa-btn-add · .sa-btn-ghost · .sa-row · .sa-empty —— 这里只留本组件特有的布局。 */
.photo-folders {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
}
.photo-folders-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--sa-text-tertiary);
}
.photo-folders-add {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.folder-pick-bar {
  flex: 1 1 240px;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 12px;
  border: 1px dashed var(--sa-border);
  border-radius: 12px;
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  cursor: pointer;
  font-size: 13px;
  transition: border-color 0.15s ease, color 0.15s ease;
}
.folder-pick-bar:hover {
  border-color: var(--sa-accent-border);
  color: var(--sa-accent);
}
.folder-pick-text {
  flex: 1;
  min-width: 0;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.photo-folders-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.photo-folders-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.photo-folders-scan-all {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  color: var(--sa-accent);
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
  font-family: inherit;
  padding: 0;
}
.photo-folders-scan-all:disabled {
  opacity: 0.5;
  cursor: default;
}
.photo-folder-meta {
  min-width: 0;
  flex: 1;
}
.photo-folder-path {
  font-size: 13px;
  color: var(--sa-text-primary);
  word-break: break-all;
}
.photo-folder-sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.photo-folder-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

@media (max-width: 768px) {
  .photo-folder-row {
    flex-wrap: wrap;
  }
  .photo-folder-actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
