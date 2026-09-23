<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { NButton, NModal, NForm, NFormItem, NInput, useMessage } from 'naive-ui'
import { songsApi } from '@/api/songs'
import type { SongBrief, Song, SongFuzzyHit } from '@/types/models'
import { AddOutlined } from '@/components/icons'
import SaSelect, { type SaOption } from '@/components/SaSelect.vue'

const props = withDefaults(
  defineProps<{
    modelValue?: number | null
    placeholder?: string
    clearable?: boolean
    disabled?: boolean
    /** 允许快速创建；默认 true */
    allowCreate?: boolean
  }>(),
  {
    modelValue: null,
    placeholder: '搜索歌曲名 / 别名 / 所属专辑',
    clearable: true,
    disabled: false,
    allowCreate: true,
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: number | null): void
  (e: 'change', song: SongBrief | null): void
}>()

const message = useMessage()

// 选项列表（带 tag = 「歌名（主体 · 别名「XXX」）」，同名歌曲靠它区分）
const options = ref<SaOption[]>([])
const loading = ref(false)
// 已选 id => 用于回显 label
const selectedId = ref<number | null>(props.modelValue)
const selectedBrief = ref<SongBrief | null>(null)

// 快速创建弹窗
const createModalShow = ref(false)
const creating = ref(false)
const createForm = ref<{ name: string; chinese_name: string }>({ name: '', chinese_name: '' })

function buildLabel(s: { name: string; chinese_name?: string | null }): string {
  return s.chinese_name ? `${s.name} / ${s.chinese_name}` : s.name
}

const MATCH_FIELD_ZH: Record<string, string> = {
  alias: '别名',
  korean_name: '韩文名',
  english_name: '英文名',
}

/**
 * 选项附加提示：主体名（消歧）+ 命中说明（不是靠歌名被搜出来的）。
 * 例：`Supernova（少女时代）`、`Into the New World（别名「다시 만난 세계」）`、
 * `Attention（俞娜 · 专辑「NewJeans 1st EP」）`。
 */
function buildTag(s: SongBrief): string | undefined {
  const parts: string[] = []
  if (s.owner) parts.push(s.owner)
  const field = s.matched_field ? MATCH_FIELD_ZH[s.matched_field] : null
  if (field && s.matched_value) parts.push(`${field}「${s.matched_value}」`)
  if (s.matched_album_name) parts.push(`专辑「${s.matched_album_name}」`)
  return parts.length ? parts.join(' · ') : undefined
}

function toOption(s: SongBrief): SaOption {
  return { label: buildLabel(s), value: s.id, tag: buildTag(s) }
}

