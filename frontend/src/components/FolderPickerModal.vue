<script setup lang="ts">
/**
 * 文件夹选择对话框 —— 通用软件式（左侧常用位置 + 右侧当前目录逐级浏览）。
 *
 * 数据来自 /library/browse-dirs（只读逐级列子目录，防穿越由后端把守）；
 * 选中 = 「使用此目录」按钮或双击右侧空白快捷项，替代原平铺列表。
 */
import { computed, ref, watch } from 'vue'
import { NButton, NSpace } from 'naive-ui'
import { libraryApi } from '@/api/library'
import { FolderOutlined, SearchOutlined } from '@/components/icons'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ 'update:show': [v: boolean]; select: [dir: string] }>()

const LIB_ROOT_LABEL = '资料库根目录'

const curRel = ref('')
const dirs = ref<string[]>([])
const loading = ref(false)
const loadError = ref('')
// 面包屑分段（rel 拆分）
const crumbs = computed(() =>
  curRel.value ? curRel.value.split('/').filter(Boolean).map((name, i, arr) => ({
    name,
    rel: arr.slice(0, i + 1).join('/'),
  })) : [],
)
// 搜索过滤当前层
const filter = ref('')
const shownDirs = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return dirs.value
  return dirs.value.filter((d) => d.toLowerCase().includes(q))
})

async function enter(rel: string) {
  loading.value = true
  loadError.value = ''
  try {
    const r = await libraryApi.browseDirs(rel)
    curRel.value = r.rel
    dirs.value = r.dirs
    filter.value = ''
  } catch (e) {
    loadError.value = (e as Error).message || '目录读取失败'
  } finally {
    loading.value = false
  }
}

// 快捷位置 = 库内一级目录（常用入口）；根目录本身恒在列表首位
const quickLinks = ref<string[]>([])
watch(
  () => props.show,
  async (open) => {
    if (!open) return
    filter.value = ''
    await enter('')
    try {
      const r = await libraryApi.browseDirs('')
      quickLinks.value = r.dirs.slice(0, 12)
    } catch {
      quickLinks.value = []
    }
  },
  { immediate: true },
)

function chooseCurrent() {
  emit('select', curRel.value)
  emit('update:show', false)
}
</script>

<template>
  <n-modal
    :show="props.show"
    preset="card"
    title="选择目标文件夹"
    :style="{ width: 'min(720px, calc(100vw - 24px))' }"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <div class="fp">
      <!-- 左侧：常用位置 -->
      <aside class="fp-side">
        <p class="fp-side-title">常用位置</p>
        <button class="fp-quick" type="button" :class="{ on: curRel === '' }" @click="enter('')">
          <FolderOutlined :size="14" />
          {{ LIB_ROOT_LABEL }}
        </button>
        <button
          v-for="q in quickLinks"
          :key="q"
          class="fp-quick"
          type="button"
          :class="{ on: curRel === q }"
          :title="q"
          @click="enter(q)"
        >
          <FolderOutlined :size="14" />
          <span class="fp-quick-name">{{ q }}</span>
        </button>
      </aside>

      <!-- 右侧：面包屑 + 当前层目录 -->
      <div class="fp-main">
        <div class="fp-crumb">
          <button class="fp-crumb-item" type="button" @click="enter('')">资料库</button>
          <template v-for="c in crumbs" :key="c.rel">
            <span class="fp-crumb-sep">›</span>
            <button class="fp-crumb-item" type="button" @click="enter(c.rel)">{{ c.name }}</button>
          </template>
          <span class="fp-spacer" />
          <div class="fp-filter">
            <SearchOutlined :size="13" />
            <input v-model="filter" type="text" placeholder="筛选" />
          </div>
        </div>

        <div class="fp-list">
          <div v-if="loading" class="fp-empty">读取中…</div>
          <div v-else-if="loadError" class="fp-empty">{{ loadError }}</div>
          <template v-else-if="shownDirs.length">
            <button
              v-for="d in shownDirs"
              :key="d"
              class="fp-row"
              type="button"
              @dblclick="enter(curRel ? `${curRel}/${d}` : d)"
              @click="enter(curRel ? `${curRel}/${d}` : d)"
            >
              <FolderOutlined :size="15" />
              <span class="fp-row-name">{{ d }}</span>
            </button>
          </template>
          <div v-else class="fp-empty">
            {{ curRel ? '此目录下没有子文件夹' : '资料库还没有子文件夹' }}
          </div>
        </div>

        <p class="fp-hint">
          单击进入 · 选「{{ curRel || LIB_ROOT_LABEL }}」为入库目录
        </p>
      </div>
    </div>

    <template #footer>
      <n-space justify="space-between" align="center">
        <code class="fp-current">{{ curRel || LIB_ROOT_LABEL }}</code>
        <n-space>
          <n-button @click="emit('update:show', false)">取消</n-button>
          <n-button type="primary" @click="chooseCurrent">使用此目录</n-button>
        </n-space>
      </n-space>
    </template>
  </n-modal>
</template>

<style scoped>
.fp {
  display: grid;
  grid-template-columns: 168px minmax(0, 1fr);
  gap: 14px;
  min-height: 340px;
}
/* 左侧常用位置 */
.fp-side {
  border-right: 1px solid var(--sa-border-subtle);
  padding-right: 12px;
  overflow-y: auto;
}
.fp-side-title {
  margin: 0 0 8px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--sa-text-tertiary);
}
.fp-quick {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 9px;
  margin-bottom: 2px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}
.fp-quick:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
.fp-quick.on {
  background: var(--sa-accent-subtle);
  color: var(--sa-accent);
}
.fp-quick-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 右侧主区 */
.fp-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.fp-crumb {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  min-height: 30px;
}
.fp-crumb-item {
  border: none;
  background: transparent;
  color: var(--sa-accent);
  font-size: 13px;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 6px;
}
.fp-crumb-item:hover {
  background: var(--sa-hover);
}
.fp-crumb-sep {
  color: var(--sa-text-tertiary);
  font-size: 12px;
}
.fp-spacer {
  flex: 1;
}
.fp-filter {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 999px;
  background: var(--sa-subtle);
  color: var(--sa-text-tertiary);
}
.fp-filter input {
  width: 90px;
  border: none;
  background: transparent;
  color: var(--sa-text-primary);
  font-size: 12px;
  outline: none;
}
.fp-list {
  flex: 1;
  min-height: 220px;
  max-height: 320px;
  overflow-y: auto;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 10px;
  background: var(--sa-elevated);
  padding: 6px;
}
.fp-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--sa-text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.1s;
}
.fp-row:hover {
  background: var(--sa-hover);
}
.fp-row svg {
  color: var(--sa-accent);
  flex: none;
}
.fp-row-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fp-empty {
  padding: 40px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}
.fp-hint {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.fp-current {
  font-size: 12px;
  color: var(--sa-text-secondary);
  background: var(--sa-subtle);
  border-radius: 6px;
  padding: 4px 10px;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 手机：左栏收进顶部横排 */
@media (max-width: 640px) {
  .fp {
    grid-template-columns: 1fr;
  }
  .fp-side {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    border-right: none;
    padding-right: 0;
    padding-bottom: 8px;
  }
  .fp-side-title {
    display: none;
  }
  .fp-quick {
    width: auto;
    flex: none;
    border: 1px solid var(--sa-border-subtle);
  }
}
</style>
