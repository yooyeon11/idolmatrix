<script setup lang="ts">
import { ref } from 'vue'
import { companyRelationsApi, type CompanyRelation, type CompanyRelationPatch } from '@/api/companyRelations'
import SaSelect from '@/components/SaSelect.vue'
import type { SaOption } from '@/components/SaSelect.vue'

// 组合/艺人工作台共用的「所属公司」编辑区。
// 组件只负责交互，API 调用由父级按实体类型完成（add/update/remove）。
// addable=false 时隐藏「手动补挂」行（组合详情页关闭，艺人页保留）。
withDefaults(
  defineProps<{
    relations: CompanyRelation[]
    /** 是否显示手动补挂行 */
    addable?: boolean
    /** 补挂行是否显示「角色」输入（组合页关闭） */
    roleInput?: boolean
    /** 补挂按钮文案 */
    addLabel?: string
  }>(),
  { addable: true, roleInput: true, addLabel: '补挂公司' },
)

const emit = defineEmits<{
  (e: 'add', companyId: number, role: string | null): void
  (e: 'update', relationId: number, patch: CompanyRelationPatch): void
  (e: 'remove', relationId: number): void
}>()

const STATUS_OPTIONS = [
  { value: 'Active', label: '合约中' },
  { value: 'Former', label: '已解约' },
  { value: 'Inactive', label: '暂停' },
]

const pickedCompanyId = ref<string | null>(null)
const newRole = ref('')
const confirmRemoveId = ref<number | null>(null)

/** 供 SaSelect 远程搜索：空关键字返回前 50 家公司，保证点开下拉就有候选 */
async function searchCompanies(q: string): Promise<SaOption[]> {
  const list = await companyRelationsApi.searchCompanies(q)
  return list.map((c) => ({ value: String(c.id), label: c.name }))
}

function add() {
  if (!pickedCompanyId.value) return
  emit('add', Number(pickedCompanyId.value), newRole.value.trim() || null)
  pickedCompanyId.value = null
  newRole.value = ''
}

function onRole(r: CompanyRelation, ev: Event) {
  emit('update', r.id, { role: (ev.target as HTMLInputElement).value || null })
}
function onStatus(r: CompanyRelation, ev: Event) {
  emit('update', r.id, { status: (ev.target as HTMLSelectElement).value })
}
function onStart(r: CompanyRelation, ev: Event) {
  emit('update', r.id, { start_date: (ev.target as HTMLInputElement).value || null })
}
function onEnd(r: CompanyRelation, ev: Event) {
  emit('update', r.id, { end_date: (ev.target as HTMLInputElement).value || null })
}
function onRemove(r: CompanyRelation) {
  if (confirmRemoveId.value !== r.id) {
    confirmRemoveId.value = r.id
    return
  }
  confirmRemoveId.value = null
  emit('remove', r.id)
}
</script>

<template>
  <div class="cr-rows">
    <div v-if="!relations.length" class="sa-empty">
      {{ addable ? '还没有公司关系——下方搜索公司补挂。' : '还没有公司关系。' }}
    </div>
    <div v-for="r in relations" :key="r.id" class="sa-row cr-row">
      <span class="cr-name">{{ r.company_name }}</span>
      <input
        type="text"
        class="cr-input cr-role"
        :value="r.role ?? ''"
        placeholder="角色，如 经纪公司"
        @change="onRole(r, $event)"
      />
      <select class="cr-input cr-select" :value="r.status" @change="onStatus(r, $event)">
        <option v-for="s in STATUS_OPTIONS" :key="s.value" :value="s.value">{{ s.label }}</option>
      </select>
      <input type="date" class="cr-input" :value="r.start_date ?? ''" title="开始日期" @change="onStart(r, $event)" />
      <input type="date" class="cr-input" :value="r.end_date ?? ''" title="结束日期" @change="onEnd(r, $event)" />
      <button
        class="cr-del"
        type="button"
        :class="{ confirm: confirmRemoveId === r.id }"
        @click="onRemove(r)"
      >
        {{ confirmRemoveId === r.id ? '确认删除' : '×' }}
      </button>
    </div>

    <div v-if="addable" class="sa-add-row">
      <SaSelect
        v-model="pickedCompanyId"
        :fetch-options="searchCompanies"
        prefetch
        placeholder="搜索公司名称"
      />
      <input v-if="roleInput" v-model="newRole" type="text" class="sa-input" placeholder="角色（可选）" @keyup.enter="add" />
      <button class="sa-btn-add" type="button" :disabled="!pickedCompanyId" @click="add">{{ addLabel }}</button>
    </div>
  </div>
</template>

<style scoped>
.cr-rows { display: flex; flex-direction: column; gap: 6px; }
/* 行容器用统一的 .sa-row（白底 + --sa-border 描边 + 12px 圆角），这里只补行内编辑特有的换行与间距 */
.cr-row { gap: 8px; flex-wrap: wrap; }
.cr-name { font-weight: 800; font-size: 13px; flex-shrink: 0; min-width: 64px; }
.cr-input { border: none; background: transparent; color: var(--sa-text-primary); font-size: 12px; font-weight: 600; font-family: inherit; outline: none; border-bottom: 1px dashed var(--sa-border); padding: 1px 0; }
.cr-input:focus { border-bottom-color: var(--sa-accent); }
.cr-role { flex: 1; min-width: 120px; }
.cr-select { cursor: pointer; }
.cr-del { flex-shrink: 0; width: 22px; height: 22px; border: none; border-radius: 6px; background: none; color: var(--sa-text-tertiary); font-size: 13px; line-height: 1; cursor: pointer; }
.cr-del:hover { color: var(--dh-bad); background: rgba(208, 48, 80, 0.1); }
.cr-del.confirm { width: auto; padding: 0 8px; font-size: 10.5px; font-weight: 700; color: #fff; background: var(--dh-bad); }
</style>
