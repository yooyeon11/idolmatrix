<script setup lang="ts">import { computed, onMounted, ref, watch } from 'vue'
import SaSelect from '@/components/SaSelect.vue'
import type { SaOption } from '@/components/SaSelect.vue'
import SaOverflowTabs from '@/components/SaOverflowTabs.vue'
import { SearchOutlined } from '@/components/icons'
import SearchCompleteSheet from '@/components/db/SearchCompleteSheet.vue'
import CompanyRelationsEditor from '@/components/db/CompanyRelationsEditor.vue'
import EntityMediaCard from '@/components/db/EntityMediaCard.vue'
import SocialMediaEditor from '@/components/db/SocialMediaEditor.vue'
import SettingsPhotoFolders from '@/views/settings/SettingsPhotoFolders.vue'
import { companyRelationsApi, type CompanyRelation, type CompanyRelationPatch } from '@/api/companyRelations'
import { dbFieldSuggestApi } from '@/api/dbViews'
import { dbViewsApi, type GroupWorkspaceResponse } from '@/api/dbViews'
import { loadGrowthDraft, saveGrowthDraft } from '@/utils/growthDraft'
import { groupsApi } from '@/api/groups'
import { artistsApi } from '@/api/artists'
import { membershipsApi } from '@/api/memberships'
import { albumsApi } from '@/api/albums'
import { entityLocksApi, type EntityLockMap } from '@/api/entityLocks'
import type { AlbumTrackDetail } from '@/types/models'
import { formatPositions, parsePositionsInput } from '@/utils/positions'

const props = defineProps<{ uid: string }>()
// 「返回列表」由宿主处理：整页模式 = 页面壳自己的返回按钮，内嵌模式 = 设置页的「← 返回列表」

const uid = computed(() => props.uid)
const data = ref<GroupWorkspaceResponse | null>(null)
const locks = ref<EntityLockMap>({})
const loading = ref(false)
const loadError = ref('')
const savingField = ref('')
const saveError = ref('')

const searchOpen = ref(false)
const growthUrls = ref<string[]>([])
let loadSeq = 0

function restoreGrowthDraft(forUid: string) {
  const draft = loadGrowthDraft(forUid)
  growthUrls.value = draft?.urls ?? []
}

function persistGrowthDraft() {
  if (!uid.value) return
  saveGrowthDraft(uid.value, {
    step: 1,
    urls: growthUrls.value,
    completedSteps: [],
  })
}

const expandedAlbums = ref<Record<number, boolean>>({})
const albumTracks = ref<Record<number, AlbumTrackDetail[]>>({})
const albumTracksLoading = ref<Record<number, boolean>>({})

// 手动添加专辑（挂到本组合）
const newAlbumName = ref('')
const newAlbumDate = ref('')
const addingAlbum = ref(false)
const albumError = ref('')

