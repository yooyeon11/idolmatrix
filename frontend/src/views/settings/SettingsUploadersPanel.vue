<script setup lang="ts">
/**
 * 博主管理：给上传人（博主）指定「固定视频类型」+ 配置 YouTube 缺失检测。
 *
 * 固定类型：命中规则的博主，在待整理打开详情时会自动选中该类型，AI 也不再判断视频类型；
 * 未配置的博主照旧全部交给 AI。规则按博主名（忽略大小写与首尾空白）匹配。
 *
 * 缺失检测：填了频道地址并开启后，博主页可拉取频道完整投稿列表做缺失比对。
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
/** 只显示已配置（固定类型或缺失检测）的博主 */
const onlyFixed = ref(false)

function ruleSummary(item: UploaderItem) {
  const types = item.video_types || []
  if (!types.length) return ''
  return types.map((t) => VIDEO_TYPE_LABEL[t] || t).join('、')
}

const fixedCount = computed(
  () => items.value.filter((i) => (i.video_types || []).length || i.youtube_channel_url).length,
)
const channelCount = computed(() => items.value.filter((i) => i.youtube_channel_url).length)

const filtered = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  return items.value.filter((i) => {
    if (onlyFixed.value && !(i.video_types || []).length && !i.youtube_channel_url) return false
    if (!k) return true
    return (
      i.name.toLowerCase().includes(k) ||
      (i.video_types || []).some((t) => (VIDEO_TYPE_LABEL[t] || '').toLowerCase().includes(k)) ||
      (i.youtube_channel_url || '').toLowerCase().includes(k)
    )
  })
})

