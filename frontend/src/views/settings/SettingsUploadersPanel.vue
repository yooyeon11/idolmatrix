<script setup lang="ts">
/**
 * 博主管理：给上传人（博主）指定「固定视频类型」。
 *
 * 命中规则的博主，在待整理打开详情时会自动选中该类型，AI 也不再判断视频类型；
 * 未配置的博主照旧全部交给 AI。规则按博主名（忽略大小写与首尾空白）匹配。
 */
import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import SaSelect from '@/components/SaSelect.vue'
import { uploadersApi, type UploaderItem } from '@/api/uploaders'
import { VIDEO_TYPE_OPTIONS, VIDEO_TYPE_LABEL } from '@/types/models'
import { RefreshOutlined, SearchOutlined } from '@/components/icons'

const message = useMessage()

const items = ref<UploaderItem[]>([])
const loading = ref(false)
const saving = ref(false)
const keyword = ref('')
/** 只显示已固定类型的博主 */
const onlyFixed = ref(false)

function ruleSummary(item: UploaderItem) {
  const types = item.video_types || []
  if (!types.length) return ''
  return types.map((t) => VIDEO_TYPE_LABEL[t] || t).join('、')
}

const fixedCount = computed(() => items.value.filter((i) => (i.video_types || []).length).length)

const filtered = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  return items.value.filter((i) => {
    if (onlyFixed.value && !(i.video_types || []).length) return false
    if (!k) return true
    return i.name.toLowerCase().includes(k) || (i.video_types || []).some((t) => (VIDEO_TYPE_LABEL[t] || '').toLowerCase().includes(k))
  })
})

async function load() {
  loading.value = true
  try {
    const res = await uploadersApi.manage()
    items.value = res.items.map((i) => ({ ...i, video_types: [...(i.video_types || [])] }))
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

/** 整体覆盖保存：payload 只含「已固定类型」的博主 */
async function persist(successText?: string) {
  saving.value = true
  try {
    const payload = items.value
      .filter((i) => (i.video_types || []).length)
      .map((i) => ({ name: i.name, video_types: [...(i.video_types as string[])] }))
    await uploadersApi.saveRules(payload)
    if (successText) message.success(successText)
  } catch (e) {
    message.error((e as Error).message)
    await load()
  } finally {
    saving.value = false
  }
}

async function onRowChange(item: UploaderItem, types: string[]) {
  item.video_types = [...types]
  await persist(
    types.length
      ? `已固定「${item.name}」为 ${ruleSummary(item)}，待整理将自动选中`
      : `已取消「${item.name}」的固定类型，恢复由 AI 判断`,
  )
}

async function clearRule(item: UploaderItem) {
  item.video_types = []
  await persist(`已取消「${item.name}」的固定类型，恢复由 AI 判断`)
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="uploader-rules">
    <div class="uploader-rules-bar">
      <div class="uploader-rules-search">
        <SearchOutlined :size="14" />
        <input v-model="keyword" type="text" placeholder="搜索博主或已固定的类型" />
      </div>
      <label class="uploader-rules-filter">
        <input v-model="onlyFixed" type="checkbox" />
        只看已固定（{{ fixedCount }}）
      </label>
      <button class="sa-btn-ghost" type="button" :disabled="loading || saving" @click="load">
        <RefreshOutlined :size="13" />
        刷新
      </button>
    </div>

    <div class="uploader-rules-meta">
      共 {{ items.length }} 位博主，已固定 {{ fixedCount }} 位 · 库内博主按作品数排序
      <span v-if="saving"> · 保存中…</span>
    </div>

    <div v-if="loading" class="sa-empty">加载中…</div>
    <div v-else-if="!filtered.length" class="sa-empty">
      {{ items.length ? '没有匹配的博主' : '库内还没有带上传人信息的视频，入库带上传人的视频后即可在此固定类型。' }}
    </div>
    <div v-else class="uploader-rules-list">
      <div v-for="item in filtered" :key="item.name" class="sa-row uploader-row">
        <div class="uploader-meta">
          <div class="uploader-name">
            {{ item.name }}
            <span v-if="item.platform" class="uploader-tag">{{ item.platform }}</span>
            <span v-if="!item.in_library" class="uploader-tag">库内暂无作品</span>
          </div>
          <div class="uploader-sub">
            {{ item.video_count }} 个视频
            <template v-if="ruleSummary(item)"> · 已固定为 {{ ruleSummary(item) }}</template>
            <template v-else> · 由 AI 判断</template>
          </div>
        </div>
        <SaSelect
          :model-value="item.video_types || []"
          :options="VIDEO_TYPE_OPTIONS"
          multiple
          filterable
          :placeholder="'跟随 AI 判断'"
          class="uploader-select"
          @update:model-value="(v) => onRowChange(item, (v as string[]) || [])"
        />
        <button
          class="sa-btn-ghost is-danger"
          type="button"
          :disabled="!(item.video_types || []).length || saving"
          @click="clearRule(item)"
        >
          取消固定
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 控件口径统一走全局（styles/db-controls.css）：.sa-select /
   .sa-btn-ghost / .sa-row / .sa-empty。 */
.uploader-rules {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  min-width: 0;
}
.uploader-rules-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.uploader-rules-search {
  flex: 1 1 200px;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--sa-border);
  border-radius: 12px;
  background: var(--sa-elevated);
  color: var(--sa-text-tertiary);
}
.uploader-rules-search input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 13px;
  font-family: inherit;
  color: var(--sa-text-primary);
}
.uploader-rules-filter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--sa-text-secondary);
  cursor: pointer;
  white-space: nowrap;
}
.uploader-rules-meta {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.uploader-rules-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.uploader-meta {
  flex: 1 1 180px;
  min-width: 0;
}
.uploader-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--sa-text-primary);
  overflow-wrap: anywhere;
}
.uploader-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 999px;
  border: 1px solid var(--sa-border);
  background: var(--sa-subtle);
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.uploader-sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
  overflow-wrap: anywhere;
}
.uploader-select {
  flex: 0 1 300px;
  min-width: 0;
}

@media (max-width: 768px) {
  .uploader-row {
    flex-wrap: wrap;
  }
  .uploader-select {
    flex: 1 1 100%;
  }
}
</style>
