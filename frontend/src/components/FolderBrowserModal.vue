<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NButton, NCheckbox, NModal, useMessage } from 'naive-ui'
import { photosApi } from '@/api/photos'
import type {
  MtPhotosFolder,
  PhotoOwnerType,
  PhotoSection,
  PhotoSource,
} from '@/types/models'
import { ChevronRightOutlined, FolderOutlined } from '@/components/icons'

/**
 * 图片文件夹浏览器弹窗（Emby/Plex 式）。
 *
 * - 单击行直接进入子目录，面包屑可跳回任意层级
 * - 已绑定文件夹置灰（仍可点入穿过），绑定语义 = 绑定「当前所在文件夹」
 * - 父级已绑定且含子目录时给「父级已绑定」徽标，提示照片可能被重复扫描
 * - mode=local：数据来自 GET /photos/browse（白名单根路径内）
 * - mode=mt：数据来自 GET /photos/mtphotos/folders（按 parent_id 逐层）
 */
const props = defineProps<{
  show: boolean
  mode: 'local' | 'mt'
  ownerType: PhotoOwnerType
  ownerId: number
  section: PhotoSection
  sources: PhotoSource[]
}>()

const emit = defineEmits<{
  (e: 'update:show', v: boolean): void
  (e: 'bound'): void
}>()

const message = useMessage()

interface Level {
  key: string | null
  name: string
}

interface Row {
  key: string
  name: string
  photoCount: number
  subfolderCount: number
  bound: boolean
  parentBound: boolean
}

const stack = ref<Level[]>([{ key: null, name: '根目录' }])
const rows = ref<Row[]>([])
const loading = ref(false)
const errorText = ref('')
const recursive = ref(false)
const binding = ref(false)
const currentPath = ref('')
const currentMtId = ref<number | null>(null)
const currentMtName = ref('')

const isMt = computed(() => props.mode === 'mt')
const title = computed(() => (isMt.value ? '选择 MT Photos 文件夹' : '选择本地文件夹'))

// ---------- 绑定状态判定（与后端查重粒度一致：owner 级，不分官方/粉丝分区） ----------

function isMtFolderSource(s: PhotoSource) {
  return s.provider === 'mtphotos' && (s.folder_path || '').includes('/folder/')
}

function boundLocalPaths(): Set<string> {
  return new Set(
    props.sources.filter((s) => s.provider !== 'mtphotos').map((s) => s.folder_path),
  )
}

function boundMtFolderIds(): Set<string> {
  return new Set(
    props.sources.filter(isMtFolderSource).map((s) => String(s.external_id)),
  )
}

function recursiveLocalPaths(): string[] {
  return props.sources
    .filter((s) => s.provider !== 'mtphotos' && s.recursive)
    .map((s) => s.folder_path)
}

function recursiveMtFolderIds(): Set<string> {
  return new Set(
    props.sources
      .filter((s) => isMtFolderSource(s) && s.recursive)
      .map((s) => String(s.external_id)),
  )
}

// ---------- 当前层状态 ----------

const currentBound = computed(() => {
  if (isMt.value) {
    return currentMtId.value != null && boundMtFolderIds().has(String(currentMtId.value))
  }
  return !!currentPath.value && boundLocalPaths().has(currentPath.value)
})

const currentParentBound = computed(() => {
  if (currentBound.value) return false
  if (isMt.value) {
    const recursiveIds = recursiveMtFolderIds()
    // 祖先链 = 除当前层（栈顶）外的所有层
    return stack.value.slice(0, -1).some((l) => l.key != null && recursiveIds.has(l.key))
  }
  return (
    !!currentPath.value &&
    recursiveLocalPaths().some((p) => currentPath.value.startsWith(p + '/'))
  )
})

const canBind = computed(() => {
  if (binding.value || currentBound.value) return false
  return isMt.value ? currentMtId.value != null : !!currentPath.value
})

const displayPath = computed(() => {
  if (isMt.value) {
    return stack.value.map((l) => l.name).join(' / ') || '根目录'
  }
  return currentPath.value || '全部目录'
})

// ---------- 面包屑 ----------