async function load() {
  loading.value = true
  try {
    const res = await uploadersApi.manage()
    items.value = res.items.map((i) => ({
      ...i,
      video_types: [...(i.video_types || [])],
      youtube_channel_url: i.youtube_channel_url || '',
      missing_check_enabled: !!i.missing_check_enabled,
    }))
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

/** 整体覆盖保存：payload 含「已固定类型」或「配置了频道地址」的博主 */
async function persist(successText?: string) {
  saving.value = true
  try {
    const payload = items.value
      .filter((i) => (i.video_types || []).length || i.youtube_channel_url)
      .map((i) => ({
        name: i.name,
        video_types: [...(i.video_types as string[])],
        youtube_channel_url: i.youtube_channel_url || '',
        missing_check_enabled: !!i.missing_check_enabled,
      }))
    await uploadersApi.saveRules(payload)
    if (successText) message.success(successText)
    // 成功后回读：服务端会归一化合并（同名不同写法的两行合并成一条、空行丢弃），
    // 频道刚配上时也顺带带回缺失检测统计，否则完成度要刷新页面才出现
    await load()
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

/** 频道地址：失焦时保存（与固定类型同一套整体覆盖） */
async function onChannelBlur(item: UploaderItem) {
  const url = (item.youtube_channel_url || '').trim()
  item.youtube_channel_url = url
  if (url) await persist(`已保存「${item.name}」的频道地址`)
}

async function onChannelToggle(item: UploaderItem, enabled: boolean) {
  if (enabled && !(item.youtube_channel_url || '').trim()) {
    item.missing_check_enabled = false
    message.error('开启缺失检测前请先填写 YouTube 频道地址')
    return
  }
  item.missing_check_enabled = enabled
  await persist(
    enabled
      ? `已开启「${item.name}」的缺失视频检测，可到博主页检测`
      : `已关闭「${item.name}」的缺失视频检测`,
  )
}

/** 频道地址即时校验：解析出频道 ID 并回读频道名，确认地址写对了（出网 1-2 quota，不落库） */
const probing = ref('')
async function testChannel(item: UploaderItem) {
  const url = (item.youtube_channel_url || '').trim()
  if (!url) {
    message.error('请先填写 YouTube 频道地址')
    return
  }
  probing.value = item.name
  try {
    const r = await uploadersApi.channelProbe(url)
    message.success(`地址有效：${r.title || r.channel_id}（${r.channel_id}）`)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    probing.value = ''
  }
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
        <input v-model="keyword" type="text" placeholder="搜索博主、已固定的类型或频道地址" />
      </div>
      <label class="uploader-rules-filter">
        <input v-model="onlyFixed" type="checkbox" />
        只看已配置（{{ fixedCount }}）
      </label>
      <button class="sa-btn-ghost" type="button" :disabled="loading || saving" @click="load">
        <RefreshOutlined :size="13" />
        刷新
      </button>
    </div>

    <div class="uploader-rules-meta">
      共 {{ items.length }} 位博主，已固定 {{ fixedCount - channelCount }} 位类型 ·
      {{ channelCount }} 位启用缺失检测 · 库内博主按作品数排序
      <span v-if="saving"> · 保存中…</span>
    </div>

    <div v-if="loading" class="sa-empty">加载中…</div>
    <div v-else-if="!filtered.length" class="sa-empty">
      {{ items.length ? '没有匹配的博主' : '库内还没有带上传人信息的视频，入库带上传人的视频后即可在此固定类型。' }}
    </div>
    <div v-else class="uploader-rules-list">
      <div v-for="item in filtered" :key="item.name" class="sa-row uploader-row">
        <div class="uploader-main">
          <div class="uploader-line">
            <div class="uploader-meta">
              <div class="uploader-name">
                {{ item.name }}
                <span v-if="item.platform" class="uploader-tag">{{ item.platform }}</span>
                <span v-if="!item.in_library" class="uploader-tag">库内暂无作品</span>
                <span v-if="item.missing_count != null" class="uploader-tag uploader-tag--missing">
                  缺 {{ item.missing_count }}
                </span>
              </div>
              <div class="uploader-sub">
                {{ item.video_count }} 个视频
                <template v-if="ruleSummary(item)"> · 已固定为 {{ ruleSummary(item) }}</template>
                <template v-else> · 由 AI 判断</template>
                <template v-if="item.channel_video_total != null">
                  · 库内 {{ item.video_count }}/{{ item.channel_video_total }}
                </template>
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
          <div class="uploader-line uploader-channel">
            <input
              v-model="item.youtube_channel_url"
              class="uploader-channel-input"
              type="text"
              placeholder="YouTube 频道地址（@handle / channel 链接 / UC ID），填后可检测缺失视频"
              :disabled="saving"
              @blur="onChannelBlur(item)"
              @keydown.enter.prevent="($event.target as HTMLInputElement).blur()"
            />
            <label class="uploader-check">
              <input
                :checked="item.missing_check_enabled"
                type="checkbox"
                :disabled="saving"
                @change="onChannelToggle(item, ($event.target as HTMLInputElement).checked)"
              />
              缺失检测
            </label>
            <button
              class="sa-btn-ghost"
              type="button"
              :disabled="probing === item.name || !(item.youtube_channel_url || '').trim()"
              @click="testChannel(item)"
            >
              {{ probing === item.name ? '测试中…' : '测试地址' }}
            </button>
          </div>
        </div>
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
.uploader-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.uploader-line {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
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
.uploader-tag--missing {
  border-color: var(--sa-accent);
  color: var(--sa-accent);
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
.uploader-channel {
  padding-left: 0;
}
.uploader-channel-input {
  flex: 1;
  min-width: 0;
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 10px;
  background: var(--sa-subtle);
  font-size: 12px;
  font-family: inherit;
  color: var(--sa-text-primary);
  outline: none;
  transition: border-color 0.2s;
}
.uploader-channel-input:focus {
  border-color: var(--sa-accent);
}
.uploader-channel-input::placeholder {
  color: var(--sa-text-tertiary);
}
.uploader-channel-input:disabled {
  opacity: 0.6;
}
.uploader-check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--sa-text-secondary);
  cursor: pointer;
  white-space: nowrap;
}

@media (max-width: 768px) {
  .uploader-line {
    flex-wrap: wrap;
  }
  .uploader-select {
    flex: 1 1 100%;
  }
  .uploader-channel {
    flex-wrap: nowrap;
  }
}
</style>
