<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SaHeader from '@/components/SaHeader.vue'
import DbTabs from '@/components/db/DbTabs.vue'
import SaOverflowTabs from '@/components/SaOverflowTabs.vue'
import { artistWorkspaceApi, dbFieldSuggestApi, artistGrowthApi, type ArtistSourcePreview, type ArtistSourceDiffItem, type AlbumProposal, type ArtistWorkspaceResponse } from '@/api/dbViews'
import { artistsApi } from '@/api/artists'
import { albumsApi } from '@/api/albums'
import { membershipsApi } from '@/api/memberships'
import { groupsApi } from '@/api/groups'
import { companyRelationsApi, type CompanyRelation, type CompanyRelationPatch } from '@/api/companyRelations'
import { entityLocksApi, type EntityLockMap } from '@/api/entityLocks'
import CompanyRelationsEditor from '@/components/db/CompanyRelationsEditor.vue'
import EntityMediaCard from '@/components/db/EntityMediaCard.vue'
import SocialMediaEditor from '@/components/db/SocialMediaEditor.vue'
import SettingsPhotoFolders from '@/views/settings/SettingsPhotoFolders.vue'
import SaSelect from '@/components/SaSelect.vue'
import type { SaOption } from '@/components/SaSelect.vue'
import { formatDuration } from '@/utils/format'
import type { AlbumTrackDetail } from '@/types/models'
import { formatPositions, parsePositionsInput } from '@/utils/positions'

const route = useRoute()
const router = useRouter()

// 内嵌进设置页·资料库时为 true：uid 由 props 传入，且去掉整屏外壳（站点头 / 页签行）
const props = defineProps<{ uid?: string; embedded?: boolean }>()

const uid = computed(() => props.uid || String(route.params.uid || ''))
const data = ref<ArtistWorkspaceResponse | null>(null)
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
    data.value = await artistWorkspaceApi.getArtistWorkspace(uid.value)
    if (data.value) {
      locks.value = await entityLocksApi.get('artists', data.value.artist.id)
    }
    descDraft.value = null
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载工作台失败'
  } finally {
    loading.value = false
  }
}

// ===== solo 专辑生长 =====
const ALBUM_ACTION_LABELS: Record<string, string> = {
  create: '新建',
  update: '补全',
  exists: '已在库',
  fill_tracks: '补曲目',
}
const ALBUM_TYPE_LABELS_GROWTH: Record<string, string> = {
  single: '单曲',
  ep: '迷你专辑',
  album: '正规专辑',
  compilation: '合辑',
}
const albumSuggestions = ref<AlbumProposal[]>([])
const albumNote = ref('')
const albumChecked = ref<Record<number, boolean>>({})
const albumSearching = ref(false)
const albumApplying = ref(false)
const albumError = ref('')
const albumResult = ref('')

async function searchAlbums() {
  albumSearching.value = true
  albumError.value = ''
  albumResult.value = ''
  try {
    const r = await artistGrowthApi.suggestAlbums(uid.value)
    albumSuggestions.value = r.albums || []
    albumNote.value = r.note || ''
    const checked: Record<number, boolean> = {}
    albumSuggestions.value.forEach((p, i) => {
      checked[i] = p.action !== 'exists'
    })
    albumChecked.value = checked
  } catch (e) {
    albumError.value = e instanceof Error ? e.message : '专辑检索失败'
  } finally {
    albumSearching.value = false
  }
}

async function applyAlbums() {
  const picked = albumSuggestions.value.filter((_, i) => albumChecked.value[i])
  if (!picked.length) return
  albumApplying.value = true
  albumError.value = ''
  try {
    const r = await artistGrowthApi.applyAlbums(uid.value, picked)
    albumResult.value =
      `新建专辑 ${r.albums_created} 张 · 补全 ${r.albums_updated} 张 · 跳过 ${r.albums_skipped} 张` +
      (r.songs_created || r.tracks_created
        ? ` · 新建歌曲 ${r.songs_created} 首 · 写入曲目 ${r.tracks_created} 首`
        : '') +
      (r.tracklist_failed ? ` · 曲目抓取失败 ${r.tracklist_failed} 张` : '')
    albumSuggestions.value = []
    growthStep.value = 3
    await load()
  } catch (e) {
    albumError.value = e instanceof Error ? e.message : '应用失败'
  } finally {
    albumApplying.value = false
  }
}

// ===== 生长：三步流（① 身份与公司 ② 专辑 ③ 歌曲/曲目），与组合生长同构 =====
const APPLICABLE_LABELS: Record<string, string> = {
  description: '描述',
  tagline: '一句话简介',
  birth_date: '出生日期',
  birth_place: '出生地',
  occupation: '职业',
  korean_name: '韩文名',
  english_name: '英文名',
  chinese_name: '中文名',
  debut_date: '出道日期',
}

/** 艺人头部副标题：中文名 → 韩文名 → 英文名，取第一个与主标题不同的字段，无则空 */
function subtitles() {
  const a = data.value?.artist
  if (!a) return ''
  const main = (a.name || '').trim()
  for (const c of [a.chinese_name, a.korean_name, a.english_name]) {
    const t = (c || '').trim()
    if (t && t !== main) return t
  }
  return ''
}
const GROWTH_STEPS = [
  { id: 1, title: '① 身份与公司' },
  { id: 2, title: '② 专辑' },
  { id: 3, title: '③ 歌曲 / 曲目' },
]
const growthStep = ref(1)
const growthOpen = ref(false)
const growthUrls = ref('')
const growthPreview = ref<ArtistSourcePreview | null>(null)
const growthChecked = ref<Record<string, boolean>>({})
const growthPreviewing = ref(false)
const growthApplying = ref(false)
const growthError = ref('')
const growthDone = ref<string[]>([])

function toggleGrowth() {
  growthOpen.value = !growthOpen.value
  if (!growthOpen.value) {
    growthPreview.value = null
    growthError.value = ''
    growthDone.value = []
  }
}

