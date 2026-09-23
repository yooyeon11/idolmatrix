<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { photosApi } from '@/api/photos'
import type { PhotoCollection, PhotoItem } from '@/types/models'

const props = defineProps<{
  photo: PhotoItem
  currentCollectionId?: number | null
}>()

const emit = defineEmits<{
  change: [photoId: number, collectionIds: number[]]
  removedFromCurrent: []
}>()

const message = useMessage()
const collections = ref<PhotoCollection[]>([])
const selected = ref<Set<number>>(new Set())
const loading = ref(false)
const savingId = ref<number | null>(null)
const newName = ref('')
const creating = ref(false)

const isInCurrent = computed(() =>
  props.currentCollectionId != null && selected.value.has(props.currentCollectionId),
)

async function load() {
  loading.value = true
  try {
    const [cols, ms] = await Promise.all([
      photosApi.collections(),
      photosApi.memberships([props.photo.id]),
    ])
    collections.value = cols
    const ids = ms.memberships[String(props.photo.id)] || ms.memberships[props.photo.id as unknown as string] || []
    selected.value = new Set(ids)
    emit('change', props.photo.id, ids)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function toggle(col: PhotoCollection) {
  savingId.value = col.id
  const next = new Set(selected.value)
  try {
    if (next.has(col.id)) {
      await photosApi.removeFromCollection(col.id, props.photo.id)
      next.delete(col.id)
      if (props.currentCollectionId === col.id) emit('removedFromCurrent')
    } else {
      await photosApi.addToCollection(col.id, { photo_uid: props.photo.uid, photo_id: props.photo.id })
      next.add(col.id)
    }
    selected.value = next
    emit('change', props.photo.id, [...next])
    collections.value = await photosApi.collections()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    savingId.value = null
  }
}

async function createAndAdd() {
  const name = newName.value.trim()
  if (!name) {
    message.warning('请填写收藏夹名称')
    return
  }
  creating.value = true
  try {
    const col = await photosApi.createCollection({ name })
    await photosApi.addToCollection(col.id, { photo_uid: props.photo.uid, photo_id: props.photo.id })
    newName.value = ''
    const next = new Set(selected.value)
    next.add(col.id)
    selected.value = next
    emit('change', props.photo.id, [...next])
    collections.value = await photosApi.collections()
    message.success(`已加入「${col.name}」`)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    creating.value = false
  }
}

onMounted(load)
watch(
  () => props.photo.id,
  () => {
    void load()
  },
)
</script>

<template>
  <div class="collect-picker" @click.stop @pointerdown.stop>
    <div class="collect-title">收藏到</div>
    <div class="collect-create">
      <input
        v-model="newName"
        class="collect-input"
        type="text"
        placeholder="新建收藏夹"
        @keydown.enter="createAndAdd"
      />
      <button class="collect-add-btn" type="button" :disabled="creating" @click="createAndAdd">
        {{ creating ? '…' : '创建' }}
      </button>
    </div>
    <div v-if="loading" class="collect-empty">加载中…</div>
    <div v-else-if="!collections.length" class="collect-empty">还没有收藏夹</div>
    <div v-else class="collect-list">
      <button
        v-for="col in collections"
        :key="col.id"
        class="collect-row"
        :class="{ 'collect-row--on': selected.has(col.id) }"
        type="button"
        :disabled="savingId === col.id"
        @click="toggle(col)"
      >
        <span class="collect-check">{{ selected.has(col.id) ? '✓' : '' }}</span>
        <span class="collect-name">{{ col.name }}</span>
        <span class="collect-count">{{ col.photo_count }}</span>
      </button>
    </div>
    <p v-if="isInCurrent" class="collect-hint">当前收藏夹已勾选，取消勾选即移出</p>
  </div>
</template>

<style scoped>
.collect-picker {
  width: min(280px, calc(100vw - 32px));
  padding: 12px;
  border-radius: 12px;
  background: var(--sa-elevated, #1c1c1c);
  color: var(--sa-text-primary, #fff);
  border: 1px solid var(--sa-border-subtle, rgba(255, 255, 255, 0.12));
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35);
}
.collect-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 10px;
}
.collect-create {
  display: flex;
  gap: 6px;
  margin-bottom: 10px;
}
.collect-input {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  border-radius: 8px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-bg, transparent);
  color: inherit;
  font-size: 13px;
}
.collect-add-btn {
  padding: 6px 10px;
  border-radius: 8px;
  border: 0;
  background: var(--sa-accent);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}
.collect-add-btn:disabled {
  opacity: 0.6;
}
.collect-empty {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  padding: 8px 0;
}
.collect-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 220px;
  overflow: auto;
}
.collect-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}
.collect-row:hover,
.collect-row--on {
  background: var(--sa-hover, rgba(255, 255, 255, 0.08));
}
.collect-check {
  width: 16px;
  font-size: 13px;
  color: var(--sa-accent);
}
.collect-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.collect-count {
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.collect-hint {
  margin: 8px 0 0;
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
</style>
