<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SaSelect from '@/components/SaSelect.vue'
import SaHeader from '@/components/SaHeader.vue'
import DbTabs from '@/components/db/DbTabs.vue'
import SaOverflowTabs from '@/components/SaOverflowTabs.vue'
import SaDatePicker from '@/components/SaDatePicker.vue'
import RemoteSongSelect from '@/components/RemoteSongSelect.vue'
import { albumWorkspaceApi, type AlbumWorkspaceResponse } from '@/api/dbViews'
import type { CoverSearchItem } from '@/types/models'
import { albumsApi } from '@/api/albums'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'
import { companiesApi } from '@/api/companies'
import { entityLocksApi, type EntityLockMap } from '@/api/entityLocks'
import { formatDuration } from '@/utils/format'

const route = useRoute()
const router = useRouter()

// 内嵌进设置页·资料库时为 true：uid 由 props 传入，且去掉整屏外壳（站点头 / 页签行）
const props = defineProps<{ uid?: string; embedded?: boolean }>()

const uid = computed(() => props.uid || String(route.params.uid || ''))
const data = ref<AlbumWorkspaceResponse | null>(null)
const locks = ref<EntityLockMap>({})
const loading = ref(false)
const loadError = ref('')
const savingField = ref('')
const saveError = ref('')
const confirmTrackId = ref<number | null>(null)