async function search(q: string) {
  loading.value = true
  try {
    // 下拉是「手动找歌」的主场：三个开关都开 —— 别名 / 韩文名 / 专辑名都能搜到，
    // 且每条带主体名（库里有 I AM / Supernova / Too Hot 这类同名歌曲）
    const list = await songsApi.brief(q || undefined, {
      deep: true,
      withOwner: true,
      albumHits: true,
    })
    options.value = list.map(toOption)
    // 如果有当前选中 id 且未在结果里出现，补一条保证回显
    if (selectedId.value && !options.value.some((o) => o.value === selectedId.value)) {
      // 尝试从已缓存的 selectedBrief 补
      if (selectedBrief.value) {
        options.value.unshift(toOption(selectedBrief.value))
      }
    }
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function ensureSelectedLoaded() {
  if (!selectedId.value) {
    selectedBrief.value = null
    return
  }
  try {
    const full: Song = await songsApi.get(selectedId.value)
    selectedBrief.value = { id: full.id, uid: full.uid, name: full.name, chinese_name: full.chinese_name }
    // 顺手把选项补上，保证显示
    if (!options.value.some((o) => o.value === full.id)) {
      options.value.unshift(toOption(selectedBrief.value))
    }
  } catch {
    selectedBrief.value = null
  }
}

function onSelect(value: number | null) {
  selectedId.value = value
  emit('update:modelValue', value)
  const found = options.value.find((o) => o.value === value)
  if (found && selectedBrief.value?.id !== value) {
    // 简易 brief（label 是 name/中文名 拼接，无法精确还原，这里用 found.label 兜底）
    selectedBrief.value = null
  }
  if (value && selectedBrief.value?.id !== value) {
    // 异步补全
    void ensureSelectedLoaded()
  }
  if (!value) selectedBrief.value = null
  emit('change', selectedBrief.value)
}

function openCreate() {
  createForm.value = { name: '', chinese_name: '' }
  fuzzyCandidates.value = []
  createModalShow.value = true
}

// 创建前查重：精确同名直接选中；相似候选列出待确认
const fuzzyCandidates = ref<SongFuzzyHit[]>([])

async function selectHit(hit: { id: number }) {
  try {
    const full = await songsApi.get(hit.id)
    const brief: SongBrief = { id: full.id, uid: full.uid, name: full.name, chinese_name: full.chinese_name }
    selectedBrief.value = brief
    selectedId.value = brief.id
    if (!options.value.some((o) => o.value === brief.id)) {
      options.value = [toOption(brief), ...options.value]
    }
    emit('update:modelValue', brief.id)
    emit('change', brief)
    createModalShow.value = false
    message.success('已选中库内已有歌曲')
  } catch (e) {
    message.error((e as Error).message)
  }
}

async function submitCreate() {
  const name = createForm.value.name.trim()
  if (!name) {
    message.warning('请填写歌曲名')
    return
  }
  // 首次提交先查重；确认过相似候选后（列表已展示）再点则真正创建
  if (!fuzzyCandidates.value.length) {
    let hits: SongFuzzyHit[] = []
    try {
      hits = await songsApi.fuzzy(name)
    } catch {
      hits = []
    }
    const exact = hits.find((h) => (h.score || 0) >= 0.999)
    if (exact) {
      await selectHit(exact)
      return
    }
    if (hits.length) {
      fuzzyCandidates.value = hits
      return
    }
  }
  creating.value = true
  try {
    const created = await songsApi.create({
      name,
      chinese_name: createForm.value.chinese_name.trim() || null,
    })
    message.success('已创建歌曲')
    const brief: SongBrief = { id: created.id, uid: created.uid, name: created.name, chinese_name: created.chinese_name }
    selectedBrief.value = brief
    selectedId.value = created.id
    // 立即把新选项塞入下拉，便于回显
    options.value = [toOption(brief), ...options.value]
    emit('update:modelValue', created.id)
    emit('change', brief)
    createModalShow.value = false
    fuzzyCandidates.value = []
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    creating.value = false
  }
}

// 外部 modelValue 变化时同步（例如打开编辑弹窗回填）
watch(
  () => props.modelValue,
  (v) => {
    if (v !== selectedId.value) {
      selectedId.value = v
      if (v) {
        void ensureSelectedLoaded()
      } else {
        selectedBrief.value = null
      }
    }
  },
)

onMounted(() => {
  // 初次进入如果有默认值，先把对应 label 取回来
  if (selectedId.value) {
    void ensureSelectedLoaded()
  }
  // 同时加载一批常用项，避免下拉空
  void search('')
})
</script>

<template>
  <SaSelect
    :model-value="selectedId"
    :options="options"
    :loading="loading"
    :placeholder="placeholder"
    :clearable="clearable"
    :disabled="disabled"
    filterable
    remote
    @search="search"
    @update:model-value="onSelect"
  >
    <template v-if="allowCreate && !disabled" #action>
      <n-button size="small" type="primary" quaternary @click="openCreate">
        <template #icon><AddOutlined /></template>
        没找到？快速新建歌曲
      </n-button>
    </template>
  </SaSelect>

  <n-modal
    v-model:show="createModalShow"
    preset="card"
    title="快速新建歌曲"
    style="width: 420px"
    :bordered="false"
  >
    <n-form label-placement="top">
      <n-form-item label="名称" required>
        <n-input v-model:value="createForm.name" placeholder="歌曲名" />
      </n-form-item>
      <n-form-item label="中文名（可选）">
        <n-input v-model:value="createForm.chinese_name" placeholder="中文名" />
      </n-form-item>
    </n-form>
    <div v-if="fuzzyCandidates.length" class="fuzzy-box">
      <p class="fuzzy-box-title">库里已有相近歌曲，点击可选中，避免建成两条：</p>
      <button
        v-for="hit in fuzzyCandidates"
        :key="hit.id"
        class="fuzzy-box-item"
        type="button"
        @click="selectHit(hit)"
      >
        {{ hit.name }}
        <template v-if="hit.korean_name"> · {{ hit.korean_name }}</template>
        <template v-if="hit.chinese_name"> · {{ hit.chinese_name }}</template>
        <span class="fuzzy-box-score">{{ Math.round((hit.score || 0) * 100) }}%</span>
      </button>
    </div>
    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <n-button @click="createModalShow = false">取消</n-button>
        <n-button type="primary" :loading="creating" @click="submitCreate">
          {{ fuzzyCandidates.length ? '都不是，仍要新建' : '创建并选择' }}
        </n-button>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.fuzzy-box {
  margin-top: 4px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.fuzzy-box-title {
  margin: 0 0 2px;
  font-size: 12px;
  color: #f0a020;
}
.fuzzy-box-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  text-align: left;
  padding: 8px 10px;
  border: 1px solid rgba(128, 128, 128, 0.25);
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
}
.fuzzy-box-item:hover {
  border-color: rgba(128, 128, 128, 0.5);
}
.fuzzy-box-score {
  margin-left: auto;
  font-size: 12px;
  opacity: 0.55;
}
</style>
