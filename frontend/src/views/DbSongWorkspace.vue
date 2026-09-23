<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SaSelect from '@/components/SaSelect.vue'
import { MusicNoteOutlined } from '@/components/icons'
import SaHeader from '@/components/SaHeader.vue'
import DbTabs from '@/components/db/DbTabs.vue'
import SaOverflowTabs from '@/components/SaOverflowTabs.vue'
import SaDatePicker from '@/components/SaDatePicker.vue'
import {
  songWorkspaceApi,
  type SongWorkspaceResponse,
} from '@/api/dbViews'
import { songsApi } from '@/api/songs'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'
import { entityLocksApi, type EntityLockMap } from '@/api/entityLocks'
import { formatDuration } from '@/utils/format'

const route = useRoute()
const router = useRouter()

// 内嵌进设置页·资料库时为 true：uid 由 props 传入，且去掉整屏外壳（站点头 / 页签行）
const props = defineProps<{ uid?: string; embedded?: boolean }>()

const uid = computed(() => props.uid || String(route.params.uid || ''))
const data = ref<SongWorkspaceResponse | null>(null)
const locks = ref<EntityLockMap>({})
const loading = ref(false)
const loadError = ref('')
const savingField = ref('')
const saveError = ref('')

async function load() {
  if (!uid.value) return
  loading.value = true
  loadError.value = ''
  try {
    data.value = await songWorkspaceApi.getSongWorkspace(uid.value)
    if (data.value) {
      locks.value = await entityLocksApi.get('songs', data.value.song.id)
    }
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载工作台失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
onMounted(() => {
  void searchPerformers('')
})
watch(uid, load)

interface FieldDef {
  key: string
  label: string
  kind: 'date' | 'text' | 'select' | 'number'
  options?: string[]
  placeholder?: string
}

const SONG_TYPE_LABELS: Record<string, string> = {
  Title: '主打',
  'B-side': 'B面曲',
  Intro: 'Intro',
  Outro: 'Outro',
  Interlude: '间奏曲',
  Other: '其他',
}

const ROLE_LABELS: Record<string, string> = {
  PrimaryArtist: '主唱',
  FeaturedArtist: 'feat.',
  Remixer: '混音',
  CoverArtist: '翻唱',
}

const FIELDS: FieldDef[] = [
  { key: 'release_date', label: '发行日期', kind: 'date' },
  {
    key: 'song_type',
    label: '歌曲类型',
    kind: 'select',
    options: ['Title', 'B-side', 'Intro', 'Outro', 'Interlude', 'Other'],
  },
  { key: 'korean_name', label: '韩文名', kind: 'text', placeholder: '点击填写' },
  { key: 'english_name', label: '英文名', kind: 'text', placeholder: '点击填写' },
  { key: 'chinese_name', label: '中文名', kind: 'text', placeholder: '点击填写' },
  { key: 'duration', label: '时长（秒）', kind: 'number', placeholder: '如 213' },
]

function fieldValue(key: string): string {
  const s = data.value?.song
  if (!s) return ''
  const v = (s as unknown as Record<string, string | number | null>)[key]
  return v == null ? '' : String(v)
}

async function saveField(key: string, value: string | number | null) {
  const s = data.value?.song
  if (!s) return
  saveError.value = ''
  savingField.value = key
  try {
    const payload: Record<string, unknown> =
      key === 'duration'
        ? { duration: value === '' || value === null ? null : Number(value) }
        : { [key]: value === '' ? null : value }
    await songsApi.update(s.id, payload)
    locks.value = await entityLocksApi.put('songs', s.id, { ...locks.value, [key]: true })
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    savingField.value = ''
  }
}

function onFieldChange(f: FieldDef, ev: Event) {
  saveField(f.key, (ev.target as HTMLInputElement).value)
}

async function unlock(key: string) {
  const s = data.value?.song
  if (!s) return
  const next = { ...locks.value }
  delete next[key]
  locks.value = await entityLocksApi.put('songs', s.id, next)
}

const filledFieldKeys = computed(() =>
  FIELDS.map((f) => f.key)
    .concat('description')
    .filter((k) => fieldValue(k) !== ''),
)

async function lockAllFilled() {
  const s = data.value?.song
  if (!s) return
  saveError.value = ''
  try {
    const next: EntityLockMap = { ...locks.value }
    for (const k of filledFieldKeys.value) next[k] = true
    locks.value = await entityLocksApi.put('songs', s.id, next)
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '锁定失败'
  }
}

async function unlockAll() {
  const s = data.value?.song
  if (!s) return
  locks.value = await entityLocksApi.put('songs', s.id, {})
}

// ===== 演出者关系增删改 =====
const performerOptions = ref<{ value: string; label: string }[]>([])
const performerLoading = ref(false)
const pickedPerformer = ref<string | null>(null)
const newPerformerRole = ref('PrimaryArtist')

async function searchPerformers(q: string) {
  performerLoading.value = true
  try {
    const term = q.trim()
    const [artists, groups] = term
      ? await Promise.all([artistsApi.fuzzy(term), groupsApi.fuzzy(term)])
      : await Promise.all([artistsApi.brief(), groupsApi.brief()])
    performerOptions.value = [
      ...groups.map((g) => ({ value: `group:${g.id}`, label: `组合 · ${g.chinese_name || g.name}` })),
      ...artists.map((a) => ({ value: `artist:${a.id}`, label: `solo · ${a.chinese_name || a.stage_name || a.name}` })),
    ]
  } catch {
    performerOptions.value = []
  } finally {
    performerLoading.value = false
  }
}

async function addPerformer() {
  const s = data.value?.song
  if (!s || !pickedPerformer.value) return
  saveError.value = ''
  const idx = pickedPerformer.value.indexOf(':')
  const type = pickedPerformer.value.slice(0, idx)
  const id = Number(pickedPerformer.value.slice(idx + 1))
  try {
    await songsApi.createRelation({
      song_id: s.id,
      artist_id: type === 'artist' ? id : null,
      group_id: type === 'group' ? id : null,
      role: newPerformerRole.value,
      order: data.value?.performers.length ?? 0,
    })
    pickedPerformer.value = null
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '添加演出者失败'
  }
}

async function onPerformerRole(p: SongWorkspaceResponse['performers'][number], ev: Event) {
  const s = data.value?.song
  if (!s) return
  saveError.value = ''
  const role = (ev.target as HTMLSelectElement).value
  try {
    await songsApi.removeRelation(p.relation_id)
    if (!p.is_dangling) {
      await songsApi.createRelation({
        song_id: s.id,
        artist_id: p.subject_type === 'artist' ? p.subject_id : null,
        group_id: p.subject_type === 'group' ? p.subject_id : null,
        role,
        order: p.order,
      })
    }
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '修改角色失败'
  }
}

async function removePerformer(p: SongWorkspaceResponse['performers'][number]) {
  saveError.value = ''
  try {
    await songsApi.removeRelation(p.relation_id)
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '移除演出者失败'
  }
}

// ===== Credits =====
const newCreditName = ref('')
const newCreditRole = ref('')
const addingCredit = ref(false)

async function addCredit() {
  const s = data.value?.song
  if (!s || !newCreditName.value.trim() || !newCreditRole.value.trim()) return
  addingCredit.value = true
  saveError.value = ''
  try {
    await songsApi.createCredit(s.id, {
      name: newCreditName.value.trim(),
      role: newCreditRole.value.trim(),
    })
    newCreditName.value = ''
    newCreditRole.value = ''
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '添加创作人员失败'
  } finally {
    addingCredit.value = false
  }
}

async function removeCredit(creditId: number) {
  saveError.value = ''
  try {
    await songsApi.removeCredit(creditId)
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '移除失败'
  }
}

const issues = computed(() => data.value?.issues ?? [])
const lockedFields = computed(() => Object.keys(locks.value).filter((k) => locks.value[k]))
const issueTotal = computed(() => {
  const c = data.value?.issue_counts
  return c ? (c.error || 0) + (c.warning || 0) : 0
})
const WS_TABS = [
  { key: 'base', label: '基本信息' },
  { key: 'performers', label: '演出者' },
  { key: 'albums', label: '专辑' },
  { key: 'credits', label: 'Credits' },
]
const wsTab = ref('base')
const sevMeta: Record<string, { label: string; cls: string }> = {
  error: { label: '错误', cls: 'sev-error' },
  warning: { label: '缺失', cls: 'sev-warning' },
  hint: { label: '提示', cls: 'sev-hint' },
}

function completenessColor(score: number) {
  if (score >= 80) return 'var(--dh-ok)'
  if (score >= 50) return 'var(--dh-warn)'
  return 'var(--dh-bad)'
}
</script>

<template>
  <div class="mv-page" :class="{ 'mv-page--embedded': embedded }">
    <SaHeader v-if="!embedded" />
    <div class="mv-container">
      <div v-if="!embedded" class="ws-tab-row">
        <DbTabs />
        <button class="back-btn" @click="router.push('/db/songs')">← 返回列表</button>
      </div>

      <div v-if="loadError" class="load-error">{{ loadError }}</div>
      <div v-else-if="loading && !data" class="list-loading">加载工作台…</div>

      <template v-else-if="data">
        <!-- ═══ 身份节点条 ═══ -->
        <section class="ident glass">
          <div class="ident-avatar">
            <MusicNoteOutlined :size="22" />
          </div>
          <div class="ident-main">
            <h1>
              {{ data.song.name }}
              <span v-if="data.song.korean_name" class="h-sub">{{ data.song.korean_name }}</span>
            </h1>
            <div class="ident-meta">
              <span class="meta-pill" :class="{ warn: !data.song.release_date }">
                {{ data.song.release_date ? `${data.song.release_date} 发行` : '缺发行日期' }}
              </span>
              <span class="meta-pill">{{ data.song.song_type ? (SONG_TYPE_LABELS[data.song.song_type] || data.song.song_type) : '未设类型' }}</span>
              <span class="meta-pill">{{ data.song.duration != null ? formatDuration(data.song.duration) : '时长 —' }}</span>
              <span class="meta-pill" :class="{ warn: !data.performers.length }">
                {{ data.performers.length ? `${data.performers.length} 位演出者` : '缺演唱者' }}
              </span>
            </div>
          </div>
          <div class="ident-stats">
            <div class="stat"><b>{{ data.albums.length }}</b><span>专辑</span></div>
            <div class="stat"><b>{{ data.credits.length }}</b><span>Credits</span></div>
            <div class="stat score" :style="{ color: completenessColor(data.completeness) }">
              <b>{{ data.completeness }}</b><span>完整度</span>
            </div>
          </div>
        </section>

        <div class="board">
          <div class="board-main">
            <div class="ws-tabs">
              <SaOverflowTabs v-model="wsTab" :items="WS_TABS" aria-label="工作台板块" />
            </div>

            <template v-if="wsTab === 'base'">
<!-- ① 基本信息 -->
            <section class="node node-teal">
              <header class="node-head">
                <span class="node-dot dot-teal" />
                <b>歌曲档案</b>
                <span class="node-note">点击即改 · 保存后自动 🔒，AI 补全不再覆盖</span>
              </header>
              <div class="node-body">
                <div v-if="saveError" class="form-error">{{ saveError }}</div>
                <div class="field-grid">
                  <label v-for="f in FIELDS" :key="f.key" class="field-card" :class="{ locked: locks[f.key], 'field-card--date': f.kind === 'date' }">
                    <span class="f-label">{{ f.label }}<i v-if="locks[f.key]" class="lock-mark">🔒</i></span>
                    <select v-if="f.kind === 'select'" class="f-input" :value="fieldValue(f.key)" :disabled="savingField === f.key" @change="onFieldChange(f, $event)">
                      <option value="">未填写</option>
                      <option v-for="opt in f.options" :key="opt" :value="opt">{{ SONG_TYPE_LABELS[opt] || opt }}</option>
                    </select>
                    <SaDatePicker
                      v-else-if="f.kind === 'date'"
                      variant="bordered"
                      :model-value="fieldValue(f.key)"
                      :disabled="savingField === f.key"
                      @update:model-value="(v: string | null) => saveField(f.key, v)"
                    />
                    <input v-else-if="f.kind === 'number'" class="f-input" type="number" min="0" :value="fieldValue(f.key)" :placeholder="f.placeholder || ''" :disabled="savingField === f.key" @change="onFieldChange(f, $event)" />
                    <input v-else class="f-input" type="text" :value="fieldValue(f.key)" :placeholder="f.placeholder || '点击填写'" :disabled="savingField === f.key" @change="onFieldChange(f, $event)" />
                  </label>
                  <label class="field-card wide" :class="{ locked: locks['description'] }">
                    <span class="f-label">描述 {{ locks['description'] ? '🔒' : '' }}</span>
                    <textarea class="f-input" rows="2" :value="data.song.description ?? ''" placeholder="歌曲介绍" :disabled="savingField === 'description'" @change="saveField('description', ($event.target as HTMLTextAreaElement).value || null)" />
                  </label>
                </div>
              </div>
            </section>

            
            <section class="node checkup-node">
              <header class="node-head">
                <b>体检</b>
                <span class="node-note">问题 · 字段锁</span>
              </header>
              <div class="node-body">
                <div class="ws-sum">
                  <span class="ws-sum__i">
                    <i class="ws-dot" :class="issueTotal ? (data.issue_counts.error ? 'ws-dot--bad' : 'ws-dot--warn') : 'ws-dot--ok'" />
                    {{ issueTotal }} 问题
                  </span>
                  <span class="ws-sum__i"><i class="ws-dot ws-dot--lock" />锁 {{ lockedFields.length }}</span>
                </div>
                <details class="ws-sec" :open="issueTotal > 0">
                  <summary>问题 <span v-if="issueTotal" class="cnt-badge">{{ issueTotal }}</span></summary>
                  <div v-if="!issues.length" class="side-empty">没有问题</div>
                  <div v-for="(issue, i) in issues" :key="i" class="issue">
                    <span class="sev-badge" :class="sevMeta[issue.severity]?.cls">{{ sevMeta[issue.severity]?.label }}</span>
                    <div class="issue-tx">
                      <b>{{ issue.title }}</b>
                      <div v-if="issue.suggestion" class="issue-sub">{{ issue.suggestion }}</div>
                    </div>
                  </div>
                </details>
                <details class="ws-sec">
                  <summary>字段锁 <span v-if="lockedFields.length" class="cnt-badge">{{ lockedFields.length }}</span></summary>
                  <div class="lock-actions">
                    <button class="lock-all-btn" type="button" :disabled="!filledFieldKeys.length" @click="lockAllFilled">
                      一键锁定已填字段（{{ filledFieldKeys.length }}）
                    </button>
                    <button v-if="lockedFields.length" class="unlock-btn" type="button" @click="unlockAll">全部解锁</button>
                  </div>
                  <div v-if="!lockedFields.length" class="side-empty">
                    还没有锁定字段。手动修改过的字段会自动上锁；AI 补全与再抓取只跳过锁定字段。
                  </div>
                  <div v-for="k in lockedFields" :key="k" class="lock-row">
                    <span>{{ k }}</span>
                    <button class="unlock-btn" type="button" @click="unlock(k)">解锁</button>
                  </div>
                </details>
              </div>
            </section>
            </template>

            <template v-else-if="wsTab === 'performers'">
<!-- ② 演出者 -->
            <section class="node node-accent">
              <header class="node-head">
                <span class="node-dot dot-accent" />
                <b>演出者</b>
                <span class="node-note">演唱组合 / solo 艺人 · 角色下拉即改 · × 移除</span>
              </header>
              <div class="node-body">
                <div v-if="!data.performers.length" class="side-empty">还没有演出者——下方搜索补挂。</div>
                <div v-else class="track-list">
                  <div
                    v-for="p in data.performers"
                    :key="p.relation_id"
                    class="track-row"
                    :class="{ dangling: p.is_dangling }"
                  >
                    <span class="tk-no">{{ ROLE_LABELS[p.role] || p.role }}</span>
                    <router-link
                      v-if="p.subject_type === 'group' && p.subject_uid"
                      :to="`/db/groups/${p.subject_uid}`"
                      class="tk-name"
                    >{{ p.name }}</router-link>
                    <router-link
                      v-else-if="p.subject_type === 'artist' && p.subject_uid"
                      :to="`/db/artists/${p.subject_uid}`"
                      class="tk-name"
                    >{{ p.name }}</router-link>
                    <span v-else class="tk-name missing-text">悬空关系</span>
                    <select class="tk-role" :value="p.role" @change="onPerformerRole(p, $event)">
                      <option v-for="(label, key) in ROLE_LABELS" :key="key" :value="key">{{ label }}</option>
                    </select>
                    <button class="tk-del" type="button" @click="removePerformer(p)">×</button>
                  </div>
                </div>

                <div class="add-performer">
                  <SaSelect
                    v-model="pickedPerformer"
                    :options="performerOptions"
                    :loading="performerLoading"
                    filterable
                    remote
                    placeholder="搜索组合 / 艺人"
                    @search="searchPerformers"
                  />
                  <select v-model="newPerformerRole" class="tk-role fixed">
                    <option v-for="(label, key) in ROLE_LABELS" :key="key" :value="key">{{ label }}</option>
                  </select>
                  <button class="add-btn" type="button" :disabled="!pickedPerformer" @click="addPerformer">添加</button>
                </div>
              </div>
            </section>

                        </template>

            <template v-else-if="wsTab === 'albums'">
<!-- ③ 专辑关联 -->
            <section class="node node-teal" >
              <header class="node-head">
                <span class="node-dot dot-teal" />
                <b>专辑关联</b>
                <span class="node-note">点击进入专辑工作台 · 曲目增删在专辑侧管理</span>
              </header>
              <div class="node-body video-strip">
                <div v-if="!data.albums.length" class="side-empty">暂无专辑关联</div>
                <template v-else>
                <router-link v-for="al in data.albums" :key="al.track_id" :to="`/db/albums/${al.uid}`" class="video-tile">
                  <div class="vd-name">{{ al.name }}</div>
                  <div class="vd-meta">{{ al.release_date || '缺日期' }} · 第 {{ al.track_number }} 首</div>
                </router-link>
                </template>
              </div>
            </section>

                        </template>

            <template v-else-if="wsTab === 'credits'">
<!-- ④ Credits -->
            <section class="node node-teal">
              <header class="node-head">
                <span class="node-dot dot-teal" />
                <b>创作人员 Credits</b>
                <span class="node-note">作词 / 作曲 / 编曲等 · 填姓名与角色后添加</span>
              </header>
              <div class="node-body">
                <div v-if="!data.credits.length" class="side-empty">还没有创作人员记录。</div>
                <div v-else class="track-list">
                  <div v-for="c in data.credits" :key="c.id" class="track-row">
                    <span class="tk-no">{{ c.role }}</span>
                    <span class="tk-name">{{ c.name }}<template v-if="c.artist_name">（{{ c.artist_name }}）</template></span>
                    <button class="tk-del" type="button" @click="removeCredit(c.id)">×</button>
                  </div>
                </div>
                <div class="add-performer">
                  <input v-model="newCreditName" type="text" class="credit-input" placeholder="姓名，如 이수만" @keyup.enter="addCredit" />
                  <input v-model="newCreditRole" type="text" class="credit-input" placeholder="角色，如 作曲" @keyup.enter="addCredit" />
                  <button class="add-btn" type="button" :disabled="!newCreditName.trim() || !newCreditRole.trim() || addingCredit" @click="addCredit">
                    {{ addingCredit ? '添加中…' : '添加' }}
                  </button>
                </div>
              </div>
            </section>
                      </template>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.mv-page {
  min-height: 100vh;
  --dh-ok: #18a058;
  --dh-warn: #d97706;
  --dh-bad: #d03050;
}
/* 内嵌进设置页·资料库：不占整屏、去掉自身容器留白（面板自带 padding） */
.mv-page--embedded { min-height: 0; }
.mv-page--embedded .mv-container { padding: 0; }
.mv-container { max-width: 1280px; margin: 0 auto; padding: 14px 22px 96px; }
.ws-tab-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.back-btn { border: none; background: none; color: var(--sa-text-tertiary); font-size: 12px; cursor: pointer; }
.load-error { padding: 12px 16px; border-radius: 10px; background: rgba(208,48,80,.1); color: var(--dh-bad); font-size: 13px; margin-bottom: 16px; }
.list-loading { padding: 60px 0; text-align: center; color: var(--sa-text-tertiary); font-size: 13px; }
.glass { background: var(--sa-elevated); border: 1px solid var(--sa-border-subtle); border-radius: 16px; }

.ident { display: flex; align-items: center; gap: 10px 14px; flex-wrap: wrap; padding: 13px 20px; margin-bottom: 10px; }
.ident-avatar { width: 52px; height: 52px; border-radius: 16px; flex-shrink: 0; background: var(--sa-subtle); color: var(--sa-accent); display: flex; align-items: center; justify-content: center; }
.ident-main { flex: 1; min-width: 0; }
.ident-main h1 { margin: 0; font-size: 20px; overflow-wrap: anywhere; }
.h-sub { font-size: 12px; color: var(--sa-text-tertiary); font-weight: 400; margin-left: 6px; }
.ident-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.meta-pill { font-size: 11px; padding: 2px 9px; border-radius: 6px; background: var(--sa-subtle); color: var(--sa-text-secondary); }
.meta-pill.warn { background: rgba(217,119,6,.12); color: var(--dh-warn); }
.ident-stats { display: flex; gap: 10px 15px; row-gap: 6px; flex-wrap: wrap; align-items: center; }
.stat { text-align: center; min-width: 38px; }
.stat b { display: block; font-size: 17px; }
.stat span { font-size: 10px; color: var(--sa-text-tertiary); letter-spacing: 1px; }

.board { display: grid; grid-template-columns: minmax(0, 1fr); gap: 11px; align-items: start; }
.ws-tabs { margin-bottom: 4px; }
.checkup-node .node-body { display: flex; flex-direction: column; gap: 4px; }
.board-main { display: flex; flex-direction: column; gap: 9px; min-width: 0; }
.rail { display: flex; flex-direction: column; gap: 9px; position: sticky; top: 60px; }

.node { background: var(--sa-elevated); border: 1px solid var(--sa-border-subtle); border-radius: 13px; overflow: hidden; border-left: 3px solid var(--sa-border); }
.node-accent { border-left-color: var(--sa-accent); }
.node-teal { border-left-color: #18a058; }
.node-head { display: flex; align-items: baseline; gap: 4px 8px; flex-wrap: wrap; padding: 10px 15px; border-bottom: 1px solid var(--sa-border-subtle); }
.node-head b { font-size: 13px; }
.node-dot { width: 8px; height: 8px; border-radius: 50%; align-self: center; flex-shrink: 0; }
.dot-accent { background: var(--sa-accent); }
.dot-teal { background: #18a058; }
.node-note { flex: 1; min-width: 0; font-size: 11px; color: var(--sa-text-tertiary); }
.node-body { padding: 11px 14px 13px; }
.form-error { margin: 7px 0; padding: 6px 11px; border-radius: 8px; background: rgba(208,48,80,.1); color: var(--dh-bad); font-size: 12px; }

.field-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 7px 9px; }
.field-card { display: flex; flex-direction: column; gap: 3px; padding: 6px 10px; border: 1px solid var(--sa-border-subtle); border-radius: 10px; transition: border-color .15s; min-width: 0; }
.field-card:hover { border-color: var(--sa-accent); }
.field-card.locked { background: var(--sa-subtle); }
.field-card.wide { grid-column: 1 / -1; }
.f-label { font-size: 10px; font-weight: 800; letter-spacing: 1.2px; color: var(--sa-text-tertiary); display: flex; align-items: center; gap: 4px; }
.lock-mark { font-style: normal; font-size: 9px; }
.f-input { width: 100%; border: none; background: transparent; color: var(--sa-text-primary); font-size: 12.5px; font-weight: 600; font-family: inherit; outline: none; padding: 1px 0; border-bottom: 1px dashed var(--sa-border); resize: vertical; }
.f-input:focus { border-bottom-color: var(--sa-accent); }

.track-list { display: flex; flex-direction: column; }
.track-row { display: flex; align-items: center; gap: 10px; padding: 6px 8px; border-radius: 8px; }
.track-row:hover { background: var(--sa-subtle); }
.track-row.dangling { opacity: .55; }
.tk-no { flex-shrink: 0; min-width: 52px; font-size: 11px; font-weight: 800; color: var(--sa-text-tertiary); }
.tk-name { flex: 1; min-width: 0; font-size: 12.5px; font-weight: 600; color: var(--sa-text-primary); text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
a.tk-name:hover { color: var(--sa-accent); }
.tk-role { flex-shrink: 0; border: 1px solid var(--sa-border-subtle); border-radius: 7px; background: var(--sa-bg); color: var(--sa-text-secondary); font-size: 11.5px; font-family: inherit; padding: 2px 6px; cursor: pointer; }
.tk-role.fixed { width: 90px; }
.tk-del { flex-shrink: 0; width: 22px; height: 22px; border: none; border-radius: 6px; background: none; color: var(--sa-text-tertiary); font-size: 13px; line-height: 1; cursor: pointer; }
.tk-del:hover { color: var(--dh-bad); background: rgba(208,48,80,.1); }
.missing-text { font-size: 12px; color: var(--sa-text-tertiary); font-style: italic; }
.add-performer { display: flex; gap: 8px; align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--sa-border-subtle); }
.add-performer :deep(.n-select) { flex: 1; min-width: 0; }
.credit-input { flex: 1; min-width: 0; height: 34px; padding: 0 10px; border: 1px solid var(--sa-border-subtle); border-radius: 8px; background: var(--sa-bg); color: var(--sa-text-primary); font-size: 12.5px; font-family: inherit; outline: none; }
.credit-input:focus { border-color: var(--sa-accent); }
.add-btn { flex-shrink: 0; height: 34px; padding: 0 14px; border: 1px solid var(--sa-accent); background: var(--sa-accent-subtle); color: var(--sa-accent); border-radius: 8px; font-size: 12px; font-weight: 700; cursor: pointer; font-family: inherit; }
.add-btn:disabled { opacity: .45; cursor: default; }

.video-strip { display: grid; grid-template-columns: repeat(auto-fill, minmax(185px, 1fr)); gap: 7px; }
.video-tile { border: 1px solid var(--sa-border-subtle); border-radius: 10px; padding: 7px 11px; background: var(--sa-bg); text-decoration: none; color: var(--sa-text-primary); }
.video-tile:hover { border-color: var(--sa-accent); }
.vd-name { font-weight: 700; font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vd-meta { font-size: 10.5px; color: var(--sa-text-tertiary); margin-top: 2px; }

.rail-card { background: var(--sa-elevated); border: 1px solid var(--sa-border-subtle); border-radius: 13px; padding: 13px 15px; }
.rail-card h3 { display: flex; align-items: center; gap: 7px; font-size: 13px; margin: 0 0 8px; }
.cnt-badge { margin-left: auto; font-size: 11px; color: var(--dh-bad); background: rgba(208,48,80,.12); border-radius: 6px; padding: 1px 7px; }
.side-empty { font-size: 11.5px; color: var(--sa-text-tertiary); line-height: 1.7; }
.fix { display: flex; gap: 8px; padding: 6px 0; border-top: 1px dashed var(--sa-border-subtle); align-items: flex-start; }
.fix:first-of-type { border-top: none; }
.sev-badge { flex-shrink: 0; font-size: 10px; font-weight: 800; border-radius: 6px; padding: 2px 7px; margin-top: 1px; }
.sev-badge.sev-error { background: rgba(208,48,80,.12); color: var(--dh-bad); }
.sev-badge.sev-warning { background: rgba(217,119,6,.12); color: var(--dh-warn); }
.sev-badge.sev-hint { background: var(--sa-subtle); color: var(--sa-text-tertiary); }
.fix-tx { flex: 1; font-size: 12px; line-height: 1.5; }
.fix-sub { font-size: 11px; color: var(--sa-text-tertiary); margin-top: 2px; }
.lock-actions { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
.lock-all-btn { border: 1px solid var(--sa-accent); background: var(--sa-accent-subtle); color: var(--sa-accent); border-radius: 9px; font-size: 12px; font-weight: 700; padding: 7px 10px; cursor: pointer; }
.lock-all-btn:disabled { opacity: .45; cursor: default; }
.lock-row { display: flex; align-items: center; justify-content: space-between; font-size: 12px; font-weight: 600; padding: 5px 0; border-top: 1px dashed var(--sa-border-subtle); }
.unlock-btn { border: 1px solid var(--sa-border); background: none; color: var(--sa-text-secondary); border-radius: 7px; font-size: 10.5px; padding: 2px 9px; cursor: pointer; }
.unlock-btn:hover { color: var(--dh-bad); border-color: var(--dh-bad); }

@media (max-width: 960px) {
  .mv-container { padding: 12px 16px 80px; }
  .board { grid-template-columns: 1fr; }
  .rail { position: static; }
  .field-grid { grid-template-columns: repeat(2, 1fr); }
  .ident { flex-wrap: wrap; }
  .ident-stats { width: 100%; justify-content: flex-start; }
  .add-performer { flex-wrap: wrap; }
}
@media (max-width: 768px) {
  .mv-container { padding: 12px 14px 110px; }
  .ws-tab-row { margin-bottom: 10px; }
  .ident { padding: 11px 14px; gap: 8px 10px; }
  .ident-avatar { width: 42px; height: 42px; border-radius: 12px; font-size: 15px; }
  .ident-main h1 { font-size: 17px; }
  .ident-stats { gap: 8px 14px; }
  .stat { min-width: 32px; }
  .stat b { font-size: 15px; }
  .stat.score b { font-size: 18px; }
  .board { gap: 9px; }
  .node-head { padding: 9px 12px; }
  .node-body { padding: 10px 12px 12px; }
  .field-grid { grid-template-columns: 1fr 1fr; }
  .rail-card { padding: 11px 12px; }
  .ident-meta { gap: 4px; }
  .meta-pill { font-size: 10.5px; padding: 2px 8px; }
}
</style>

<style scoped src="./db-workspace.css"></style>
