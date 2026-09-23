<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { NButton, NModal, NForm, NFormItem, NInput, useMessage } from 'naive-ui'
import { artistsApi } from '@/api/artists'
import type { ArtistBrief, Artist, ArtistFuzzyHit } from '@/types/models'
import { AddOutlined } from '@/components/icons'
import SaSelect from '@/components/SaSelect.vue'

const props = withDefaults(
  defineProps<{
    modelValue?: number | null
    placeholder?: string
    clearable?: boolean
    disabled?: boolean
    /** 允许快速创建；默认 true */
    allowCreate?: boolean
    /** Fancam 场景提示文案（不阻断，仅 UI 提示） */
    fancamHint?: boolean
  }>(),
  {
    modelValue: null,
    placeholder: '搜索并选择艺人',
    clearable: true,
    disabled: false,
    allowCreate: true,
    fancamHint: false,
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: number | null): void
  (e: 'change', artist: ArtistBrief | null): void
}>()

const message = useMessage()

const options = ref<{ label: string; value: number }[]>([])
const loading = ref(false)
const selectedId = ref<number | null>(props.modelValue)
const selectedBrief = ref<ArtistBrief | null>(null)

// 快速创建弹窗
const createModalShow = ref(false)
const creating = ref(false)
const createForm = ref<{ name: string; chinese_name: string; stage_name: string; gender: string }>({
  name: '',
  chinese_name: '',
  stage_name: '',
  gender: 'Female',
})

const genderOptions = [
  { label: '女', value: 'Female' },
  { label: '男', value: 'Male' },
  { label: '其他', value: 'Other' },
]

function buildLabel(a: { name: string; chinese_name?: string | null; stage_name?: string | null }): string {
  const parts = [a.name]
  if (a.chinese_name) parts.push(a.chinese_name)
  if (a.stage_name && a.stage_name !== a.name) parts.push(`(${a.stage_name})`)
  return parts.join(' / ')
}

async function search(q: string) {
  loading.value = true
  try {
    const list = await artistsApi.brief(q || undefined)
    options.value = list.map((a) => ({ label: buildLabel(a), value: a.id }))
    if (selectedId.value && !options.value.some((o) => o.value === selectedId.value)) {
      if (selectedBrief.value) {
        options.value.unshift({
          label: buildLabel(selectedBrief.value),
          value: selectedBrief.value.id,
        })
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
    const full: Artist = await artistsApi.get(selectedId.value)
    selectedBrief.value = {
      id: full.id,
      uid: full.uid,
      name: full.name,
      chinese_name: full.chinese_name,
      stage_name: full.stage_name,
    }
    if (!options.value.some((o) => o.value === full.id)) {
      options.value.unshift({ label: buildLabel(full), value: full.id })
    }
  } catch {
    selectedBrief.value = null
  }
}

function onSelect(value: number | null) {
  selectedId.value = value
  emit('update:modelValue', value)
  if (value && selectedBrief.value?.id !== value) {
    void ensureSelectedLoaded()
  }
  if (!value) selectedBrief.value = null
  emit('change', selectedBrief.value)
}

function openCreate() {
  createForm.value = { name: '', chinese_name: '', stage_name: '', gender: 'Female' }
  fuzzyCandidates.value = []
  createModalShow.value = true
}

// 创建前查重：精确同名直接选中；相似候选列出待确认
const fuzzyCandidates = ref<ArtistFuzzyHit[]>([])

async function selectHit(hit: { id: number }) {
  try {
    const full = await artistsApi.get(hit.id)
    const brief: ArtistBrief = {
      id: full.id,
      uid: full.uid,
      name: full.name,
      chinese_name: full.chinese_name,
      stage_name: full.stage_name,
    }
    selectedBrief.value = brief
    selectedId.value = brief.id
    if (!options.value.some((o) => o.value === brief.id)) {
      options.value = [{ label: buildLabel(brief), value: brief.id }, ...options.value]
    }
    emit('update:modelValue', brief.id)
    emit('change', brief)
    createModalShow.value = false
    message.success('已选中库内已有艺人')
  } catch (e) {
    message.error((e as Error).message)
  }
}

async function submitCreate() {
  const name = createForm.value.name.trim()
  if (!name) {
    message.warning('请填写艺人名')
    return
  }
  // 首次提交先查重；确认过相似候选后（列表已展示）再点则真正创建
  if (!fuzzyCandidates.value.length) {
    let hits: ArtistFuzzyHit[] = []
    try {
      hits = await artistsApi.fuzzy(name)
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
    const created = await artistsApi.create({
      name,
      chinese_name: createForm.value.chinese_name.trim() || null,
      stage_name: createForm.value.stage_name.trim() || null,
      gender: createForm.value.gender,
    })
    message.success('已创建艺人')
    const brief: ArtistBrief = {
      id: created.id,
      uid: created.uid,
      name: created.name,
      chinese_name: created.chinese_name,
      stage_name: created.stage_name,
    }
    selectedBrief.value = brief
    selectedId.value = created.id
    options.value = [{ label: buildLabel(brief), value: brief.id }, ...options.value]
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
  if (selectedId.value) {
    void ensureSelectedLoaded()
  }
  void search('')
})
</script>

<template>
  <div class="remote-artist-select">
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
          没找到？快速新建艺人
        </n-button>
      </template>
    </SaSelect>
    <div v-if="fancamHint && !selectedId" class="hint">
      当前类型为个人直拍，建议填写直拍对象（成员）
    </div>

    <n-modal
      v-model:show="createModalShow"
      preset="card"
      title="快速新建艺人"
      style="width: 440px"
      :bordered="false"
    >
      <n-form label-placement="top">
        <n-form-item label="名称" required>
          <n-input v-model:value="createForm.name" placeholder="艺人名" />
        </n-form-item>
        <n-form-item label="中文名（可选）">
          <n-input v-model:value="createForm.chinese_name" placeholder="中文名" />
        </n-form-item>
        <n-form-item label="艺名（可选）">
          <n-input v-model:value="createForm.stage_name" placeholder="艺名 / 活动名" />
        </n-form-item>
        <n-form-item label="性别">
          <SaSelect v-model="createForm.gender" :options="genderOptions" />
        </n-form-item>
      </n-form>
      <div v-if="fuzzyCandidates.length" class="fuzzy-box">
        <p class="fuzzy-box-title">库里已有相近艺人，点击可选中，避免建成两条：</p>
        <button
          v-for="hit in fuzzyCandidates"
          :key="hit.id"
          class="fuzzy-box-item"
          type="button"
          @click="selectHit(hit)"
        >
          {{ hit.name }}
          <template v-if="hit.stage_name && hit.stage_name !== hit.name">（{{ hit.stage_name }}）</template>
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
  </div>
</template>

<style scoped>
.remote-artist-select .hint {
  margin-top: 4px;
  font-size: 12px;
  color: #f0a020;
}
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