/** 页签行右端「生长」按钮：展开/收起整个生长模块（v3.5.6 取代原「生长」页签）。 */
function toggleGrowthPanel() {
  growthPanelOpen.value = !growthPanelOpen.value
  growthOpen.value = growthPanelOpen.value
  if (!growthPanelOpen.value) {
    growthPreview.value = null
    growthError.value = ''
    growthDone.value = []
  }
}

async function runGrowth(withUrls: boolean) {
  growthPreviewing.value = true
  growthError.value = ''
  growthDone.value = []
  try {
    const urls = withUrls
      ? growthUrls.value.split(/\s+/).map((u) => u.trim()).filter(Boolean)
      : []
    const r = await artistGrowthApi.preview(uid.value, urls)
    growthPreview.value = r
    const checked: Record<string, boolean> = {}
    for (const item of r.items) checked[item.field] = !item.same
    growthChecked.value = checked
  } catch (e) {
    growthError.value = e instanceof Error ? e.message : '生长预览失败'
  } finally {
    growthPreviewing.value = false
  }
}

function growthItemTitle(item: ArtistSourceDiffItem) {
  const cur = item.current ? `当前：${item.current.slice(0, 80)}` : '当前：未填写'
  return `${cur}\n提案：${item.proposed.slice(0, 200)}`
}

async function applyGrowth() {
  const items = (growthPreview.value?.items ?? []).filter((i) => growthChecked.value[i.field])
  if (!items.length) return
  growthApplying.value = true
  growthError.value = ''
  const fields: Record<string, string> = {}
  const sourceUrls = new Set<string>()
  for (const item of items) {
    fields[item.field] = item.proposed
    for (const u of item.source_urls || []) sourceUrls.add(u)
  }
  try {
    const r = await artistGrowthApi.apply(uid.value, fields, [...sourceUrls])
    growthDone.value = r.applied.fields
    if (r.applied.fields_skipped_locked.length) {
      growthError.value = `已跳过锁定字段：${r.applied.fields_skipped_locked.join('、')}`
    }
    growthPreview.value = null
    await load()
  } catch (e) {
    growthError.value = e instanceof Error ? e.message : '应用失败'
  } finally {
    growthApplying.value = false
  }
}

// ===== 单独搜索简介 =====
const descDraft = ref<string | null>(null)
const suggestBusy = ref(false)
const suggestSource = ref('')
const suggestNone = ref(false)

async function suggestDescription() {
  suggestBusy.value = true
  suggestSource.value = ''
  suggestNone.value = false
  try {
    const r = await dbFieldSuggestApi.suggestArtistField(uid.value, 'description')
    if (r.value) {
      descDraft.value = r.value
      suggestSource.value = r.source_label || ''
    } else {
      suggestNone.value = true
    }
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '简介搜索失败'
  } finally {
    suggestBusy.value = false
  }
}

onMounted(load)
watch(uid, load)

interface FieldDef {
  key: string
  label: string
  kind: 'date' | 'text' | 'select' | 'textarea'
  options?: string[]
  placeholder?: string
}

const GENDER_LABELS: Record<string, string> = { Female: '女', Male: '男' }

const FIELDS: FieldDef[] = [
  { key: 'birth_date', label: '出生日期', kind: 'date' },
  { key: 'debut_date', label: '出道日期', kind: 'date' },
  {
    key: 'gender',
    label: '性别',
    kind: 'select',
    options: ['Female', 'Male'],
  },
  { key: 'birth_place', label: '出生地', kind: 'text', placeholder: '如：首尔' },
  { key: 'occupation', label: '职业', kind: 'text', placeholder: '如：歌手' },
  { key: 'korean_name', label: '韩文名', kind: 'text', placeholder: '点击填写' },
  { key: 'english_name', label: '英文名', kind: 'text', placeholder: '点击填写' },
  { key: 'chinese_name', label: '中文名', kind: 'text', placeholder: '点击填写' },
  { key: 'stage_name', label: '艺名', kind: 'text', placeholder: '如与本名相同可留空' },
  { key: 'tagline', label: '一句话简介', kind: 'text', placeholder: '≤40 字' },
]

function fieldValue(key: string): string {
  const a = data.value?.artist
  if (!a) return ''
  return (a as unknown as Record<string, string | null>)[key] ?? ''
}

async function saveField(key: string, value: string | null) {
  const a = data.value?.artist
  if (!a) return
  saveError.value = ''
  savingField.value = key
  try {
    await artistsApi.update(a.id, { [key]: value === '' ? null : value })
    locks.value = await entityLocksApi.put('artists', a.id, { ...locks.value, [key]: true })
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    savingField.value = ''
  }
}

async function saveSocialMedia(map: Record<string, string>) {
  const a = data.value?.artist
  if (!a) return
  saveError.value = ''
  savingField.value = 'social_media'
  try {
    await artistsApi.update(a.id, { social_media: Object.keys(map).length ? map : null })
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '保存社交媒体失败'
  } finally {
    savingField.value = ''
  }
}