async function load() {
  if (!uid.value) return
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const next = await dbViewsApi.getGroupWorkspace(uid.value)
    if (seq !== loadSeq) return
    data.value = next
    if (data.value) {
      locks.value = await entityLocksApi.get('groups', data.value.group.id)
    }
    descDraft.value = null
  } catch (e) {
    if (seq !== loadSeq) return
    loadError.value = e instanceof Error ? e.message : '加载工作台失败'
  } finally {
    if (seq === loadSeq) loading.value = false
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
    const r = await dbFieldSuggestApi.suggestGroupField(uid.value, 'description')
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

onMounted(() => {
  restoreGrowthDraft(uid.value)
  load()
})
watch(uid, () => {
  expandedAlbums.value = {}
  albumTracks.value = {}
  searchOpen.value = false
  restoreGrowthDraft(uid.value)
  load()
})

watch(growthUrls, () => persistGrowthDraft(), { deep: true })

const row = computed(() => data.value?.row ?? null)
const issues = computed(() => data.value?.issues ?? [])
const sourceUrls = computed(() =>
  (data.value?.group.external_links ?? [])
    .map((l) => (typeof l === 'string' ? l : (l.url as string) || ''))
    .filter(Boolean),
)
const lockedFields = computed(() => Object.keys(locks.value).filter((k) => locks.value[k]))

// ===== 工作台页签（HeroUI Tabs，见 components/SaOverflowTabs.vue）=====
const WS_TABS = [
  { key: 'base', label: '基本信息' },
  { key: 'members', label: '成员' },
  { key: 'albums', label: '专辑' },
  { key: 'orgs', label: '资料来源' },
  { key: 'media', label: '媒体' },
]
const wsTab = ref('base')

// 体检摘要：问题 / 来源 / 锁
const issueTotal = computed(() => {
  const c = data.value?.issue_counts
  return c ? c.error + c.warning : 0
})


function openSearch() {
  persistGrowthDraft()
  searchOpen.value = true
}

async function onSearchApplied() {
  persistGrowthDraft()
  await load()
}

function onSearchClose() {
  persistGrowthDraft()
  searchOpen.value = false
}

// 「去生长成员 / 生长专辑 / 去补曲目」等快捷入口：打开生长（补齐信息）向导。
// step 为向导建议聚焦的类别（3=成员、5=专辑、6=曲目），向导内部按清单展开。
function openGrowth(_step: number) {
  openSearch()
}

const searchAlbums = computed(() =>
  (data.value?.albums ?? []).map((a) => ({
    id: a.id,
    name: a.name,
    track_count: a.track_count ?? 0,
  })),
)

const searchGroupFields = computed(() => {
  const g = data.value?.group
  if (!g) return null
  return {
    debut_date: g.debut_date,
    korean_name: g.korean_name,
    english_name: g.english_name,
    chinese_name: g.chinese_name,
    description: g.description,
    tagline: g.tagline,
  }
})

// ===== 基本信息 =====
interface FieldDef {
  key: string
  label: string
  kind: 'date' | 'text' | 'select' | 'textarea'
  options?: string[]
  placeholder?: string
}

const GROUP_TYPE_LABELS: Record<string, string> = {
  'Girl Group': '女子组合',
  'Boy Group': '男子组合',
  'Co-ed': '混合组合',
  Project: '企划组合',
  'Sub-unit': '小分队',
}

const groupTypeOptions = Object.entries(GROUP_TYPE_LABELS).map(([value, label]) => ({ label, value }))

function groupTypeLabel(v?: string | null): string {
  if (!v) return '类型未填'
  return GROUP_TYPE_LABELS[v] || v
}

const FIELDS: FieldDef[] = [
  { key: 'debut_date', label: '出道日期', kind: 'date' },
  {
    key: 'group_type',
    label: '组合类型',
    kind: 'select',
    options: Object.keys(GROUP_TYPE_LABELS),
  },
  { key: 'origin_country', label: '国家/地区', kind: 'text', placeholder: '如：韩国' },
  { key: 'chinese_name', label: '中文名', kind: 'text', placeholder: '点击填写' },
  { key: 'english_name', label: '英文名', kind: 'text', placeholder: '点击填写' },
  { key: 'korean_name', label: '韩文名', kind: 'text', placeholder: '点击填写' },
]

const TEXT_FIELDS = FIELDS.filter((f) => f.kind === 'text')

function fieldValue(key: string): string {
  const g = data.value?.group
  if (!g) return ''
  return (g as unknown as Record<string, string | null>)[key] ?? ''
}

async function saveField(key: string, value: string | null) {
  const g = data.value?.group
  if (!g) return
  saveError.value = ''
  savingField.value = key
  try {
    await groupsApi.update(g.id, { [key]: value === '' ? null : value })
    locks.value = await entityLocksApi.put('groups', g.id, { ...locks.value, [key]: true })
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    savingField.value = ''
  }
}

async function saveSocialMedia(map: Record<string, string>) {
  const g = data.value?.group
  if (!g) return
  saveError.value = ''
  savingField.value = 'social_media'
  try {
    await groupsApi.update(g.id, { social_media: Object.keys(map).length ? map : null })
    await load()
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '保存社交媒体失败'
  } finally {
    savingField.value = ''
  }
}

async function toggleHideFromHome(ev: Event) {
  const g = data.value?.group
  if (!g) return
  const checked = (ev.target as HTMLInputElement).checked
  saveError.value = ''
  savingField.value = 'hide_from_home'
  try {
    await groupsApi.update(g.id, { hide_from_home: checked })
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
  const g = data.value?.group
  if (!g) return
  const next = { ...locks.value }
  delete next[key]
  locks.value = await entityLocksApi.put('groups', g.id, next)
}

const filledFieldKeys = computed(() =>
  FIELDS.map((f) => f.key)
    .concat('description')
    .filter((k) => fieldValue(k) !== ''),
)

async function lockAllFilled() {
  const g = data.value?.group
  if (!g) return
  saveError.value = ''
  try {
    const next: EntityLockMap = { ...locks.value }
    for (const k of filledFieldKeys.value) next[k] = true
    locks.value = await entityLocksApi.put('groups', g.id, next)
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '锁定失败'
  }
}

async function unlockAll() {
  const g = data.value?.group
  if (!g) return
  locks.value = await entityLocksApi.put('groups', g.id, {})
}

// ===== 成员抽屉 =====
const drawerMemberName = ref<string | null>(null)
const drawerSaving = ref('')
const drawerError = ref('')
const drawerMember = computed(() => row.value?.members.find((m) => m.name === drawerMemberName.value) ?? null)

function openDrawer(name: string) {
  drawerMemberName.value = name
}
function closeDrawer() {
  drawerMemberName.value = null
}

async function saveMembership(patch: Record<string, unknown>) {
  const m = drawerMember.value
  if (!m) return
  drawerError.value = ''
  drawerSaving.value = 'membership'
  try {
    await membershipsApi.update(m.membership_id, patch)
    await load()
  } catch (e) {
    drawerError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    drawerSaving.value = ''
  }
}

function onDrawerDateValue(kind: 'join_date' | 'leave_date', v: string | null) {
  void saveMembership({ [kind]: v })
}
function onDrawerStatus(ev: Event) {
  saveMembership({ status: (ev.target as HTMLSelectElement).value })
}
function onDrawerPositions(ev: Event) {
  const raw = (ev.target as HTMLInputElement).value
  saveMembership({ positions: parsePositionsInput(raw) })
}

async function removeDrawerMembership() {
  const m = drawerMember.value
  if (!m || !window.confirm(`确定删除「${m.name}」的这条成员记录？可在回收站恢复。`)) return
  drawerError.value = ''
  drawerSaving.value = 'remove'
  try {
    await membershipsApi.remove(m.membership_id)
    closeDrawer()
    await load()
  } catch (e) {
    drawerError.value = e instanceof Error ? e.message : '删除失败'
  } finally {
    drawerSaving.value = ''
  }
}

// ===== 添加成员（组合侧直接拉艺人入组；支持新建艺人）=====
const NEW_ARTIST_PREFIX = '__new__:'
const pickedArtistId = ref<number | null>(null)
const newArtistName = ref('')
const newMemberJoinDate = ref('')
const newMemberPositions = ref('')
const addingMember = ref(false)
const memberError = ref('')

/** 选择器当前值：选中「新建艺人」时回填合成值，让下拉显示该项 */
const artistSelValue = computed(() =>
  newArtistName.value ? NEW_ARTIST_PREFIX + newArtistName.value : pickedArtistId.value,
)

/** 供 SaSelect 远程搜索：库里没有输入的名字时，顶部给「新建艺人」入口 */
async function fetchArtistOptions(q: string): Promise<SaOption[]> {
  const term = q.trim()
  const list = term ? await artistsApi.fuzzy(term) : await artistsApi.brief()
  const opts: SaOption[] = list.map((a) => ({
    value: String(a.id),
    label: a.chinese_name ? `${a.name}（${a.chinese_name}）` : a.name,
  }))
  if (term && !list.some((a) => a.name === term)) {
    opts.unshift({ value: NEW_ARTIST_PREFIX + term, label: `➕ 新建艺人「${term}」` })
  }
  return opts
}

function onArtistPick(v: string | number | null) {
  const s = v == null ? '' : String(v)
  if (s.startsWith(NEW_ARTIST_PREFIX)) {
    newArtistName.value = s.slice(NEW_ARTIST_PREFIX.length)
    pickedArtistId.value = null
  } else {
    pickedArtistId.value = s ? Number(s) : null
    newArtistName.value = ''
  }
}

async function addMember() {
  if (!data.value) return
  if (!pickedArtistId.value && !newArtistName.value.trim()) return
  addingMember.value = true
  memberError.value = ''
  try {
    let artistId: number | null = pickedArtistId.value
    // 库里没有该艺人 → 先建艺人，再建成员关系
    if (artistId == null) {
      const created = await artistsApi.create({ name: newArtistName.value.trim() })
      artistId = created.id
    }
    await membershipsApi.create({
      group_id: data.value.group.id,
      artist_id: artistId,
      status: 'Active',
      join_date: newMemberJoinDate.value || null,
      positions: parsePositionsInput(newMemberPositions.value),
    })
    pickedArtistId.value = null
    newArtistName.value = ''
    newMemberJoinDate.value = ''
    newMemberPositions.value = ''
    await load()
  } catch (e) {
    memberError.value = e instanceof Error ? e.message : '添加成员失败'
  } finally {
    addingMember.value = false
  }
}

const ARTIST_FIELDS: FieldDef[] = [
  { key: 'birth_date', label: '出生日期', kind: 'date' },
  { key: 'birth_place', label: '出生地', kind: 'text' },
  { key: 'occupation', label: '职业', kind: 'text' },
  { key: 'chinese_name', label: '中文名', kind: 'text' },
  { key: 'english_name', label: '英文名', kind: 'text' },
  { key: 'korean_name', label: '韩文名', kind: 'text' },
]
const artistLocks = ref<EntityLockMap>({})

watch(drawerMemberName, async (name) => {
  artistLocks.value = {}
  const m = row.value?.members.find((x) => x.name === name)
  if (m?.artist_id) {
    try {
      artistLocks.value = await entityLocksApi.get('artists', m.artist_id)
    } catch {
      artistLocks.value = {}
    }
  }
})

function artistValue(key: string): string {
  const m = drawerMember.value
  if (!m) return ''
  return (m as unknown as Record<string, string | null>)[key] ?? ''
}

async function saveArtistField(key: string, value: string | null) {
  const m = drawerMember.value
  if (!m?.artist_id) return
  drawerError.value = ''
  drawerSaving.value = key
  try {
    await artistsApi.update(m.artist_id, { [key]: value === '' ? null : value })
    artistLocks.value = await entityLocksApi.put('artists', m.artist_id, {
      ...artistLocks.value,
      [key]: true,
    })
    await load()
  } catch (e) {
    drawerError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    drawerSaving.value = ''
  }
}

function onArtistFieldChange(f: FieldDef, ev: Event) {
  saveArtistField(f.key, (ev.target as HTMLInputElement).value)
}

const subunitTagsByArtist = computed<Record<number, string[]>>(() => {
  const out: Record<number, string[]> = {}
  for (const su of data.value?.sub_units ?? []) {
    for (const aid of su.member_ids) {
      ;(out[aid] ??= []).push(su.name)
    }
  }
  return out
})

function completenessColor(score: number) {
  if (score >= 80) return 'var(--dh-ok)'
  if (score >= 50) return 'var(--dh-warn)'
  return 'var(--dh-bad)'
}

const sevMeta: Record<string, { label: string; cls: string }> = {
  error: { label: '错误', cls: 'sev-error' },
  warning: { label: '缺失', cls: 'sev-warning' },
  hint: { label: '提示', cls: 'sev-hint' },
}

const mediaStamp = ref(Date.now())

function avatarUrl() {
  if (!data.value?.group.avatar_path) return ''
  return `/api/groups/${data.value.group.id}/avatar?t=${mediaStamp.value}`
}

function onMediaUpdated() {
  mediaStamp.value = Date.now()
  void load()
}

// ===== 所属公司编辑 =====
const companyError = ref('')
const companyRelations = computed<CompanyRelation[]>(() =>
  (row.value?.companies ?? []).map((c) => ({
    id: c.relation_id,
    company_id: c.company_id,
    company_name: c.name,
    role: c.role,
    status: c.status,
    start_date: c.start_date,
    end_date: c.end_date,
  })),
)

async function addCompany(companyId: number, role: string | null) {
  if (!data.value) return
  companyError.value = ''
  try {
    await companyRelationsApi.createForGroup({
      group_id: data.value.group.id,
      company_id: companyId,
      role,
      status: 'Active',
    })
    await load()
  } catch (e) {
    companyError.value = e instanceof Error ? e.message : '添加公司失败'
  }
}
async function updateCompany(relationId: number, patch: CompanyRelationPatch) {
  companyError.value = ''
  try {
    await companyRelationsApi.updateForGroup(relationId, patch)
    await load()
  } catch (e) {
    companyError.value = e instanceof Error ? e.message : '保存失败'
  }
}
async function removeCompany(relationId: number) {
  companyError.value = ''
  try {
    await companyRelationsApi.removeForGroup(relationId)
    await load()
  } catch (e) {
    companyError.value = e instanceof Error ? e.message : '删除失败'
  }
}

// ===== 资料来源（external_links）手动添加网址 =====
const newSourceUrl = ref('')
const addingSource = ref(false)
const sourceError = ref('')

function sourceEntryUrl(l: unknown): string {
  return typeof l === 'string' ? l : ((l as { url?: string } | null)?.url ?? '')
}

async function addSource() {
  const url = newSourceUrl.value.trim()
  if (!url || !data.value) return
  if (!/^https?:\/\//i.test(url)) {
    sourceError.value = '请输入以 http:// 或 https:// 开头的网址'
    return
  }
  const existing = Array.isArray(data.value.group.external_links) ? data.value.group.external_links : []
  if (existing.some((l) => sourceEntryUrl(l) === url)) {
    sourceError.value = '该网址已在列表里'
    return
  }
  addingSource.value = true
  sourceError.value = ''
  try {
    await groupsApi.update(data.value.group.id, {
      external_links: [...existing, { url, source: '手动添加', synced_at: new Date().toISOString() }],
    })
    newSourceUrl.value = ''
    await load()
  } catch (e) {
    sourceError.value = e instanceof Error ? e.message : '添加网址失败'
  } finally {
    addingSource.value = false
  }
}
function initials(name: string) {
  return name.trim().slice(0, 2).toUpperCase()
}
function statusDot(m: { status: string; is_dangling: boolean }) {
  if (m.is_dangling) return 'dot-bad'
  return m.status === 'Former' ? 'dot-former' : 'dot-active'
}
function memberBirth(m: { birth_date?: string | null }) {
  return m.birth_date ? m.birth_date.slice(5) : ''
}

async function toggleAlbum(al: { id: number; track_count: number }) {
  const open = !expandedAlbums.value[al.id]
  expandedAlbums.value = { ...expandedAlbums.value, [al.id]: open }
  if (!open) return
  if (albumTracks.value[al.id]) return
  albumTracksLoading.value = { ...albumTracksLoading.value, [al.id]: true }
  try {
    albumTracks.value = {
      ...albumTracks.value,
      [al.id]: await albumsApi.tracks(al.id),
    }
  } catch {
    albumTracks.value = { ...albumTracks.value, [al.id]: [] }
  } finally {
    albumTracksLoading.value = { ...albumTracksLoading.value, [al.id]: false }
  }
}

/** 新建专辑并挂到本组合（release_artist_type='group'） */
async function addAlbum() {
  const name = newAlbumName.value.trim()
  if (!name || !data.value) return
  addingAlbum.value = true
  albumError.value = ''
  try {
    await albumsApi.create({
      name,
      release_date: newAlbumDate.value || null,
      release_artist_type: 'group',
      release_artist_id: data.value.group.id,
    })
    newAlbumName.value = ''
    newAlbumDate.value = ''
    await load()
  } catch (e) {
    albumError.value = e instanceof Error ? e.message : '添加专辑失败'
  } finally {
    addingAlbum.value = false
  }
}
</script>

<template>
  <div class="gwsp">

      <div v-if="loadError" class="load-error">{{ loadError }}</div>
      <div v-else-if="loading && !data" class="list-loading">加载关系页…</div>

      <template v-else-if="data">
        <section class="ident glass">
          <div class="ident-avatar">
            <img v-if="data.group.avatar_path" :src="avatarUrl()" alt="" />
            <template v-else>{{ data.group.name.slice(0, 2).toUpperCase() }}</template>
          </div>
          <div class="ident-main">
            <h1>
              {{ data.group.name }}
              <span v-if="data.group.korean_name" class="h-sub">{{ data.group.korean_name }}</span>
            </h1>
            <div class="ident-meta">
              <span class="meta-pill" :class="{ warn: !data.group.debut_date }">
                {{ data.group.debut_date ? `${data.group.debut_date} 出道` : '缺出道日期' }}
              </span>
              <span class="meta-pill">{{ groupTypeLabel(data.group.group_type) }}</span>
              <span v-if="row" class="meta-pill ok"
                >{{ row.members_active }} 在籍<template v-if="row.members_former">
                  / {{ row.members_former }} 退出</template
                ></span
              >
              <span v-if="data.sub_units.length" class="meta-pill su">{{ data.sub_units.length }} 小分队</span>
            </div>
          </div>
          <div v-if="row" class="ident-stats">
            <div class="stat"><b>{{ row.works.albums }}</b><span>专辑</span></div>
            <div class="stat"><b>{{ row.works.songs }}</b><span>歌曲</span></div>
            <div class="stat"><b>{{ row.works.videos }}</b><span>影像</span></div>
            <div class="stat score" :style="{ color: completenessColor(row.completeness) }">
              <b>{{ row.completeness }}</b><span>完整度</span>
            </div>
          </div>
          <div class="ident-actions">
            <button
              class="grow-cta grow-cta--icon"
              type="button"
              title="搜索补全"
              aria-label="搜索补全"
              @click="openSearch"
            >
              <SearchOutlined :size="17" />
            </button>
          </div>
        </section>

        <div class="board">
          <div class="board-main">
            <div class="ws-tabs">
              <SaOverflowTabs v-model="wsTab" :items="WS_TABS" aria-label="工作台板块" />
            </div>

            <template v-if="wsTab === 'base'">
            <section class="node">
              <header class="node-head">
                <b>基本信息</b>
                <span class="node-note">点击即改 · 保存后自动锁定</span>
              </header>
              <div class="node-body">
                <div v-if="saveError" class="form-error">{{ saveError }}</div>
                <div class="ws-fields">
                  <div class="ws-field ws-field--date" :class="{ 'is-locked': locks['debut_date'] }">
                    <span class="ws-label">出道日期<i v-if="locks['debut_date']" class="lock-mark">锁</i></span>
                    <SaDatePicker
                      variant="bordered"
                      :model-value="fieldValue('debut_date')"
                      :disabled="savingField === 'debut_date'"
                      @update:model-value="(v: string | null) => saveField('debut_date', v)"
                    />
                  </div>

                  <div class="ws-field" :class="{ 'is-locked': locks['group_type'] }">
                    <span class="ws-label">组合类型<i v-if="locks['group_type']" class="lock-mark">锁</i></span>
                    <SaSelect
                      :model-value="fieldValue('group_type') || null"
                      :options="groupTypeOptions"
                      placeholder="未填写"
                      :disabled="savingField === 'group_type'"
                      @update:model-value="(v) => saveField('group_type', v as string | null)"
                    />
                  </div>

                  <div v-for="f in TEXT_FIELDS" :key="f.key" class="ws-field" :class="{ 'is-locked': locks[f.key] }">
                    <span class="ws-label">{{ f.label }}<i v-if="locks[f.key]" class="lock-mark">锁</i></span>
                    <input
                      class="sa-input"
                      type="text"
                      :value="fieldValue(f.key)"
                      :placeholder="f.placeholder || '点击填写'"
                      :disabled="savingField === f.key"
                      @change="onFieldChange(f, $event)"
                    />
                  </div>

                  <div class="ws-field ws-field--wide" :class="{ 'is-locked': locks['description'] }">
                    <span class="ws-label">简介<i v-if="locks['description']" class="lock-mark">锁</i></span>
                    <textarea
                      class="sa-textarea"
                      rows="2"
                      :value="descDraft ?? (data.group.description ?? '')"
                      placeholder="组合完整介绍"
                      :disabled="savingField === 'description'"
                      @change="onFieldChange({ key: 'description', label: '简介', kind: 'textarea' }, $event)"
                    />
                    <div class="desc-suggest">
                      <button
                        v-if="!descDraft && !(data.group.description ?? '')"
                        type="button"
                        class="desc-suggest-btn"
                        :disabled="suggestBusy"
                        @click="suggestDescription"
                      >
                        {{ suggestBusy ? '搜索中…' : '🔍 单独搜索简介' }}
                      </button>
                      <span v-if="suggestSource" class="desc-suggest-src">来源：{{ suggestSource }} · 修改后点击保存</span>
                      <span v-else-if="suggestNone" class="desc-suggest-src">外部站点没有找到可用简介，可手动填写或稍后重试</span>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <section class="node">
              <header class="node-head">
                <b>所属公司</b>
                <span class="node-note">含历史 · 角色/状态/起止点击即改</span>
              </header>
              <div class="node-body">
                <div v-if="companyError" class="form-error">{{ companyError }}</div>
                <CompanyRelationsEditor
                  :relations="companyRelations"
                  :role-input="false"
                  add-label="添加公司"
                  @add="addCompany"
                  @update="updateCompany"
                  @remove="removeCompany"
                />
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
                  <span class="ws-sum__i"><i class="ws-dot ws-dot--src" />来源 {{ sourceUrls.length }}</span>
                  <span class="ws-sum__i"><i class="ws-dot ws-dot--lock" />锁 {{ lockedFields.length }}</span>
                </div>

                <details class="ws-sec">
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

            <template v-else-if="wsTab === 'members'">
            <section v-if="data.sub_units.length" class="node">
              <header class="node-head">
                <b>小分队</b>
                <span class="node-note">点击进入其工作台</span>
              </header>
              <div class="node-body subunit-cards">
                <router-link
                  v-for="su in data.sub_units"
                  :key="su.uid"
                  :to="`/db/groups/${su.uid}`"
                  class="subunit-card"
                >
                  <span class="su-name">{{ su.name }}</span>
                  <span class="su-meta">{{ su.members_active }} 人 · 完整度 {{ su.completeness }}</span>
                </router-link>
              </div>
            </section>

            <section v-if="row" class="node node--popout">
              <header class="node-head">
                <b>成员</b>
                <span class="node-note"
                  >{{ row.members_active }} 在籍<template v-if="row.members_former">
                    · {{ row.members_former }} 已退出</template
                  ></span
                >
              </header>
              <div class="node-body">
                <div class="member-canvas">
                  <button
                    v-for="m in row.members"
                    :key="m.membership_id"
                    type="button"
                    class="member-chip"
                    :class="{ open: drawerMemberName === m.name }"
                    @click="openDrawer(m.name)"
                  >
                    <span class="chip-status" :class="statusDot(m)" />
                    <span class="chip-name">{{ m.name }}</span>
                    <span v-if="m.korean_name" class="chip-kr">{{ m.korean_name }}</span>
                    <span class="chip-birth">{{ memberBirth(m) || '生日 —' }}</span>
                    <span class="chip-positions">{{ formatPositions(m.positions, '/') || '担当 —' }}</span>
                    <span
                      v-for="tag in (subunitTagsByArtist[m.artist_id] || []).slice(0, 2)"
                      :key="tag"
                      class="chip-su"
                      >{{ tag }}</span
                    >
                  </button>
                  <div v-if="!row.members.length" class="same-row">暂无成员</div>
                </div>

                <div class="sa-add-row">
                  <SaSelect
                    :model-value="artistSelValue"
                    :fetch-options="fetchArtistOptions"
                    prefetch
                    placeholder="搜索艺人，或直接输入新成员名"
                    @update:model-value="onArtistPick"
                  />
                  <SaDatePicker v-model="newMemberJoinDate" variant="bordered" placeholder="入组日期" style="width: 148px" />
                  <input
                    v-model="newMemberPositions"
                    type="text"
                    class="sa-input"
                    placeholder="担当（可选，如 主唱 / 中心）"
                    @keyup.enter="addMember"
                  />
                  <button
                    class="sa-btn-add"
                    type="button"
                    :disabled="(!pickedArtistId && !newArtistName) || addingMember"
                    @click="addMember"
                  >
                    {{ addingMember ? '添加中…' : newArtistName ? `新建「${newArtistName}」并加入` : '添加成员' }}
                  </button>
                </div>
                <div v-if="memberError" class="form-error">{{ memberError }}</div>
              </div>
            </section>
            </template>

            <template v-else-if="wsTab === 'albums'">
            <section class="node node--popout">
              <header class="node-head">
                <b>专辑</b>
                <span class="node-note"
                  >{{ data.albums.length }} 张 · 点击展开曲目
                  <template v-if="row"> · {{ row.works.songs }} 首歌曲</template></span
                >
              </header>
              <div class="node-body album-list">
                <div v-if="!data.albums.length" class="same-row">暂无专辑</div>
                <div v-for="al in data.albums" :key="al.id" class="album-row" :class="{ open: expandedAlbums[al.id] }">
                  <button type="button" class="album-head" @click="toggleAlbum(al)">
                    <span class="al-chev">{{ expandedAlbums[al.id] ? '▾' : '▸' }}</span>
                    <span class="al-name">{{ al.name }}</span>
                    <span class="al-meta"
                      >{{ al.release_date || '缺日期' }} · {{ al.track_count }} 曲<template v-if="al.video_count">
                        · {{ al.video_count }} 影像</template
                      ></span
                    >
                    <button
                      v-if="al.track_count === 0"
                      type="button"
                      class="fill-tracks"
                      @click.stop="openGrowth(6)"
                    >
                      补曲目
                    </button>
                  </button>
                  <div v-if="expandedAlbums[al.id]" class="album-tracks">
                    <div v-if="albumTracksLoading[al.id]" class="same-row">加载曲目…</div>
                    <div v-else-if="!(albumTracks[al.id] || []).length" class="same-row">
                      暂无曲目
                      <button type="button" class="inline-link" @click="openGrowth(6)">去补曲目</button>
                    </div>
                    <div v-for="t in albumTracks[al.id] || []" :key="t.id" class="track-line">
                      <span class="tn">{{ t.disc_number > 1 ? `${t.disc_number}-` : '' }}{{ t.track_number }}</span>
                      <span class="tname">{{ t.song_name || `歌曲 #${t.song_id}` }}</span>
                    </div>
                  </div>
                </div>

                <div class="sa-add-row">
                  <input
                    v-model="newAlbumName"
                    type="text"
                    class="sa-input"
                    placeholder="新专辑名称，如 Fancy You"
                    @keyup.enter="addAlbum"
                  />
                  <SaDatePicker v-model="newAlbumDate" variant="bordered" placeholder="发行日期（可选）" style="width: 168px" />
                  <button class="sa-btn-add" type="button" :disabled="!newAlbumName.trim() || addingAlbum" @click="addAlbum">
                    {{ addingAlbum ? '添加中…' : '添加专辑' }}
                  </button>
                </div>
                <div v-if="albumError" class="form-error">{{ albumError }}</div>
              </div>
            </section>
            </template>

            <template v-else-if="wsTab === 'orgs'">
            <section class="node">
              <header class="node-head">
                <b>资料来源</b>
                <span class="node-note">搜索补全写入的来源链接</span>
              </header>
              <div class="node-body">
                <div v-if="!sourceUrls.length" class="side-empty">暂无来源，可手动添加网址或在搜索补全里补</div>
                <a v-for="(u, i) in sourceUrls" :key="i" :href="u" target="_blank" rel="noreferrer" class="src-line">{{ u }}</a>

                <div class="sa-add-row">
                  <input
                    v-model="newSourceUrl"
                    type="text"
                    class="sa-input"
                    placeholder="粘贴来源网址，如 https://zh.wikipedia.org/..."
                    @keyup.enter="addSource"
                  />
                  <button class="sa-btn-add" type="button" :disabled="!newSourceUrl.trim() || addingSource" @click="addSource">
                    {{ addingSource ? '添加中…' : '添加网址' }}
                  </button>
                </div>
                <div v-if="sourceError" class="form-error">{{ sourceError }}</div>
              </div>
            </section>
            <section class="node media-node">
              <header class="node-head">
                <b>社交媒体</b>
                <span class="node-note">仅展示有链接的平台 · AI 搜索需人工确认后写入</span>
              </header>
              <div class="node-body">
                <SocialMediaEditor
                  entity-type="group"
                  :entity-id="data.group.id"
                  :model-value="(data.group.social_media as Record<string, string> | null) || null"
                  :disabled="savingField === 'social_media'"
                  @save="saveSocialMedia"
                />
              </div>
            </section>

            </template>

            <template v-else-if="wsTab === 'media'">
            <section class="node media-node">
              <header class="node-head">
                <b>媒体图片</b>
                <span class="node-note">头像与首页主舞台横幅 · 支持本地上传与历史切换</span>
              </header>
              <div class="node-body">
                <EntityMediaCard
                  kind="group"
                  :entity-id="data.group.id"
                  :avatar-path="data.group.avatar_path"
                  :banner-path="data.group.banner_path"
                  :locks="locks"
                  :search-query="data.group.name"
                  @updated="onMediaUpdated"
                />
              </div>
            </section>

            <section class="node">
              <header class="node-head">
                <b>图片文件夹</b>
                <span class="node-note">官方 / 粉丝 / 照片墙 · 绑 MT Photos 相册/文件夹</span>
              </header>
              <div class="node-body">
                <SettingsPhotoFolders owner-type="group" :owner-id="data.group.id" />
              </div>
            </section>
            <section class="node media-node">
              <header class="node-head">
                <b>首页主舞台</b>
                <span class="node-note">控制杂志/影院主题轮动池</span>
              </header>
              <div class="node-body">
                <label class="home-toggle">
                  <input
                    type="checkbox"
                    :checked="!!data.group.hide_from_home"
                    :disabled="savingField === 'hide_from_home'"
                    @change="toggleHideFromHome"
                  />
                  <span>不在首页轮动展示</span>
                </label>
              </div>
            </section>

            </template>
          </div>

        </div>
      </template>

    <SearchCompleteSheet
      :open="searchOpen"
      :uid="uid"
      :group-name="data?.group.name || ''"
      :locks="locks"
      :saved-urls="growthUrls.length ? growthUrls : sourceUrls"
      :group="searchGroupFields"
      :member-count="row?.members?.length ?? 0"
      :subunit-count="data?.sub_units?.length ?? 0"
      :company-count="row?.companies?.length ?? 0"
      :albums="searchAlbums"
      @close="onSearchClose"
      @applied="onSearchApplied"
      @update:urls="growthUrls = $event"
    />

    <teleport to="body">
      <transition name="drawer">
        <div v-if="drawerMember" class="drawer-scrim" @click.self="closeDrawer">
          <div class="drawer glass-strong">
            <header class="drawer-head">
              <div class="avatar">{{ initials(drawerMember.name) }}</div>
              <div class="dh-main">
                <h2>
                  {{ drawerMember.name }}
                  <span v-if="drawerMember.korean_name" class="h-sub">{{ drawerMember.korean_name }}</span>
                </h2>
                <div class="dh-meta">
                  <span class="st" :class="drawerMember.status === 'Former' ? 'st-off' : 'st-on'">{{
                    drawerMember.status === 'Former' ? '已退出' : '在籍'
                  }}</span>
                  <span class="meta-pill">加入 {{ drawerMember.join_date || '—' }}</span>
                  <span v-if="drawerMember.leave_date" class="meta-pill">退出 {{ drawerMember.leave_date }}</span>
                </div>
                <router-link
                  v-if="drawerMember.artist_uid"
                  :to="`/db/artists/${drawerMember.artist_uid}`"
                  class="dh-ws-link"
                  @click="$event.stopPropagation()"
                >
                  打开艺人工作台 →
                </router-link>
              </div>
              <button class="close-btn" type="button" @click="closeDrawer">关闭</button>
            </header>
            <div class="drawer-body">
              <div v-if="drawerError" class="form-error">{{ drawerError }}</div>
              <div class="d-sec">
                <div class="d-sec-title">成员关系</div>
                <div class="field-grid tight">
                  <label class="field-card field-card--date"
                    ><span class="f-label">加入日期</span
                    ><SaDatePicker
                      variant="bordered"
                      :model-value="drawerMember.join_date ?? ''"
                      :disabled="drawerSaving === 'membership'"
                      placeholder="加入日期"
                      @update:model-value="(v: string | null) => onDrawerDateValue('join_date', v)"
                  /></label>
                  <label class="field-card field-card--date"
                    ><span class="f-label">退出日期</span
                    ><SaDatePicker
                      variant="bordered"
                      :model-value="drawerMember.leave_date ?? ''"
                      :disabled="drawerSaving === 'membership'"
                      placeholder="退出日期"
                      @update:model-value="(v: string | null) => onDrawerDateValue('leave_date', v)"
                  /></label>
                  <label class="field-card"
                    ><span class="f-label">状态</span>
                    <select
                      class="f-input"
                      :value="drawerMember.status"
                      :disabled="drawerSaving === 'membership'"
                      @change="onDrawerStatus"
                    >
                      <option value="Active">在籍</option>
                      <option value="Former">已退出</option>
                      <option value="Inactive">暂停活动</option>
                    </select>
                  </label>
                  <label class="field-card"
                    ><span class="f-label">担当</span
                    ><input
                      class="f-input"
                      type="text"
                      :value="formatPositions(drawerMember.positions)"
                      placeholder="如 主唱 / 中心"
                      :disabled="drawerSaving === 'membership'"
                      @change="onDrawerPositions"
                  /></label>
                </div>
                <button class="del-btn big" type="button" :disabled="drawerSaving === 'remove'" @click="removeDrawerMembership">
                  删除此成员记录
                </button>
              </div>
              <div class="d-sec">
                <div class="d-sec-title">艺人档案</div>
                <div class="field-grid tight">
                  <label
                    v-for="f in ARTIST_FIELDS"
                    :key="f.key"
                    class="field-card"
                    :class="{ locked: artistLocks[f.key], 'field-card--date': f.kind === 'date' }"
                  >
                    <span class="f-label">{{ f.label }}<i v-if="artistLocks[f.key]" class="lock-mark">锁</i></span>
                    <SaDatePicker
                      v-if="f.kind === 'date'"
                      variant="bordered"
                      :model-value="artistValue(f.key)"
                      :disabled="drawerSaving === f.key"
                      @update:model-value="(v: string | null) => saveArtistField(f.key, v)"
                    />
                    <input
                      v-else
                      class="f-input"
                      type="text"
                      :value="artistValue(f.key)"
                      :disabled="drawerSaving === f.key"
                      @change="onArtistFieldChange(f, $event)"
                    />
                  </label>
                  <label class="field-card wide" :class="{ locked: artistLocks['description'] }">
                    <span class="f-label">描述</span>
                    <textarea
                      class="f-input"
                      rows="2"
                      :value="drawerMember.description ?? ''"
                      :disabled="drawerSaving === 'description'"
                      @change="onArtistFieldChange({ key: 'description', label: '描述', kind: 'textarea' }, $event)"
                    />
                  </label>
                </div>
              </div>
            </div>
          </div>
        </div>
      </transition>
    </teleport>
  </div>
</template>

<style scoped>
.gwsp {
  --dh-ok: #18a058;
  --dh-warn: #d97706;
  --dh-bad: #d03050;
}.mv-container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 14px 22px 64px;
}
.load-error {
  padding: 12px 16px;
  border-radius: 10px;
  background: rgba(208, 48, 80, 0.1);
  color: var(--dh-bad);
  font-size: 13px;
  margin-bottom: 16px;
}
.list-loading {
  padding: 60px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}
/* 空态提示条：不再整屏拦住关系网编辑（原 empty-hero 的「只给搜索一条路」已废弃） */
.sparse-hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px 16px;
  flex-wrap: wrap;
  padding: 14px 18px;
  margin-bottom: 12px;
}
.sparse-hint__text {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.sparse-hint__text b {
  font-size: 14px;
}
.sparse-hint__text span {
  font-size: 12.5px;
  color: var(--sa-text-secondary);
}
.board {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}
.checkup-node .node-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.board-main {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}
.rail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  position: sticky;
  top: 60px;
}
.drawer-scrim {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(5, 5, 15, 0.45);
  backdrop-filter: blur(3px);
  display: flex;
  justify-content: flex-end;
}
.drawer {
  width: min(560px, 94vw);
  height: 100vh;
  overflow-y: auto;
  padding: 18px 22px 40px;
  border-left: 1px solid var(--sa-border);
}
.drawer-head {
  display: flex;
  align-items: center;
  gap: 13px;
  padding-bottom: 13px;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.drawer-head .avatar {
  width: 46px;
  height: 46px;
  border-radius: 14px;
  background: var(--sa-accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 15px;
}
.dh-main {
  flex: 1;
  min-width: 0;
}
.dh-main h2 {
  margin: 0;
  font-size: 18px;
}
.dh-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 5px;
  align-items: center;
}
.st {
  font-size: 10.5px;
  font-weight: 800;
  border-radius: 6px;
  padding: 2px 8px;
}
.st-on {
  background: rgba(24, 160, 88, 0.12);
  color: var(--dh-ok);
}
.st-off {
  background: rgba(217, 119, 6, 0.12);
  color: var(--dh-warn);
}
.close-btn {
  border: none;
  background: none;
  color: var(--sa-text-tertiary);
  font-size: 13px;
  cursor: pointer;
}
.dh-ws-link {
  display: inline-block;
  margin-top: 6px;
  font-size: 11.5px;
  font-weight: 700;
  color: var(--sa-accent);
  text-decoration: none;
}
.drawer-body {
  padding-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.d-sec-title {
  font-size: 13px;
  font-weight: 800;
  margin-bottom: 8px;
}
.del-btn {
  border: 1px solid rgba(208, 48, 80, 0.3);
  background: none;
  color: var(--dh-bad);
  border-radius: 9px;
  font-size: 12px;
  font-weight: 700;
  padding: 6px 14px;
  cursor: pointer;
  margin-top: 8px;
}
.drawer-enter-active,
.drawer-leave-active {
  transition: transform 0.22s ease, opacity 0.22s ease;
}
.drawer-enter-from,
.drawer-leave-to {
  transform: translateX(40px);
  opacity: 0;
}
@media (max-width: 960px) {
  .board {
    grid-template-columns: 1fr;
  }
  .rail-unused {
    position: static;
  }
}
.desc-suggest { display: flex; align-items: center; gap: 8px; margin-top: 5px; flex-wrap: wrap; }
.desc-suggest-btn { border: 1px solid var(--sa-border); background: none; color: var(--sa-text-secondary); border-radius: 7px; font-size: 11px; font-weight: 600; padding: 2px 9px; cursor: pointer; font-family: inherit; }
.desc-suggest-btn:hover:not(:disabled) { color: var(--sa-accent); border-color: var(--sa-accent); }
.desc-suggest-btn:disabled { opacity: .5; cursor: default; }
.desc-suggest-src { font-size: 10.5px; color: var(--sa-text-tertiary); }



/* 成员/专辑节点底部含日期选择器：允许日历弹层溢出，不被 .node 的 overflow:hidden 裁掉 */
.node.node--popout { overflow: visible; }

@media (max-width: 768px) {
  .ws-tab-row { margin-bottom: 10px; }
  .board { gap: 9px; }
  .drawer { width: 100vw; }
}
</style>
<style scoped src="../../views/db-workspace.css"></style>
