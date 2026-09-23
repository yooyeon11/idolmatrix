<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NSpin, useDialog, useMessage } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import PhotoGallery from '@/components/PhotoGallery.vue'
import { photosApi } from '@/api/photos'
import type { PhotoCollection } from '@/types/models'
import { collectionPath, fetchByRouteParam } from '@/utils/routes'
import { BookmarkOutlined } from '@/components/icons'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const collection = ref<PhotoCollection | null>(null)
const loading = ref(false)
const notFound = ref(false)
const editing = ref(false)
const nameDraft = ref('')
const saving = ref(false)

const routeKey = computed(() => String(route.params.uid || ''))

async function load() {
  const param = routeKey.value.trim()
  if (!param) {
    notFound.value = true
    return
  }
  loading.value = true
  notFound.value = false
  try {
    const col = await fetchByRouteParam(param, photosApi.getCollectionByUid, photosApi.getCollection)
    collection.value = col
    nameDraft.value = col.name
    if (col.uid && col.uid !== param) {
      router.replace(collectionPath(col.uid))
    }
  } catch (e) {
    notFound.value = true
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function saveName() {
  if (!collection.value) return
  const name = nameDraft.value.trim()
  if (!name) {
    message.warning('名称不能为空')
    return
  }
  saving.value = true
  try {
    collection.value = await photosApi.updateCollection(collection.value.id, { name })
    editing.value = false
    message.success('已保存')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

function confirmDelete() {
  if (!collection.value) return
  const col = collection.value
  dialog.warning({
    title: '删除收藏夹',
    content: `删除「${col.name}」不会删除照片文件，只去掉这个夹。确定删除？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await photosApi.removeCollection(col.id)
        message.success('已删除')
        router.push('/collections')
      } catch (e) {
        message.error((e as Error).message)
      }
    },
  })
}

onMounted(load)
watch(
  () => route.params.uid,
  (next, prev) => {
    if (!next || next === prev) return
    void load()
  },
)
</script>

<template>
  <div class="mv-page">
    <SaHeader />
    <div class="mv-container">
      <n-spin :show="loading">
        <div v-if="notFound" class="empty-state">
          <BookmarkOutlined :size="40" />
          <p>收藏夹不存在或已删除</p>
          <button class="empty-back" type="button" @click="router.push('/collections')">返回收藏</button>
        </div>
        <div v-else-if="collection" class="col-page">
          <div class="col-head">
            <div class="col-title-wrap">
              <input
                v-if="editing"
                v-model="nameDraft"
                class="col-name-input"
                @keydown.enter="saveName"
                @keydown.escape="editing = false"
              />
              <h1 v-else class="col-name">{{ collection.name }}</h1>
              <span class="col-count">{{ collection.photo_count }} 张</span>
            </div>
            <div class="col-actions">
              <button v-if="editing" class="sa-btn" type="button" :disabled="saving" @click="saveName">
                保存
              </button>
              <button v-else class="sa-btn" type="button" @click="editing = true">重命名</button>
              <button class="sa-btn sa-btn--danger" type="button" @click="confirmDelete">删除</button>
            </div>
          </div>
          <PhotoGallery :collection-id="collection.id" />
        </div>
      </n-spin>
    </div>
  </div>
</template>

<style scoped>
.mv-page {
  min-height: 100vh;
}
.mv-container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px 64px;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 80px 0;
  color: var(--sa-text-tertiary);
}
.empty-back {
  padding: 6px 18px;
  border: 1px solid var(--sa-border);
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  cursor: pointer;
}
.col-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  padding: 28px 0 18px;
  flex-wrap: wrap;
}
.col-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}
.col-name {
  margin: 0;
  font-size: 28px;
  font-weight: 700;
  color: var(--sa-text-primary);
}
.col-name-input {
  font-size: 24px;
  font-weight: 700;
  padding: 4px 8px;
  border: 1px solid var(--sa-border);
  border-radius: 8px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
}
.col-count {
  font-size: 14px;
  color: var(--sa-text-tertiary);
}
.col-actions {
  display: flex;
  gap: 8px;
}
.sa-btn {
  padding: 6px 14px;
  border: 1px solid var(--sa-border);
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
}
.sa-btn--danger {
  color: var(--sa-danger, #e5484d);
  border-color: transparent;
}

@media (max-width: 768px) {
  .mv-container {
    padding: 0 16px 48px;
  }
  .col-name {
    font-size: 22px;
  }
}
</style>