async function toggleHideFromHome(ev: Event) {
  const a = data.value?.artist
  if (!a) return
  const checked = (ev.target as HTMLInputElement).checked
  saveError.value = ''
  savingField.value = 'hide_from_home'
  try {
    await artistsApi.update(a.id, { hide_from_home: checked })
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
  const a = data.value?.artist
  if (!a) return
  const next = { ...locks.value }
  delete next[key]
  locks.value = await entityLocksApi.put('artists', a.id, next)
}

const filledFieldKeys = computed(() =>
  FIELDS.map((f) => f.key)
    .concat('description')
    .filter((k) => fieldValue(k) !== ''),
)

async function lockAllFilled() {
  const a = data.value?.artist
  if (!a) return
  saveError.value = ''
  try {
    const next: EntityLockMap = { ...locks.value }
    for (const k of filledFieldKeys.value) next[k] = true
    locks.value = await entityLocksApi.put('artists', a.id, next)
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '锁定失败'
  }
}

async function unlockAll() {
  const a = data.value?.artist
  if (!a) return
  locks.value = await entityLocksApi.put('artists', a.id, {})
}

const issues = computed(() => data.value?.issues ?? [])
const sourceUrls = computed(() =>
  (data.value?.artist.external_links ?? [])
    .map((l) => (typeof l === 'string' ? l : (l.url as string) || ''))
    .filter(Boolean),
)
const lockedFields = computed(() => Object.keys(locks.value).filter((k) => locks.value[k]))
const issueTotal = computed(() => {
  const c = data.value?.issue_counts
  return c ? (c.error || 0) + (c.warning || 0) : 0
})
const WS_TABS = [
  { key: 'base', label: '基本信息' },
  { key: 'media', label: '媒体' },
  { key: 'groups', label: '所属组合' },
  { key: 'company', label: '所属公司' },
  { key: 'works', label: '作品' },
]
const wsTab = ref('base')
/**
 * 「生长」（补全身份 / 按艺人名检索建碟）入口。
 * v3.5.6 用户要求：艺人详情页**取消「生长」页签** → 改由页签行右端的按钮展开，
 * 能力一点没少（模块自身仍带「展开 / 已展开」折叠），只是不再占一个页签位置。
 */
const growthPanelOpen = ref(false)

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

const mediaStamp = ref(Date.now())

function avatarUrl() {
  if (!data.value?.artist.avatar_path) return ''
  return `/api/artists/${data.value.artist.id}/avatar?t=${mediaStamp.value}`
}

function onMediaUpdated() {
  mediaStamp.value = Date.now()
  void load()
}

// ===== 所属组合关系编辑（入组/离组/状态/担当/删除）=====
type Membership = ArtistWorkspaceResponse['memberships'][number]
const savingMembership = ref<number | null>(null)
const confirmRemoveMembership = ref<number | null>(null)
const membershipError = ref('')

async function saveMembership(m: Membership, patch: Partial<Membership>) {
  membershipError.value = ''
  savingMembership.value = m.membership_id
  try {
    await membershipsApi.update(m.membership_id, patch)
    await load()
  } catch (e) {
    membershipError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    savingMembership.value = null
  }
}

function onStatus(m: Membership, ev: Event) {
  void saveMembership(m, { status: (ev.target as HTMLSelectElement).value })
}
function onPositions(m: Membership, ev: Event) {
  void saveMembership(m, { positions: parsePositionsInput((ev.target as HTMLInputElement).value) })
}
async function removeMembership(m: Membership) {
  if (confirmRemoveMembership.value !== m.membership_id) {
    confirmRemoveMembership.value = m.membership_id
    return
  }
  confirmRemoveMembership.value = null
  membershipError.value = ''
  try {
    await membershipsApi.remove(m.membership_id)
    await load()
  } catch (e) {
    membershipError.value = e instanceof Error ? e.message : '删除关系失败'
  }
}

// ===== 专辑展开（曲目懒加载）=====
const expandedAlbums = ref<Record<number, boolean>>({})
const albumTracks = ref<Record<number, AlbumTrackDetail[]>>({})
const albumTracksLoading = ref<Record<number, boolean>>({})

async function toggleAlbum(al: { id: number }) {
  const next = !expandedAlbums.value[al.id]
  expandedAlbums.value = { ...expandedAlbums.value, [al.id]: next }
  if (!next || albumTracks.value[al.id]) return
  albumTracksLoading.value = { ...albumTracksLoading.value, [al.id]: true }
  try {
    const tracks = await albumsApi.tracks(al.id)
    albumTracks.value = { ...albumTracks.value, [al.id]: tracks }
  } catch {
    albumTracks.value = { ...albumTracks.value, [al.id]: [] }
  } finally {
    albumTracksLoading.value = { ...albumTracksLoading.value, [al.id]: false }
  }
}

// ===== 新增组合关系 =====
const pickedGroupId = ref<number | null>(null)
const newJoinDate = ref('')
const newPositions = ref('')
const addingMembership = ref(false)

/** 供 SaSelect 远程搜索 */
async function fetchGroupOptions(q: string): Promise<SaOption[]> {
  const term = q.trim()
  const groups = term ? await groupsApi.fuzzy(term) : await groupsApi.brief()
  return groups.map((g) => ({
    value: String(g.id),
    label: g.chinese_name ? `${g.name}（${g.chinese_name}）` : g.name,
  }))
}

async function addMembership() {
  const a = data.value?.artist
  if (!a || !pickedGroupId.value) return
  addingMembership.value = true
  membershipError.value = ''
  try {
    await membershipsApi.create({
      group_id: pickedGroupId.value,
      artist_id: a.id,
      status: 'Active',
      join_date: newJoinDate.value || null,
      positions: parsePositionsInput(newPositions.value),
    })
    pickedGroupId.value = null
    newJoinDate.value = ''
    newPositions.value = ''
    await load()
  } catch (e) {
    membershipError.value = e instanceof Error ? e.message : '加入组合失败'
  } finally {
    addingMembership.value = false
  }
}

// ===== 所属公司编辑 =====
const companyError = ref('')
const companyRelations = computed<CompanyRelation[]>(() => (data.value?.companies ?? []) as CompanyRelation[])

async function addCompany(companyId: number, role: string | null) {
  const a = data.value?.artist
  if (!a) return
  companyError.value = ''
  try {
    await companyRelationsApi.createForArtist({
      artist_id: a.id,
      company_id: companyId,
      role,
      status: 'Active',
    })
    await load()
  } catch (e) {
    companyError.value = e instanceof Error ? e.message : '补挂公司失败'
  }
}
async function updateCompany(relationId: number, patch: CompanyRelationPatch) {
  companyError.value = ''
  try {
    await companyRelationsApi.updateForArtist(relationId, patch)
    await load()
  } catch (e) {
    companyError.value = e instanceof Error ? e.message : '保存失败'
  }
}
async function removeCompany(relationId: number) {
  companyError.value = ''
  try {
    await companyRelationsApi.removeForArtist(relationId)
    await load()
  } catch (e) {
    companyError.value = e instanceof Error ? e.message : '删除失败'
  }
}

</script>

<template>
  <div class="mv-page" :class="{ 'mv-page--embedded': embedded }">
    <SaHeader v-if="!embedded" />
    <div class="mv-container">
      <div v-if="!embedded" class="ws-tab-row">
        <DbTabs />
        <button class="back-btn" @click="router.push('/db/artists')">← 返回列表</button>
      </div>

      <div v-if="loadError" class="load-error">{{ loadError }}</div>
      <div v-else-if="loading && !data" class="list-loading">加载工作台…</div>

      <template v-else-if="data">
        <!-- ═══ 身份节点条 ═══ -->
        <section class="ident glass">
          <div class="ident-avatar">
            <img v-if="data.artist.avatar_path" :src="avatarUrl()" alt="" />
            <template v-else>{{ data.artist.name.slice(0, 2).toUpperCase() }}</template>
          </div>
          <div class="ident-main">
            <h1>
              {{ data.artist.name }}
              <span v-if="subtitles()" class="h-sub">{{ subtitles() }}</span>
            </h1>
            <div class="ident-meta">
              <span class="meta-pill" :class="{ warn: !data.artist.birth_date }">
                {{ data.artist.birth_date ? `${data.artist.birth_date} 出生` : '缺出生日期' }}
              </span>
              <span class="meta-pill">{{ data.artist.gender ? (GENDER_LABELS[data.artist.gender] || data.artist.gender) : "未填写" }}</span>
              <span class="meta-pill" v-if="data.artist.occupation">{{ data.artist.occupation }}</span>
              <span class="meta-pill" v-if="data.memberships.length">{{ data.memberships.length }} 段组合关系</span>
              <span class="meta-pill" :class="data.artist.debut_date ? '' : 'warn'">
                {{ data.artist.debut_date ? `${data.artist.debut_date} 出道` : '缺出道日期' }}
              </span>
            </div>
          </div>
          <div class="ident-stats">
            <div class="stat"><b>{{ data.memberships.length }}</b><span>组合</span></div>
            <div class="stat"><b>{{ data.albums.length }}</b><span>专辑</span></div>
            <div class="stat"><b>{{ data.songs_count }}</b><span>歌曲</span></div>
            <div class="stat"><b>{{ data.videos.length }}</b><span>影像</span></div>
            <div class="stat score" :style="{ color: completenessColor(data.completeness) }">
              <b>{{ data.completeness }}</b><span>完整度</span>
            </div>
          </div>
        </section>

        <div class="board">
          <div class="board-main">
            <div class="ws-tabs">
              <SaOverflowTabs v-model="wsTab" :items="WS_TABS" aria-label="工作台板块" />
              <!-- 生长入口（v3.5.6：不再占页签，见 growthPanelOpen 的注释） -->
              <button
                class="grow-entry"
                type="button"
                :class="{ on: growthPanelOpen }"
                title="按艺人名聚合检索 Fandom / 维基 / TMDB 补全身份，并按艺人名检索建碟"
                @click="toggleGrowthPanel"
              >
                {{ growthPanelOpen ? '收起生长' : '生长' }}
              </button>
            </div>

            <template v-if="wsTab === 'base'">
<!-- ① 基本信息 -->
            <section class="node node-teal">
              <header class="node-head">
                <span class="node-dot dot-teal" />
                <b>艺人档案</b>
                <span class="node-note">点击即改 · 保存后自动 🔒，AI 补全不再覆盖</span>
              </header>
              <div class="node-body">
                <div v-if="saveError" class="form-error">{{ saveError }}</div>
                <div class="field-grid">
                  <label v-for="f in FIELDS" :key="f.key" class="field-card" :class="{ locked: locks[f.key], 'field-card--date': f.kind === 'date' }">
                    <span class="f-label">{{ f.label }}<i v-if="locks[f.key]" class="lock-mark">🔒</i></span>
                    <select v-if="f.kind === 'select'" class="f-input" :value="fieldValue(f.key)" :disabled="savingField === f.key" @change="onFieldChange(f, $event)">
                      <option value="">未填写</option>
                      <option v-for="opt in f.options" :key="opt" :value="opt">{{ GENDER_LABELS[opt] || opt }}</option>
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
                    <textarea class="f-input" rows="2" :value="descDraft ?? (data.artist.description ?? '')" placeholder="艺人完整介绍" :disabled="savingField === 'description'" @change="onFieldChange({ key: 'description', label: '描述', kind: 'textarea' }, $event)" />
                    <div class="desc-suggest">
                      <button
                        v-if="!descDraft && !(data.artist.description ?? '')"
                        type="button"
                        class="desc-suggest-btn"
                        :disabled="suggestBusy"
                        @click="suggestDescription"
                      >
                        {{ suggestBusy ? '搜索中…' : '🔍 单独搜索简介' }}
                      </button>
                      <span v-if="suggestSource" class="desc-suggest-src">来源：{{ suggestSource }} · 修改后点击保存</span>
                      <span v-else-if="suggestNone" class="desc-suggest-src">外部站点没有找到可用简介，可稍后重试或手动填写</span>
                    </div>
                  </label>
                </div>
              </div>
            </section>

            
            <section class="node checkup-node">
              <header class="node-head">
                <b>体检</b>
                <span class="node-note">问题 · 资料来源 · 字段锁</span>
              </header>
              <div class="node-body">
                <div class="ws-sum">
                  <span class="ws-sum__i">
                    <i class="ws-dot" :class="issueTotal ? (data.issue_counts.error ? 'ws-dot--bad' : 'ws-dot--warn') : 'ws-dot--ok'" />
                    {{ issueTotal }} 问题
                  </span>
                  <span class="ws-sum__i"><i class="ws-dot ws-dot--src" />来源 {{ sourceUrls.length }}</span>
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
                  <summary>资料来源 <span v-if="sourceUrls.length" class="cnt-badge">{{ sourceUrls.length }}</span></summary>
                  <div v-if="!sourceUrls.length" class="side-empty">暂无来源链接</div>
                  <a v-for="(u, i) in sourceUrls" :key="i" :href="u" target="_blank" rel="noreferrer" class="src-line">{{ u }}</a>
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

            <template v-else-if="wsTab === 'media'">
<!-- 媒体图片：头像 + 横幅海报 -->
            <section class="node node-teal">
              <header class="node-head">
                <span class="node-dot dot-teal" />
                <b>媒体图片</b>
                <span class="node-note">头像与首页主舞台横幅 · 支持本地上传与历史切换</span>
              </header>
              <div class="node-body">
                <EntityMediaCard
                  kind="artist"
                  :entity-id="data.artist.id"
                  :avatar-path="data.artist.avatar_path"
                  :banner-path="data.artist.banner_path"
                  :locks="locks"
                  :search-query="data.artist.name"
                  @updated="onMediaUpdated"
                />
              </div>
            </section>

            <!-- 社交媒体 -->
            <section class="node node-teal">
              <header class="node-head">
                <span class="node-dot dot-teal" />
                <b>社交媒体</b>
                <span class="node-note">仅展示有链接的平台 · AI 搜索需人工确认后写入</span>
              </header>
              <div class="node-body">
                <SocialMediaEditor
                  entity-type="artist"
                  :entity-id="data.artist.id"
                  :model-value="(data.artist.social_media as Record<string, string> | null) || null"
                  :disabled="savingField === 'social_media'"
                  @save="saveSocialMedia"
                />
              </div>
            </section>

            <!-- 首页主舞台 -->
            <section class="node node-teal">
              <header class="node-head">
                <span class="node-dot dot-teal" />
                <b>首页主舞台</b>
                <span class="node-note">控制杂志/影院主题轮动池</span>
              </header>
              <div class="node-body">
                <label class="home-toggle">
                  <input
                    type="checkbox"
                    :checked="!!data.artist.hide_from_home"
                    :disabled="savingField === 'hide_from_home'"
                    @change="toggleHideFromHome"
                  />
                  <span>不在首页轮动展示</span>
                </label>
              </div>
            </section>

            <section class="node node-teal">
              <header class="node-head">
                <span class="node-dot dot-teal" />
                <b>图片文件夹</b>
                <span class="node-note">官方 / 粉丝 / 照片墙 · 绑 MT Photos 相册/文件夹</span>
              </header>
              <div class="node-body">
                <SettingsPhotoFolders owner-type="artist" :owner-id="data.artist.id" />
              </div>
            </section>

                        </template>

            <template v-else-if="wsTab === 'groups'">
<!-- ② 所属组合 -->
            <section class="node node-accent" >
              <header class="node-head">
                <span class="node-dot dot-accent" />
                <b>所属组合</b>
                <span class="node-note">入组/离组/状态/担当点击即改 · 组合名点击进入组合工作台</span>
              </header>
              <div class="node-body">
                <div v-if="!data.memberships.length" class="side-empty">还没有组合关系——下方可搜索加入。</div>
                <div v-if="membershipError" class="form-error">{{ membershipError }}</div>
                <div class="mem-rows">
                  <div v-for="m in data.memberships" :key="m.membership_id" class="mem-row">
                    <router-link class="mem-group" :to="`/db/groups/${m.group_uid}`">{{ m.group_name }}</router-link>
                    <label class="mem-cell">
                      <span class="mem-label">入组</span>
                      <SaDatePicker
                        variant="bordered"
                        :model-value="m.join_date ?? ''"
                        :disabled="savingMembership === m.membership_id"
                        placeholder="入组"
                        @update:model-value="(v: string | null) => saveMembership(m, { join_date: v })"
                      />
                    </label>
                    <label class="mem-cell">
                      <span class="mem-label">离组</span>
                      <SaDatePicker
                        variant="bordered"
                        :model-value="m.leave_date ?? ''"
                        :disabled="savingMembership === m.membership_id"
                        placeholder="离组"
                        @update:model-value="(v: string | null) => saveMembership(m, { leave_date: v })"
                      />
                    </label>
                    <select class="mem-input mem-select" :value="m.status" :disabled="savingMembership === m.membership_id" @change="onStatus(m, $event)">
                      <option value="Active">在籍</option>
                      <option value="Former">已退出</option>
                      <option value="Inactive">暂停活动</option>
                    </select>
                    <input
                      type="text"
                      class="mem-input mem-pos"
                      :value="formatPositions(m.positions)"
                      placeholder="队内担当，如 队长 / 主唱（/ 分隔）"
                      :disabled="savingMembership === m.membership_id"
                      @change="onPositions(m, $event)"
                    />
                    <button
                      class="mem-del"
                      type="button"
                      :class="{ confirm: confirmRemoveMembership === m.membership_id }"
                      @click="removeMembership(m)"
                    >
                      {{ confirmRemoveMembership === m.membership_id ? '确认删除' : '×' }}
                    </button>
                  </div>
                </div>

                <div class="sa-add-row">
                  <SaSelect
                    v-model="pickedGroupId"
                    :fetch-options="fetchGroupOptions"
                    prefetch
                    placeholder="搜索组合并加入"
                  />
                  <SaDatePicker v-model="newJoinDate" variant="bordered" placeholder="入组日期" style="width: 148px" />
                  <input
                    v-model="newPositions"
                    type="text"
                    class="sa-input"
                    placeholder="担当（可选，如 主唱 / 中心）"
                    @keyup.enter="addMembership"
                  />
                  <button class="sa-btn-add" type="button" :disabled="!pickedGroupId || addingMembership" @click="addMembership">
                    {{ addingMembership ? '加入中…' : '加入组合' }}
                  </button>
                </div>
              </div>
            </section>

                        </template>

            <template v-else-if="wsTab === 'company'">
<!-- 所属公司 -->
            <section class="node node-teal">
              <header class="node-head">
                <span class="node-dot dot-teal" />
                <b>所属公司</b>
                <span class="node-note">含历史 · 角色/状态/起止点击即改 · 可搜索补挂或删除</span>
              </header>
              <div class="node-body">
                <div v-if="companyError" class="form-error">{{ companyError }}</div>
                <CompanyRelationsEditor
                  :relations="companyRelations"
                  @add="addCompany"
                  @update="updateCompany"
                  @remove="removeCompany"
                />
              </div>
            </section>            </template>

            <template v-else-if="wsTab === 'works'">
<!-- ③ 专辑（solo 发行主体） -->
            <section class="node node-teal">
              <header class="node-head">
                <span class="node-dot dot-teal" />
                <b>专辑</b>
                <span class="node-note">{{
                  data.albums.length
                    ? `${data.albums.length} 张 solo 专辑 · 点 ▸ 展开曲目 · 专辑名进入专辑工作台`
                    : '还没有 solo 专辑 · 点上方「生长」按钮 → ② 专辑 可按艺人名检索建碟'
                }}</span>
              </header>
              <div class="node-body album-list">
                <div v-if="!data.albums.length" class="side-empty">
                  暂无 solo 专辑。点上方「生长」按钮 → ② 专辑，可按艺人名检索并建碟；组合成员 solo 出专辑挂到该艺人名下也会出现在这里。
                </div>
                <div v-for="al in data.albums" :key="al.id" class="album-row" :class="{ open: expandedAlbums[al.id] }">
                  <div class="album-head" @click="toggleAlbum(al)">
                    <span class="al-chev">{{ expandedAlbums[al.id] ? '▾' : '▸' }}</span>
                    <router-link class="al-name" :to="`/db/albums/${al.uid}`" @click.stop>{{ al.name }}</router-link>
                    <span class="al-meta"
                      >{{ al.release_date || '缺日期' }} · {{ al.track_count }} 曲<template v-if="al.video_count">
                        · {{ al.video_count }} 影像</template
                      ></span
                    >
                  </div>
                  <div v-if="expandedAlbums[al.id]" class="album-tracks">
                    <div v-if="albumTracksLoading[al.id]" class="side-empty">加载曲目…</div>
                    <div v-else-if="!(albumTracks[al.id] || []).length" class="side-empty">暂无曲目</div>
                    <div v-for="t in albumTracks[al.id] || []" :key="t.id" class="track-line">
                      <span class="tn">{{ t.disc_number > 1 ? `${t.disc_number}-` : '' }}{{ t.track_number }}</span>
                      <router-link v-if="t.song_uid" :to="`/db/songs/${t.song_uid}`" class="tname">{{ t.song_chinese_name || t.song_name }}</router-link>
                      <span v-else class="tname">{{ t.song_name || `歌曲 #${t.song_id}` }}</span>
                      <span class="tdur">{{ t.song_duration != null ? formatDuration(t.song_duration) : '' }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <!-- ④ solo 歌曲 -->
            <section class="node node-teal" v-if="data.songs.length">
              <header class="node-head">
                <span class="node-dot dot-teal" />
                <b>solo 歌曲</b>
                <span class="node-note">{{ data.songs.length }} 首 · 发行主体为本艺人（含组合成员 solo 出歌）· 点击进入歌曲工作台</span>
              </header>
              <div class="node-body track-list">
                <div v-for="s in data.songs" :key="s.uid" class="track-row">
                  <span class="tk-no">{{ s.release_date || '缺日期' }}</span>
                  <router-link :to="`/db/songs/${s.uid}`" class="tk-name">{{ s.chinese_name || s.name }}</router-link>
                  <span class="tdur">{{ s.duration != null ? formatDuration(s.duration) : '' }}</span>
                </div>
              </div>
            </section>
            </template>

            <template v-if="growthPanelOpen">
            <section class="node">
              <header class="node-head">
                <b>生长</b>
                <span class="node-note">身份补全 · 专辑检索建碟</span>
                <button class="grow-toggle" type="button" @click="toggleGrowth">{{ growthOpen ? '已展开' : '展开' }}</button>
              </header>
              <div class="node-body">
<div class="growth-steps">
                  <button
                    v-for="s in GROWTH_STEPS"
                    :key="s.id"
                    type="button"
                    class="growth-step-tab"
                    :class="{ on: growthStep === s.id }"
                    @click="growthStep = s.id"
                  >
                    {{ s.title }}
                  </button>
                </div>

                <!-- 步骤 ① 身份与公司 -->
                <template v-if="growthStep === 1">
                  <p class="side-empty">
                    按「{{ data.artist.name }}」聚合检索 K-pop Fandom / 维基百科 / TMDB，完善身份字段；公司关系请在「所属公司」区搜索补挂。
                  </p>
                  <textarea
                    v-model="growthUrls"
                    class="growth-urls"
                    rows="2"
                    placeholder="可选：粘贴来源链接（每行一条），留空则自动检索"
                  />
                  <div class="growth-actions">
                    <button class="grow-cta" type="button" :disabled="growthPreviewing" @click="runGrowth(false)">
                      {{ growthPreviewing ? '搜索中…' : '🔍 自动检索' }}
                    </button>
                    <button
                      class="grow-cta secondary"
                      type="button"
                      :disabled="growthPreviewing || !growthUrls.trim()"
                      @click="runGrowth(true)"
                    >
                      用链接更新
                    </button>
                  </div>
                  <div v-if="growthError" class="form-error">{{ growthError }}</div>
                  <div v-if="growthDone.length" class="side-empty ok-note">
                    已写入：{{ growthDone.map((f) => APPLICABLE_LABELS[f] || f).join('、') }} ✓
                  </div>
                  <div v-if="growthPreview?.items.length" class="growth-items">
                    <label v-for="item in growthPreview.items" :key="item.field" class="growth-item" :class="{ same: item.same }">
                      <input type="checkbox" v-model="growthChecked[item.field]" />
                      <span class="gi-main">
                        <b>{{ item.label }}</b>
                        <span class="gi-proposed" :title="growthItemTitle(item)">{{ item.proposed }}</span>
                        <small>{{ item.sources.join(' / ') }}{{ item.conflict ? ' · 与现值冲突' : '' }}</small>
                      </span>
                    </label>
                  </div>
                  <div v-else-if="growthPreview && !growthPreviewing" class="side-empty">没有可用的字段提案。</div>
                  <button
                    v-if="growthPreview?.items.length"
                    class="grow-cta block"
                    type="button"
                    :disabled="growthApplying"
                    @click="applyGrowth"
                  >
                    {{ growthApplying ? '应用中…' : '应用所选字段' }}
                  </button>
                </template>

                <!-- 步骤 ② 专辑 -->
                <template v-else-if="growthStep === 2">
                  <p class="side-empty">
                    按「{{ data.artist.name }}」在 iTunes / Deezer / MusicBrainz 检索 solo 发行物，勾选后建碟（自动下载封面）。
                  </p>
                  <div v-if="albumError" class="form-error">{{ albumError }}</div>
                  <div v-if="albumResult" class="side-empty ok-note">{{ albumResult }} ✓</div>
                  <div v-if="albumSearching" class="side-empty">正在检索发行物…</div>
                  <div v-else-if="albumNote" class="side-empty">{{ albumNote }}</div>
                  <div v-else-if="!albumSuggestions.length" class="side-empty">点击下方按钮开始检索。</div>
                  <template v-if="albumSuggestions.length">
                    <div class="growth-items">
                      <label v-for="(p, i) in albumSuggestions" :key="`${p.name}-${i}`" class="growth-item" :class="{ same: p.action === 'exists' }">
                        <input type="checkbox" v-model="albumChecked[i]" />
                        <span class="gi-main">
                          <b>{{ p.name }}</b>
                          <small>
                            {{ ALBUM_ACTION_LABELS[p.action] || p.action }}
                            <template v-if="p.record_type"> · {{ ALBUM_TYPE_LABELS_GROWTH[p.record_type] || p.record_type }}</template>
                            <template v-if="p.year"> · {{ p.year }}</template>
                            <template v-if="p.sources?.length"> · {{ p.sources.join('/') }}</template>
                          </small>
                        </span>
                      </label>
                    </div>
                    <button class="grow-cta block" type="button" :disabled="albumApplying" @click="applyAlbums">
                      {{ albumApplying ? '应用中…' : '应用所选专辑（含曲目）' }}
                    </button>
                  </template>
                  <button class="grow-cta block secondary" type="button" :disabled="albumSearching" @click="searchAlbums">
                    {{ albumSearching ? '检索中…' : '🔍 检索 solo 专辑' }}
                  </button>
                </template>

                <!-- 步骤 ③ 歌曲 / 曲目 -->
                <template v-else>
                  <p class="side-empty">
                    曲目随步骤 ② 的专辑提案一并写入：专辑应用时自动抓取碟志，未在库的歌曲会创建为该艺人的 solo 歌曲，并挂到对应专辑下。
                  </p>
                  <div v-if="albumResult" class="side-empty ok-note">{{ albumResult }} ✓</div>
                  <p class="side-empty">
                    已在库但缺曲目的专辑：重新执行步骤 ② 的检索，提案会标记为「补曲目」，勾选应用即可按碟志补齐。
                  </p>
                </template>
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

.board { display: grid; grid-template-columns: minmax(0, 1fr); gap: 11px; align-items: start; }
.ws-tabs { margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
.ws-tabs > :first-child { flex: 1 1 auto; min-width: 0; }
/* 「生长」入口（v3.5.6 取代原页签）：小号胶囊按钮，与页签容器视觉区分开；
   展开态只靠文案「收起生长」表达，不做填充高亮（用户是嫌它抢眼才取消页签的）。 */
.grow-entry { flex: 0 0 auto; height: 28px; padding: 0 12px; border: 1px dashed var(--sa-border); border-radius: 9999px; background: transparent; color: var(--sa-text-secondary); font-size: 12px; font-weight: 600; font-family: inherit; cursor: pointer; }
.grow-entry:hover, .grow-entry.on { border-color: var(--sa-accent); border-style: solid; color: var(--sa-accent); background: var(--sa-accent-subtle); }
.checkup-node .node-body { display: flex; flex-direction: column; gap: 4px; }

.board-main { display: flex; flex-direction: column; gap: 9px; min-width: 0; }

.rail { display: flex; flex-direction: column; gap: 9px; position: sticky; top: 60px; }

.node-accent { border-left-color: var(--sa-accent); }

.node-teal { border-left-color: #18a058; }

.node-dot { width: 8px; height: 8px; border-radius: 50%; align-self: center; flex-shrink: 0; }

.dot-accent { background: var(--sa-accent); }

.dot-teal { background: #18a058; }

.f-input:focus { border-bottom-color: var(--sa-accent); }

.group-cards { display: flex; flex-direction: column; gap: 7px; }

.mem-rows { display: flex; flex-direction: column; gap: 6px; }

.mem-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 7px 9px; border: 1px solid var(--sa-border); border-radius: 11px; }

.mem-group { font-weight: 800; font-size: 13px; color: var(--sa-text-primary); text-decoration: none; flex-shrink: 0; }

.mem-group:hover { color: var(--sa-accent); }

.mem-cell { display: flex; align-items: center; gap: 4px; }

.mem-label { font-size: 10px; font-weight: 800; color: var(--sa-text-tertiary); }

.mem-input { border: none; background: transparent; color: var(--sa-text-primary); font-size: 12px; font-weight: 600; font-family: inherit; outline: none; border-bottom: 1px dashed var(--sa-border); padding: 1px 0; }

.mem-input:focus { border-bottom-color: var(--sa-accent); }

.mem-select { cursor: pointer; }

.mem-pos { flex: 1; min-width: 140px; }

.mem-del { flex-shrink: 0; width: 22px; height: 22px; border: none; border-radius: 6px; background: none; color: var(--sa-text-tertiary); font-size: 13px; line-height: 1; cursor: pointer; }

.mem-del:hover { color: var(--dh-bad); background: rgba(208,48,80,.1); }

.mem-del.confirm { width: auto; padding: 0 8px; font-size: 10.5px; font-weight: 700; color: #fff; background: var(--dh-bad); }

.mem-date { height: 34px; padding: 0 8px; border: 1px solid var(--sa-border-subtle); border-radius: 8px; background: var(--sa-bg); color: var(--sa-text-primary); font-size: 12px; font-family: inherit; }

.album-strip { display: grid; grid-template-columns: repeat(auto-fill, minmax(185px, 1fr)); gap: 7px; }

.album-head:hover .al-name { color: var(--sa-accent); }

a.tname:hover { color: var(--sa-accent); }

.tdur { margin-left: auto; font-size: 11px; color: var(--sa-text-tertiary); font-variant-numeric: tabular-nums; }

.track-list { display: flex; flex-direction: column; }

.track-row { display: flex; align-items: center; gap: 10px; padding: 6px 8px; border-radius: 8px; }

.track-row:hover { background: var(--sa-subtle); }

.tk-no { flex-shrink: 0; min-width: 76px; font-size: 11px; font-weight: 600; color: var(--sa-text-tertiary); font-variant-numeric: tabular-nums; }

.tk-name { flex: 1; min-width: 0; font-size: 12.5px; font-weight: 600; color: var(--sa-text-primary); text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

a.tk-name:hover { color: var(--sa-accent); }

.tdur { flex-shrink: 0; font-size: 11px; color: var(--sa-text-tertiary); font-variant-numeric: tabular-nums; }

.video-strip { display: grid; grid-template-columns: repeat(auto-fill, minmax(185px, 1fr)); gap: 7px; }

.video-tile { border: 1px solid var(--sa-border-subtle); border-radius: 10px; padding: 7px 11px; background: var(--sa-bg); text-decoration: none; color: var(--sa-text-primary); }

.video-tile:hover { border-color: var(--sa-accent); }

.vd-name { font-weight: 700; font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.vd-meta { font-size: 10.5px; color: var(--sa-text-tertiary); margin-top: 2px; }

.fix { display: flex; gap: 8px; padding: 6px 0; border-top: 1px dashed var(--sa-border-subtle); align-items: flex-start; }

.fix:first-of-type { border-top: none; }

.fix-tx { flex: 1; font-size: 12px; line-height: 1.5; }

.fix-sub { font-size: 11px; color: var(--sa-text-tertiary); margin-top: 2px; }

.unlock-btn:hover { color: var(--dh-bad); border-color: var(--dh-bad); }

.desc-suggest { display: flex; align-items: center; gap: 8px; margin-top: 5px; flex-wrap: wrap; }

.desc-suggest-btn { border: 1px solid var(--sa-border); background: none; color: var(--sa-text-secondary); border-radius: 7px; font-size: 11px; font-weight: 600; padding: 2px 9px; cursor: pointer; font-family: inherit; }

.desc-suggest-btn:hover:not(:disabled) { color: var(--sa-accent); border-color: var(--sa-accent); }

.desc-suggest-btn:disabled { opacity: .5; cursor: default; }

.desc-suggest-src { font-size: 10.5px; color: var(--sa-text-tertiary); }

.grow-toggle { margin-left: auto; border: 1px solid var(--sa-accent); background: var(--sa-accent-subtle); color: var(--sa-accent); border-radius: 7px; font-size: 11px; font-weight: 700; padding: 2px 10px; cursor: pointer; font-family: inherit; }

.growth-urls { width: 100%; box-sizing: border-box; border: 1px solid var(--sa-border-subtle); border-radius: 8px; background: var(--sa-bg); color: var(--sa-text-primary); font-size: 11.5px; font-family: inherit; padding: 6px 9px; outline: none; resize: vertical; margin-bottom: 7px; }

.growth-urls:focus { border-color: var(--sa-accent); }

.growth-actions { display: flex; gap: 6px; margin-bottom: 8px; }

.grow-cta.secondary { background: none; color: var(--sa-text-secondary); border-color: var(--sa-border); }

.grow-cta:disabled { opacity: .45; cursor: default; }

.growth-items { display: flex; flex-direction: column; gap: 5px; margin-top: 4px; }

.growth-item { display: flex; gap: 8px; padding: 6px 8px; border: 1px solid var(--sa-border-subtle); border-radius: 9px; align-items: flex-start; cursor: pointer; }

.growth-item:hover { border-color: var(--sa-accent); }

.growth-item.same { opacity: .55; }

.gi-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }

.gi-main b { font-size: 11.5px; }

.gi-proposed { font-size: 11.5px; color: var(--sa-text-secondary); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; word-break: break-all; }

.gi-main small { font-size: 10px; color: var(--sa-text-tertiary); }

.ok-note { color: var(--dh-ok); }

.album-grow-btn { margin-left: auto; border: 1px solid var(--sa-accent); background: var(--sa-accent-subtle); color: var(--sa-accent); border-radius: 7px; font-size: 11px; font-weight: 700; padding: 2px 10px; cursor: pointer; font-family: inherit; flex-shrink: 0; }

.album-growth { display: flex; flex-direction: column; gap: 5px; margin-bottom: 8px; }

@media (max-width: 960px) {
  .mv-container { padding: 12px 16px 80px; }
  .board { grid-template-columns: 1fr; }
  .rail { position: static; }
}

@media (max-width: 768px) {
  .mv-container { padding: 12px 14px 110px; }
  .ws-tab-row { margin-bottom: 10px; }
  .board { gap: 9px; }
}

</style>
<style scoped src="./db-workspace.css"></style>