const crumbs = computed<{ label: string; go: () => void }[]>(() => {
  if (isMt.value) {
    return stack.value.map((l, i) => ({
      label: l.name,
      go: () => {
        if (i < stack.value.length - 1) jumpStack(i)
      },
    }))
  }
  if (!currentPath.value) {
    return [{ label: '全部目录', go: () => {} }]
  }
  const parts = currentPath.value.split('/').filter(Boolean)
  let acc = ''
  return parts.map((part) => {
    acc += `/${part}`
    const target = acc
    return {
      label: part,
      go: () => {
        if (target !== currentPath.value) void loadLevel({ key: target, name: part })
      },
    }
  })
})

// ---------- 浏览 ----------

async function loadLevel(level: Level) {
  loading.value = true
  errorText.value = ''
  rows.value = []
  try {
    if (isMt.value) {
      const parentId = level.key == null ? null : Number(level.key)
      const folders: MtPhotosFolder[] = await photosApi.mtFolders(parentId)
      const boundIds = boundMtFolderIds()
      const recursiveIds = recursiveMtFolderIds()
      const ancestorRecursive = stack.value.some(
        (l) => l.key != null && recursiveIds.has(l.key),
      )
      rows.value = folders.map((f) => ({
        key: String(f.id),
        name: f.name,
        photoCount: f.count || 0,
        subfolderCount: f.subfolder_count || 0,
        bound: boundIds.has(String(f.id)),
        parentBound: ancestorRecursive,
      }))
      currentMtId.value = parentId
      currentMtName.value = level.name
      currentPath.value = ''
    } else {
      const result = await photosApi.browse(level.key)
      const bound = boundLocalPaths()
      const recPaths = recursiveLocalPaths()
      rows.value = result.entries.map((e) => ({
        key: e.path,
        name: e.name,
        photoCount: e.photo_count,
        subfolderCount: e.subfolder_count,
        bound: bound.has(e.path),
        parentBound: recPaths.some((p) => e.path.startsWith(p + '/')),
      }))
      currentPath.value = result.path
      currentMtId.value = null
    }
  } catch (e) {
    errorText.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

function enter(row: Row) {
  if (isMt.value) {
    stack.value = [...stack.value, { key: row.key, name: row.name }]
    void loadLevel({ key: row.key, name: row.name })
  } else {
    void loadLevel({ key: row.key, name: row.name })
  }
}

function jumpStack(index: number) {
  stack.value = stack.value.slice(0, index + 1)
  const top = stack.value[stack.value.length - 1]
  void loadLevel(top)
}

// ---------- 绑定 ----------

async function bindCurrent() {
  binding.value = true
  try {
    if (isMt.value) {
      if (currentMtId.value == null) {
        message.warning('请先进入要绑定的文件夹')
        return
      }
      await photosApi.bind({
        owner_type: props.ownerType,
        owner_id: props.ownerId,
        section: props.section,
        provider: 'mtphotos',
        mt_kind: 'folder',
        external_id: String(currentMtId.value),
        recursive: recursive.value,
      })
      message.success(`已绑定 MT 文件夹「${currentMtName.value}」，打开对应分区即可按帖浏览`)
    } else {
      if (!currentPath.value) {
        message.warning('请先进入要绑定的文件夹')
        return
      }
      await photosApi.bind({
        owner_type: props.ownerType,
        owner_id: props.ownerId,
        section: props.section,
        folder_path: currentPath.value,
        recursive: recursive.value,
        provider: 'folder',
      })
      message.success('已绑定，请扫描建立索引')
    }
    emit('bound')
    emit('update:show', false)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    binding.value = false
  }
}

// ---------- 弹窗打开/关闭 ----------

watch(
  () => props.show,
  (v) => {
    if (!v) return
    recursive.value = false
    stack.value = [{ key: null, name: '根目录' }]
    currentPath.value = ''
    currentMtId.value = null
    currentMtName.value = ''
    void loadLevel({ key: null, name: '根目录' })
  },
)
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    :title="title"
    style="width: min(600px, 94vw)"
    :bordered="false"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <div class="fb-body">
      <div class="fb-crumbs">
        <template v-for="(c, i) in crumbs" :key="i">
          <span v-if="i > 0" class="fb-crumb-sep">/</span>
          <button
            v-if="i < crumbs.length - 1"
            class="fb-crumb"
            type="button"
            @click="c.go"
          >
            {{ c.label }}
          </button>
          <span v-else class="fb-crumb-current">{{ c.label }}</span>
        </template>
      </div>

      <div class="fb-list">
        <div v-if="loading" class="fb-hint">加载中…</div>
        <div v-else-if="errorText" class="fb-error">{{ errorText }}</div>
        <div v-else-if="!rows.length" class="fb-hint">
          没有子文件夹，可以直接绑定当前文件夹
        </div>
        <template v-else>
          <button
            v-for="row in rows"
            :key="row.key"
            type="button"
            class="fb-row"
            :class="{ 'is-bound': row.bound }"
            :title="row.bound ? '该文件夹已绑定，点击仍可进入其子目录' : row.name"
            @click="enter(row)"
          >
            <FolderOutlined :size="16" class="fb-icon" />
            <span class="fb-name">{{ row.name }}</span>
            <span v-if="row.bound" class="fb-badge fb-badge-bound">已绑定</span>
            <span v-if="row.parentBound" class="fb-badge fb-badge-parent">父级已绑定</span>
            <span class="fb-meta">{{ row.photoCount }} 张 · {{ row.subfolderCount }} 子目录</span>
            <ChevronRightOutlined :size="14" class="fb-chev" />
          </button>
        </template>
      </div>

      <div v-if="currentParentBound" class="fb-warn">
        上级文件夹已绑定（含子目录），当前文件夹的照片可能被重复扫描
      </div>
    </div>

    <template #footer>
      <div class="fb-footer">
        <div class="fb-path">{{ displayPath }}</div>
        <div class="fb-actions">
          <n-checkbox v-model:checked="recursive">含子目录</n-checkbox>
          <n-button :disabled="binding" @click="emit('update:show', false)">取消</n-button>
          <n-button type="primary" :loading="binding" :disabled="!canBind" @click="bindCurrent">
            {{ currentBound ? '该文件夹已绑定' : '绑定当前文件夹' }}
          </n-button>
        </div>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.fb-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.fb-crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--sa-subtle);
  font-size: 12px;
}
.fb-crumb {
  border: none;
  background: transparent;
  color: var(--sa-accent);
  cursor: pointer;
  padding: 0;
  font-size: 12px;
}
.fb-crumb:hover {
  text-decoration: underline;
}
.fb-crumb-current {
  color: var(--sa-text-primary);
  font-weight: 600;
}
.fb-crumb-sep {
  color: var(--sa-text-tertiary);
}
.fb-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 340px;
  overflow-y: auto;
  min-height: 120px;
}
.fb-hint,
.fb-error {
  padding: 24px 12px;
  text-align: center;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}