async function load() {
  if (!uid.value) return
  loading.value = true
  loadError.value = ''
  try {
    data.value = await albumWorkspaceApi.getAlbumWorkspace(uid.value)
    if (data.value) {
      locks.value = await entityLocksApi.get('albums', data.value.album.id)
      seedSelects()
      seedLabels()
      // 封面搜索默认填入当前专辑名，免手打
      coverQuery.value = data.value.album.name || ''
      void searchSubject('')
      void searchLabel('')
    }
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载工作台失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(uid, load)

interface FieldDef {
  key: string
  label: string
  kind: 'date' | 'text' | 'select'
  options?: string[]
  placeholder?: string
}

const ALBUM_TYPE_LABELS: Record<string, string> = {
  Single: '单曲',
  MiniAlbum: '迷你专辑',
  FullAlbum: '正规专辑',
  Repackage: '改版',
  OST: 'OST',
  Compilation: '合辑',
  Other: '其他',
}

const FIELDS: FieldDef[] = [
  { key: 'release_date', label: '发行日期', kind: 'date' },
  {
    key: 'album_type',
    label: '专辑类型',
    kind: 'select',
    options: ['Single', 'MiniAlbum', 'FullAlbum', 'Repackage', 'OST', 'Compilation', 'Other'],
  },
  { key: 'korean_name', label: '韩文名', kind: 'text', placeholder: '点击填写' },
  { key: 'english_name', label: '英文名', kind: 'text', placeholder: '点击填写' },
  { key: 'chinese_name', label: '中文名', kind: 'text', placeholder: '点击填写' },
]

function fieldValue(key: string): string {
  const a = data.value?.album
  if (!a) return ''
  return (a as unknown as Record<string, string | null>)[key] ?? ''
}

async function saveField(key: string, value: string | null) {
  const a = data.value?.album
  if (!a) return
  saveError.value = ''
  savingField.value = key
  try {
    await albumsApi.update(a.id, { [key]: value === '' ? null : value })
    locks.value = await entityLocksApi.put('albums', a.id, { ...locks.value, [key]: true })
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
  const a = data.value?.album
  if (!a) return
  const next = { ...locks.value }
  delete next[key]
  locks.value = await entityLocksApi.put('albums', a.id, next)
}

const filledFieldKeys = computed(() =>
  FIELDS.map((f) => f.key)
    .concat('description')
    .filter((k) => fieldValue(k) !== ''),
)

async function lockAllFilled() {
  const a = data.value?.album
  if (!a) return
  saveError.value = ''
  try {
    const next: EntityLockMap = { ...locks.value }
    for (const k of filledFieldKeys.value) next[k] = true
    locks.value = await entityLocksApi.put('albums', a.id, next)
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '锁定失败'
  }
}

async function unlockAll() {
  const a = data.value?.album
  if (!a) return
  locks.value = await entityLocksApi.put('albums', a.id, {})
}

async function removeTrack(trackId: number) {
  if (confirmTrackId.value !== trackId) {
    confirmTrackId.value = trackId
    return
  }
  confirmTrackId.value = null
  saveError.value = ''
  try {
    await albumsApi.removeTrack(trackId)
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '移除曲目失败'
  }
}

function subjectWorkspaceUrl(subject: { type: string; uid: string }) {
  return subject.type === 'artist' ? `/db/artists/${subject.uid}` : `/db/groups/${subject.uid}`
}

const issues = computed(() => data.value?.issues ?? [])
const lockedFields = computed(() => Object.keys(locks.value).filter((k) => locks.value[k]))
const issueTotal = computed(() => {
  const c = data.value?.issue_counts
  return c ? (c.error || 0) + (c.warning || 0) : 0
})
const WS_TABS = [
  { key: 'base', label: '基本信息' },
  { key: 'release', label: '发行归属' },
  { key: 'tracks', label: '曲目' },
  { key: 'cover', label: '封面' },
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

function coverUrl() {
  return data.value?.album.cover_path ? albumsApi.coverUrl(data.value.album.id) : ''
}

// ===== 封面网络搜索 =====
const coverQuery = ref('')
const coverSource = ref<'netease' | 'itunes'>('netease')
const coverResults = ref<CoverSearchItem[]>([])
const coverSearching = ref(false)
const applyingCoverUrl = ref('')
const coverError = ref('')

async function searchCovers() {
  const q = coverQuery.value.trim()
  if (!q) return
  coverSearching.value = true
  coverError.value = ''
  try {
    coverResults.value = await albumsApi.coverSearch(q, coverSource.value)
  } catch (e) {
    coverError.value = e instanceof Error ? e.message : '封面搜索失败'
  } finally {
    coverSearching.value = false
  }
}

async function applyCover(coverUrl: string) {
  const a = data.value?.album
  if (!a) return
  applyingCoverUrl.value = coverUrl
  coverError.value = ''
  try {
    await albumsApi.coverApply(a.id, coverUrl)
    coverResults.value = []
    coverQuery.value = ''
    await load()
  } catch (e) {
    coverError.value = e instanceof Error ? e.message : '应用封面失败'
  } finally {
    applyingCoverUrl.value = ''
  }
}

// ===== 发行主体 / 厂牌编辑 =====
interface SelectOption {
  value: string
  label: string
}

const subjectOptions = ref<SelectOption[]>([])
const subjectLoading = ref(false)
const subjectValue = computed(() => {
  const s = data.value?.subject
  return s ? `${s.type}:${s.id}` : null
})

function seedSelects() {
  const s = data.value?.subject
  subjectOptions.value = s
    ? [{ value: `${s.type}:${s.id}`, label: `${s.type === 'artist' ? 'solo' : '组合'} · ${s.name}` }]
    : []
}

function ensureCurrentSubject() {
  const s = data.value?.subject
  const key = s ? `${s.type}:${s.id}` : null
  if (s && key && !subjectOptions.value.some((o) => o.value === key)) {
    subjectOptions.value = [
      { value: key, label: `${s.type === 'artist' ? 'solo' : '组合'} · ${s.name}` },
      ...subjectOptions.value,
    ]
  }
}

async function searchSubject(q: string) {
  subjectLoading.value = true
  try {
    const term = q.trim()
    const [artists, groups] = term
      ? await Promise.all([artistsApi.fuzzy(term), groupsApi.fuzzy(term)])
      : await Promise.all([artistsApi.brief(), groupsApi.brief()])
    subjectOptions.value = [
      ...groups.map((g) => ({ value: `group:${g.id}`, label: `组合 · ${g.chinese_name || g.name}` })),
      ...artists.map((a) => ({ value: `artist:${a.id}`, label: `solo · ${a.chinese_name || a.stage_name || a.name}` })),
    ]
    ensureCurrentSubject()
  } catch {
    subjectOptions.value = []
    ensureCurrentSubject()
  } finally {
    subjectLoading.value = false
  }
}

async function onSubjectChange(value: string | null) {
  const a = data.value?.album
  if (!a) return
  saveError.value = ''
  try {
    if (value) {
      const idx = value.indexOf(':')
      await albumsApi.update(a.id, {
        release_artist_type: value.slice(0, idx),
        release_artist_id: Number(value.slice(idx + 1)),
      })
    } else {
      await albumsApi.update(a.id, { release_artist_type: null, release_artist_id: null })
    }
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '保存发行主体失败'
  }
}

const labelOptions = ref<SelectOption[]>([])
const labelLoading = ref(false)
const labelValue = computed(() => (data.value?.label ? String(data.value.label.id) : null))

function seedLabels() {
  const l = data.value?.label
  labelOptions.value = l ? [{ value: String(l.id), label: l.name }] : []
}

async function searchLabel(q: string) {
  labelLoading.value = true
  try {
    const term = q.trim()
    const list = await companiesApi.brief(term || undefined)
    labelOptions.value = list.map((c) => ({ value: String(c.id), label: c.name }))
    const l = data.value?.label
    if (l && !labelOptions.value.some((o) => o.value === String(l.id))) {
      labelOptions.value = [{ value: String(l.id), label: l.name }, ...labelOptions.value]
    }
  } catch {
    labelOptions.value = []
  } finally {
    labelLoading.value = false
  }
}

async function onLabelChange(value: string | null) {
  const a = data.value?.album
  if (!a) return
  saveError.value = ''
  try {
    await albumsApi.update(a.id, { label_id: value ? Number(value) : null })
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '保存厂牌失败'
  }
}

// ===== 添加曲目 =====
const newTrackSongId = ref<number | null>(null)
const newTrackNo = ref<number | null>(null)
const addingTrack = ref(false)

const nextTrackNo = computed(() => {
  const t = data.value?.tracks ?? []
  return t.length ? Math.max(...t.map((x) => x.track_number)) + 1 : 1
})

async function addTrack() {
  const a = data.value?.album
  if (!a || !newTrackSongId.value) return
  addingTrack.value = true
  saveError.value = ''
  try {
    await albumsApi.addTrack({
      album_id: a.id,
      song_id: newTrackSongId.value,
      disc_number: 1,
      track_number: newTrackNo.value ?? nextTrackNo.value,
    })
    newTrackSongId.value = null
    newTrackNo.value = null
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '添加曲目失败'
  } finally {
    addingTrack.value = false
  }
}
</script>

<template>
  <div class="mv-page" :class="{ 'mv-page--embedded': embedded }">
    <SaHeader v-if="!embedded" />
    <div class="mv-container">
      <div v-if="!embedded" class="ws-tab-row">
        <DbTabs />
        <button class="back-btn" @click="router.push('/db/albums')">← 返回列表</button>
      </div>

      <div v-if="loadError" class="load-error">{{ loadError }}</div>
      <div v-else-if="loading && !data" class="list-loading">加载工作台…</div>

      <template v-else-if="data">
        <!-- ═══ 身份节点条 ═══ -->
        <section class="ident glass">
          <div class="ident-avatar">
            <img v-if="data.album.cover_path" :src="coverUrl()" alt="" />
            <template v-else>{{ data.album.name.slice(0, 2).toUpperCase() }}</template>
          </div>
          <div class="ident-main">
            <h1>
              {{ data.album.name }}
              <span v-if="data.album.korean_name" class="h-sub">{{ data.album.korean_name }}</span>
            </h1>
            <div class="ident-meta">
              <span class="meta-pill" :class="{ warn: !data.album.release_date }">
                {{ data.album.release_date ? `${data.album.release_date} 发行` : '缺发行日期' }}
              </span>
              <span class="meta-pill" :class="{ warn: !data.album.album_type }">
                {{ data.album.album_type ? (ALBUM_TYPE_LABELS[data.album.album_type] || data.album.album_type) : '缺类型' }}
              </span>
              <span v-if="data.label" class="meta-pill">厂牌 · {{ data.label.name }}</span>
              <router-link v-if="data.subject" :to="subjectWorkspaceUrl(data.subject)" class="meta-pill meta-link">
                {{ data.subject.type === 'artist' ? 'solo' : '组合' }} · {{ data.subject.name }} →
              </router-link>
              <span v-else class="meta-pill warn">未挂发行主体</span>
            </div>
          </div>
          <div class="ident-stats">
            <div class="stat"><b>{{ data.tracks.length }}</b><span>曲目</span></div>
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
                <b>专辑档案</b>
                <span class="node-note">点击即改 · 保存后自动 🔒，AI 补全不再覆盖</span>
              </header>
              <div class="node-body">
                <div v-if="saveError" class="form-error">{{ saveError }}</div>
                <div class="field-grid">
                  <label v-for="f in FIELDS" :key="f.key" class="field-card" :class="{ locked: locks[f.key], 'field-card--date': f.kind === 'date' }">
                    <span class="f-label">{{ f.label }}<i v-if="locks[f.key]" class="lock-mark">🔒</i></span>
                    <select v-if="f.kind === 'select'" class="f-input" :value="fieldValue(f.key)" :disabled="savingField === f.key" @change="onFieldChange(f, $event)">
                      <option value="">未填写</option>
                      <option v-for="opt in f.options" :key="opt" :value="opt">{{ ALBUM_TYPE_LABELS[opt] || opt }}</option>
                    </select>
                    <SaDatePicker
                      v-else-if="f.kind === 'date'"
                      variant="bordered"
                      :model-value="fieldValue(f.key)"
                      :disabled="savingField === f.key"
                      @update:model-value="(v: string | null) => saveField(f.key, v)"
                    />
                    <input v-else class="f-input" type="text" :value="fieldValue(f.key)" :placeholder="f.placeholder || '点击填写'" :disabled="savingField === f.key" @change="onFieldChange(f, $event)" />
                  </label>
                  <label class="field-card wide" :class="{ locked: locks['description'] }">
                    <span class="f-label">描述 {{ locks['description'] ? '🔒' : '' }}</span>
                    <textarea class="f-input" rows="2" :value="data.album.description ?? ''" placeholder="专辑介绍" :disabled="savingField === 'description'" @change="saveField('description', ($event.target as HTMLTextAreaElement).value || null)" />
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

            <template v-else-if="wsTab === 'release'">
<!-- ② 发行与归属 -->
            <section class="node node-accent">
              <header class="node-head">
                <span class="node-dot dot-accent" />
                <b>发行与归属</b>
                <span class="node-note">发行主体可换挂组合 / solo 艺人 · 厂牌可搜索选择</span>
              </header>
              <div class="node-body subject-grid">
                <label class="subject-field">
                  <span class="f-label">发行主体</span>
                  <SaSelect
                    :model-value="subjectValue"
                    :options="subjectOptions"
                    :loading="subjectLoading"
                    filterable
                    remote
                    placeholder="搜索组合或艺人"
                    @search="searchSubject"
                    @update:model-value="onSubjectChange"
                  />
                </label>
                <label class="subject-field">
                  <span class="f-label">厂牌</span>
                  <SaSelect
                    :model-value="labelValue"
                    :options="labelOptions"
                    :loading="labelLoading"
                    filterable
                    remote
                    placeholder="搜索公司"
                    @search="searchLabel"
                    @update:model-value="onLabelChange"
                  />
                </label>
              </div>
            </section>

                        </template>

            <template v-else-if="wsTab === 'tracks'">
<!-- ③ 曲目清单 -->
            <section class="node node-accent">
              <header class="node-head">
                <span class="node-dot dot-accent" />
                <b>曲目清单</b>
                <span class="node-note">{{ data.tracks.length }} 首 · 歌曲名点击进入歌曲详情 · × 移除曲目关联</span>
              </header>
              <div class="node-body">
                <div v-if="!data.tracks.length" class="side-empty">
                  还没有曲目——下方可搜索歌曲挂入，或在组合工作台的专辑区生长曲目。
                </div>
                <div v-else class="track-list">
                  <div
                    v-for="t in data.tracks"
                    :key="t.track_id"
                    class="track-row"
                    :class="{ dangling: t.is_dangling }"
                  >
                    <span class="tk-no">{{ t.disc_number > 1 ? `${t.disc_number}-${String(t.track_number).padStart(2, '0')}` : String(t.track_number).padStart(2, '0') }}</span>
                    <router-link v-if="t.song_uid" :to="`/songs/${t.song_uid}`" class="tk-name">
                      {{ t.song_chinese_name || t.song_name || '未知歌曲' }}
                    </router-link>
                    <span v-else class="tk-name missing-text">{{ t.song_name || '悬空曲目' }}</span>
                    <span class="tk-dur">{{ t.song_duration != null ? formatDuration(t.song_duration) : '--' }}</span>
                    <button
                      class="tk-del"
                      type="button"
                      :class="{ confirm: confirmTrackId === t.track_id }"
                      @click="removeTrack(t.track_id)"
                    >
                      {{ confirmTrackId === t.track_id ? '确认移除' : '×' }}
                    </button>
                  </div>
                </div>

                <div class="add-track">
                  <RemoteSongSelect v-model="newTrackSongId" placeholder="搜索歌曲挂入本专辑" />
                  <input
                    v-model.number="newTrackNo"
                    class="add-track-no"
                    type="number"
                    min="1"
                    :placeholder="`第 ${nextTrackNo} 首`"
                  />
                  <button
                    class="add-track-btn"
                    type="button"
                    :disabled="!newTrackSongId || addingTrack"
                    @click="addTrack"
                  >
                    {{ addingTrack ? '添加中…' : '添加曲目' }}
                  </button>
                </div>
              </div>
            </section>

                        </template>

            <template v-else-if="wsTab === 'cover'">
            <section class="node">
              <header class="node-head">
                <b>封面</b>
                <span class="node-note">网络搜索并一键应用</span>
              </header>
              <div class="node-body">
<template v-if="data.album.cover_path">
                <img class="cover-preview" :src="coverUrl()" alt="" />
                <button class="cover-remove" type="button" @click="saveField('cover_path', null)">移除封面</button>
              </template>
              <div v-else class="side-empty">还没有封面——可网络搜索并一键应用。</div>

              <div class="cover-search">
                <div v-if="coverError" class="form-error">{{ coverError }}</div>
                <input
                  v-model="coverQuery"
                  type="text"
                  class="cover-query"
                  :placeholder="data.album.name ? `搜索「${data.album.name}」封面` : '搜索专辑名'"
                  @keyup.enter="searchCovers"
                />
                <div class="cover-search-row">
                  <select v-model="coverSource" class="cover-source">
                    <option value="netease">网易云</option>
                    <option value="itunes">iTunes</option>
                  </select>
                  <button class="cover-search-btn" type="button" :disabled="!coverQuery.trim() || coverSearching" @click="searchCovers">
                    {{ coverSearching ? '搜索中…' : '搜索封面' }}
                  </button>
                </div>
                <div v-if="coverResults.length" class="cover-results">
                  <div v-for="item in coverResults" :key="`${item.source}-${item.id}`" class="cover-item">
                    <img :src="item.cover_url" alt="" loading="lazy" />
                    <div class="cover-item-tx">
                      <b>{{ item.name }}</b>
                      <small>{{ [item.artist, item.year, item.source].filter(Boolean).join(' · ') }}</small>
                    </div>
                    <button
                      class="cover-apply"
                      type="button"
                      :disabled="applyingCoverUrl === item.cover_url"
                      @click="applyCover(item.cover_url)"
                    >
                      {{ applyingCoverUrl === item.cover_url ? '应用中…' : '应用' }}
                    </button>
                  </div>
                </div>
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
.ident-avatar { width: 52px; height: 52px; border-radius: 16px; overflow: hidden; flex-shrink: 0; background: var(--sa-accent); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 18px; font-weight: 800; }
.ident-avatar img { width: 100%; height: 100%; object-fit: cover; }
.ident-main { flex: 1; min-width: 0; }
.ident-main h1 { margin: 0; font-size: 20px; overflow-wrap: anywhere; }
.h-sub { font-size: 12px; color: var(--sa-text-tertiary); font-weight: 400; margin-left: 6px; }
.ident-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.meta-pill { font-size: 11px; padding: 2px 9px; border-radius: 6px; background: var(--sa-subtle); color: var(--sa-text-secondary); }
.meta-pill.warn { background: rgba(217,119,6,.12); color: var(--dh-warn); }
.meta-link { color: var(--sa-accent); text-decoration: none; }
.meta-link:hover { background: var(--sa-accent-subtle); }
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
.subject-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.subject-field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.add-track { display: flex; gap: 8px; align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--sa-border-subtle); }
.add-track :deep(.n-select) { flex: 1; min-width: 0; }
.add-track-no { flex-shrink: 0; width: 84px; height: 34px; padding: 0 10px; border: 1px solid var(--sa-border-subtle); border-radius: 8px; background: var(--sa-bg); color: var(--sa-text-primary); font-size: 12.5px; font-family: inherit; outline: none; }
.add-track-btn { flex-shrink: 0; height: 34px; padding: 0 14px; border: 1px solid var(--sa-accent); background: var(--sa-accent-subtle); color: var(--sa-accent); border-radius: 8px; font-size: 12px; font-weight: 700; cursor: pointer; font-family: inherit; }
.add-track-btn:disabled { opacity: .45; cursor: default; }
@media (max-width: 720px) {
  .subject-grid { grid-template-columns: 1fr; }
  .add-track { flex-wrap: wrap; }
}
.track-row { display: flex; align-items: center; gap: 10px; padding: 6px 8px; border-radius: 8px; }
.track-row:hover { background: var(--sa-subtle); }
.track-row.dangling { opacity: .55; }
.tk-no { flex-shrink: 0; width: 30px; font-size: 11px; font-weight: 800; color: var(--sa-text-tertiary); font-variant-numeric: tabular-nums; }
.tk-name { flex: 1; min-width: 0; font-size: 12.5px; font-weight: 600; color: var(--sa-text-primary); text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
a.tk-name:hover { color: var(--sa-accent); }
.tk-dur { flex-shrink: 0; font-size: 11px; color: var(--sa-text-tertiary); font-variant-numeric: tabular-nums; }
.tk-del { flex-shrink: 0; width: 22px; height: 22px; border: none; border-radius: 6px; background: none; color: var(--sa-text-tertiary); font-size: 13px; line-height: 1; cursor: pointer; }
.tk-del:hover { color: var(--dh-bad); background: rgba(208,48,80,.1); }
.tk-del.confirm { width: auto; padding: 0 8px; font-size: 10.5px; font-weight: 700; color: #fff; background: var(--dh-bad); }
.missing-text { font-size: 12px; color: var(--sa-text-tertiary); font-style: italic; }

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
.cover-preview { width: 100%; border-radius: 10px; border: 1px solid var(--sa-border-subtle); display: block; }
.cover-remove { width: 100%; margin-top: 7px; border: 1px solid var(--sa-border); background: none; color: var(--sa-text-secondary); border-radius: 8px; font-size: 11.5px; padding: 5px 0; cursor: pointer; }
.cover-remove:hover { color: var(--dh-bad); border-color: var(--dh-bad); }
.cover-search { margin-top: 10px; display: flex; flex-direction: column; gap: 7px; }
.cover-query { width: 100%; box-sizing: border-box; height: 32px; padding: 0 10px; border: 1px solid var(--sa-border-subtle); border-radius: 8px; background: var(--sa-bg); color: var(--sa-text-primary); font-size: 12px; font-family: inherit; outline: none; }
.cover-query:focus { border-color: var(--sa-accent); }
.cover-search-row { display: flex; gap: 6px; }
.cover-source { flex-shrink: 0; height: 30px; border: 1px solid var(--sa-border-subtle); border-radius: 8px; background: var(--sa-bg); color: var(--sa-text-secondary); font-size: 11.5px; font-family: inherit; cursor: pointer; }
.cover-search-btn { flex: 1; height: 30px; border: 1px solid var(--sa-accent); background: var(--sa-accent-subtle); color: var(--sa-accent); border-radius: 8px; font-size: 11.5px; font-weight: 700; cursor: pointer; font-family: inherit; }
.cover-search-btn:disabled { opacity: .45; cursor: default; }
.cover-results { display: flex; flex-direction: column; gap: 6px; max-height: 320px; overflow-y: auto; }
.cover-item { display: flex; align-items: center; gap: 8px; padding: 6px; border: 1px solid var(--sa-border-subtle); border-radius: 9px; }
.cover-item img { width: 44px; height: 44px; border-radius: 7px; object-fit: cover; flex-shrink: 0; }
.cover-item-tx { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.cover-item-tx b { font-size: 11.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cover-item-tx small { font-size: 10px; color: var(--sa-text-tertiary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cover-apply { flex-shrink: 0; border: 1px solid var(--sa-accent); background: none; color: var(--sa-accent); border-radius: 7px; font-size: 10.5px; font-weight: 700; padding: 3px 9px; cursor: pointer; }
.cover-apply:disabled { opacity: .45; cursor: default; }
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