.fb-error {
  color: #e05252;
}
.fb-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  text-align: left;
  border: none;
  background: transparent;
  border-radius: 8px;
  padding: 8px 10px;
  cursor: pointer;
  color: var(--sa-text-primary);
  font-size: 13px;
}
.fb-row:hover {
  background: var(--sa-subtle);
}
.fb-row.is-bound {
  opacity: 0.55;
}
.fb-icon {
  color: var(--sa-text-tertiary);
  flex-shrink: 0;
}
.fb-name {
  font-weight: 500;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fb-badge {
  flex-shrink: 0;
  font-size: 11px;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 999px;
  white-space: nowrap;
}
.fb-badge-bound {
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border);
  color: var(--sa-text-tertiary);
}
.fb-badge-parent {
  background: rgba(240, 160, 32, 0.12);
  border: 1px solid rgba(240, 160, 32, 0.4);
  color: #b8791a;
}
.fb-meta {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
}
.fb-chev {
  flex-shrink: 0;
  color: var(--sa-text-tertiary);
}
.fb-warn {
  padding: 8px 10px;
  border-radius: 8px;
  font-size: 12px;
  background: rgba(240, 160, 32, 0.1);
  border: 1px solid rgba(240, 160, 32, 0.35);
  color: #b8791a;
}
.fb-footer {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.fb-path {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  word-break: break-all;
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
}
.fb-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.fb-actions .n-button {
  margin-left: auto;
}
.fb-actions .n-button + .n-button {
  margin-left: 0;
}

@media (max-width: 768px) {
  .fb-meta {
    display: none;
  }
}
</style>
