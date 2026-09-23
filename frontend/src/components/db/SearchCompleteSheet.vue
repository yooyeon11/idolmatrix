<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { dbViewsApi } from '@/api/dbViews'
import type { EntityLockMap } from '@/api/entityLocks'

const IDENTITY_FIELDS = [
  { key: 'debut_date', label: '出道日期' },
  { key: 'korean_name', label: '韩文名' },
  { key: 'english_name', label: '英文名' },
  { key: 'chinese_name', label: '中文名' },
  { key: 'description', label: '描述' },
  { key: 'tagline', label: '一句话简介' },
] as const

type TargetKey = 'identity' | 'members' | 'subunits' | 'company' | 'albums'

const props = defineProps<{
  open: boolean
  uid: string
  groupName: string
  locks: EntityLockMap
  savedUrls?: string[]
  group: Record<string, string | null | undefined> | null
  memberCount: number
  subunitCount: number
  companyCount: number
  albums: { id: number; name: string; track_count: number }[]
}>()

const emit = defineEmits<{
  close: []
  applied: []
  'update:urls': [urls: string[]]
}>()

const searchQ = ref('')
const srcUrlsInput = ref('')
const searching = ref(false)
const candidates = ref<{ source_type: string; title: string; snippet: string; url: string }[]>([])
const preview = ref<Awaited<ReturnType<typeof dbViewsApi.previewSource>> | null>(null)
const previewing = ref(false)
const previewError = ref('')
const applying = ref(false)
const applyMsg = ref('')
const checked = ref<Record<TargetKey, boolean>>({
  identity: false,
  members: false,
  subunits: false,
  company: false,
  albums: false,
})
const selectedFields = ref<Record<string, boolean>>({})
const selectedMembers = ref<Record<string, boolean>>({})
const selectedSubunits = ref<Record<string, boolean>>({})
const selectedCompanies = ref<Record<string, boolean>>({})
const selectedAlbums = ref<Record<string, boolean>>({})
/** 空专辑/需补曲：用户选定的外部碟 */
const pickedDisc = ref<Record<number, string>>({})
const trackPreview = ref<
  Record<number, { loading?: boolean; error?: string; tracks: { name?: string; track_number?: number }[] }>
>({})
const confirmFill = ref<Record<number, boolean>>({})

const identityUnlocked = computed(() =>
  IDENTITY_FIELDS.filter((f) => !props.locks[f.key]),
)

const availableTargets = computed(() => {
  const items: { key: TargetKey; label: string; hint: string; empty: boolean }[] = []
  if (identityUnlocked.value.length) {
    const empty = identityUnlocked.value.some((f) => !String(props.group?.[f.key] || '').trim())
    items.push({
      key: 'identity',
      label: '身份字段',
      hint: identityUnlocked.value.map((f) => f.label).join('、'),
      empty,
    })
  }
  items.push({
    key: 'members',
    label: '成员',
    hint: props.memberCount ? `库内 ${props.memberCount} 人` : '库内尚无成员',
    empty: props.memberCount === 0,
  })
  items.push({
    key: 'subunits',
    label: '小分队',
    hint: props.subunitCount ? `库内 ${props.subunitCount} 个` : '库内尚无小分队',
    empty: props.subunitCount === 0,
  })
  items.push({
    key: 'company',
    label: '公司',
    hint: props.companyCount ? `库内 ${props.companyCount} 家` : '库内尚未挂公司',
    empty: props.companyCount === 0,
  })
  const emptyAlbums = props.albums.filter((a) => a.track_count <= 0).length
  items.push({
    key: 'albums',
    label: '专辑',
    hint: emptyAlbums
      ? `${props.albums.length} 张，其中 ${emptyAlbums} 张待补曲`
      : props.albums.length
        ? `${props.albums.length} 张`
        : '库内尚无专辑',
    empty: props.albums.length === 0 || emptyAlbums > 0,
  })
  return items
})

function resetChecks() {
  const next: Record<TargetKey, boolean> = {
    identity: false,
    members: false,
    subunits: false,
    company: false,
    albums: false,
  }
  for (const t of availableTargets.value) {
    next[t.key] = t.empty
  }
  checked.value = next
}

function hydrate() {
  srcUrlsInput.value = (props.savedUrls || []).join('\n')
  searchQ.value = props.groupName || ''
  resetChecks()
  preview.value = null
  previewError.value = ''
  applyMsg.value = ''
  selectedFields.value = {}
  selectedMembers.value = {}
  selectedSubunits.value = {}
  selectedCompanies.value = {}
  selectedAlbums.value = {}
  pickedDisc.value = {}
  trackPreview.value = {}
  confirmFill.value = {}
  candidates.value = []
}

watch(
  () => props.open,
  (v) => {
    if (v) hydrate()
  },
)

watch(
  () => props.uid,
  () => {
    if (props.open) hydrate()
  },
)

function parseUrls(): string[] {
  return srcUrlsInput.value
    .split(/[\n,\s]+/)
    .map((u) => u.trim())
    .filter((u) => u.startsWith('http'))
}

function persistUrls() {
  emit('update:urls', parseUrls())
}

const selectedTargets = computed(() =>
  (Object.keys(checked.value) as TargetKey[]).filter((k) => checked.value[k]),
)

const needsUrls = computed(() =>
  selectedTargets.value.some(
    (k) => k === 'identity' || k === 'members' || k === 'subunits' || k === 'company',
  ),
)

async function doSearch() {
  if (!searchQ.value.trim()) return
  searching.value = true
  previewError.value = ''
  try {
    candidates.value = (await dbViewsApi.searchSourceCandidates(props.uid, searchQ.value.trim())).items
  } catch (e) {
    previewError.value = e instanceof Error ? e.message : '搜索失败'
  } finally {
    searching.value = false
  }
}

function addCandidateUrl(url: string) {
  const lines = new Set(parseUrls())
  lines.add(url)
  srcUrlsInput.value = [...lines].join('\n')
  persistUrls()
}

function fieldKey(item: { field: string; proposed: string; conflict: boolean }) {
  return item.conflict ? `${item.field}::${item.proposed}` : item.field
}

function albumKey(a: { name: string; album_id?: number | null; external_id?: string | null }) {
  return a.album_id != null ? `id:${a.album_id}` : `${a.name}::${a.external_id || ''}`
}

function toggleConflict(item: { field: string; proposed: string; conflict: boolean }, on: boolean) {
  if (!item.conflict || !on) return
  for (const other of preview.value?.items ?? []) {
    if (other.field === item.field && other.proposed !== item.proposed) {
      selectedFields.value[fieldKey(other)] = false
    }
  }
}

async function doPreview() {
  const urls = parseUrls()
  persistUrls()
  if (!selectedTargets.value.length) {
    previewError.value = '请先勾选要补全的项'
    return
  }
  if (needsUrls.value && !urls.length) {
    previewError.value = '身份 / 成员 / 小分队需要百科链接，请先搜索加入或粘贴'
    return
  }
  previewing.value = true
  previewError.value = ''
  applyMsg.value = ''
  try {
    preview.value = await dbViewsApi.previewSource(props.uid, urls, undefined, selectedTargets.value)
    const fields: Record<string, boolean> = {}
    for (const item of preview.value.items) {
      if (props.locks[item.field]) continue
      fields[fieldKey(item)] = item.is_new && !item.conflict
    }
    selectedFields.value = fields
    const members: Record<string, boolean> = {}
    for (const m of preview.value.member_proposals) {
      members[m.name] = m.action !== 'exists' && !m.possible_duplicate_of?.length
    }
    selectedMembers.value = members
    const subunits: Record<string, boolean> = {}
    for (const u of preview.value.subunit_proposals) {
      subunits[u.name] = u.action !== 'exists'
    }
    selectedSubunits.value = subunits
    const companies: Record<string, boolean> = {}
    for (const c of preview.value.company_proposals || []) {
      companies[c.name] = c.action !== 'exists'
    }
    selectedCompanies.value = companies
    const albums: Record<string, boolean> = {}
    for (const a of preview.value.album_proposals) {
      albums[albumKey(a)] = a.action === 'create' || a.action === 'update'
    }
    selectedAlbums.value = albums
    pickedDisc.value = {}
    trackPreview.value = {}
    confirmFill.value = {}
  } catch (e) {
    preview.value = null
    previewError.value = e instanceof Error ? e.message : '搜索失败'
  } finally {
    previewing.value = false
  }
}

async function pickDisc(albumId: number, externalId: string) {
  pickedDisc.value = { ...pickedDisc.value, [albumId]: externalId }
  confirmFill.value = { ...confirmFill.value, [albumId]: false }
  trackPreview.value = { ...trackPreview.value, [albumId]: { loading: true, tracks: [] } }
  try {
    const r = await dbViewsApi.previewTracklist(props.uid, albumId, externalId)
    trackPreview.value = {
      ...trackPreview.value,
      [albumId]: { loading: false, tracks: r.tracks_preview || [] },
    }
  } catch (e) {
    trackPreview.value = {
      ...trackPreview.value,
      [albumId]: {
        loading: false,
        error: e instanceof Error ? e.message : '曲目预览失败',
        tracks: [],
      },
    }
  }
}

const visibleFields = computed(() =>
  (preview.value?.items ?? []).filter((i) => !props.locks[i.field]),
)

const createOrUpdateAlbums = computed(() =>
  (preview.value?.album_proposals ?? []).filter((a) => a.action === 'create' || a.action === 'update'),
)

const fillTrackAlbums = computed(() =>
  (preview.value?.album_proposals ?? []).filter(
    (a) => a.album_id != null && (a.action === 'fill_tracks' || a.needs_tracks),
  ),
)

const selectedCount = computed(() => {
  const p = preview.value
  if (!p) return 0
  let n =
    Object.values(selectedFields.value).filter(Boolean).length +
    Object.entries(selectedMembers.value).filter(([name, v]) => v && p.member_proposals.find((m) => m.name === name)?.action !== 'exists').length +
    Object.entries(selectedSubunits.value).filter(([name, v]) => v && p.subunit_proposals.find((u) => u.name === name)?.action !== 'exists').length +
    Object.entries(selectedCompanies.value).filter(([name, v]) => v && (p.company_proposals || []).find((c) => c.name === name)?.action !== 'exists').length
  for (const a of createOrUpdateAlbums.value) {
    if (selectedAlbums.value[albumKey(a)]) n += 1
  }
  for (const a of fillTrackAlbums.value) {
    if (a.album_id != null && confirmFill.value[a.album_id] && pickedDisc.value[a.album_id]) n += 1
  }
  return n
})

function summarizeApplied(a: {
  fields: string[]
  members_created: number
  members_linked: number
  members_skipped: number
  albums_created: number
  albums_updated?: number
  albums_skipped: number
  songs_created: number
  tracks_created: number
  subunits_created: number
  companies_created?: number
  companies_linked?: number
  artist_fields_filled: number
  tracklist_failed: number
}) {
  const parts: string[] = []
  if (a.fields.length) parts.push(`字段 ${a.fields.length}`)
  if (a.members_created) parts.push(`新建成员 ${a.members_created}`)
  if (a.members_linked) parts.push(`挂接成员 ${a.members_linked}`)
  if (a.companies_created) parts.push(`新建公司 ${a.companies_created}`)
  if (a.companies_linked) parts.push(`挂接公司 ${a.companies_linked}`)
  if (a.albums_created) parts.push(`新专辑 ${a.albums_created}`)
  if (a.albums_updated) parts.push(`更新专辑 ${a.albums_updated}`)
  if (a.songs_created) parts.push(`新歌曲 ${a.songs_created}`)
  if (a.tracks_created) parts.push(`曲目 ${a.tracks_created}`)
  if (a.subunits_created) parts.push(`小分队 ${a.subunits_created}`)
  if (a.artist_fields_filled) parts.push(`档案补全 ${a.artist_fields_filled}`)
  if (a.members_skipped) parts.push(`跳过 ${a.members_skipped}`)
  if (a.tracklist_failed) parts.push(`曲目表失败 ${a.tracklist_failed}`)
  return parts.length ? parts.join(' · ') : '已保存'
}

async function applySelected() {
  const p = preview.value
  if (!p) return
  applying.value = true
  applyMsg.value = ''
  const sourceUrls = [...new Set([...p.sources.map((s) => s.url), ...parseUrls()])]
  const parts: string[] = []
  try {
    const fields: Record<string, string> = {}
    for (const item of visibleFields.value) {
      if (selectedFields.value[fieldKey(item)]) fields[item.field] = item.proposed
    }
    const members = p.member_proposals.filter(
      (m) => m.action !== 'exists' && selectedMembers.value[m.name],
    )
    const subunits = p.subunit_proposals.filter(
      (u) => u.action !== 'exists' && selectedSubunits.value[u.name],
    )
    const companies = (p.company_proposals || []).filter(
      (c) => c.action !== 'exists' && selectedCompanies.value[c.name],
    )
    const albumsMeta = createOrUpdateAlbums.value.filter((a) => selectedAlbums.value[albumKey(a)])
    const fillAlbums = fillTrackAlbums.value
      .filter((a) => a.album_id != null && confirmFill.value[a.album_id!] && pickedDisc.value[a.album_id!])
      .map((a) => ({
        ...a,
        action: 'fill_tracks',
        external_id: pickedDisc.value[a.album_id!],
      }))

    if (!Object.keys(fields).length && !members.length && !subunits.length && !companies.length && !albumsMeta.length && !fillAlbums.length) {
      applyMsg.value = '没有勾选任何提案'
      return
    }

    const run = async (
      step: string,
      payload: Partial<{ fields: Record<string, string>; members: typeof members; subunits: typeof subunits; companies: typeof companies; albums: Record<string, unknown>[] }>,
    ) => {
      const res = await dbViewsApi.applySource(props.uid, {
        fields: payload.fields || {},
        members: (payload.members || []) as Record<string, unknown>[],
        subunits: (payload.subunits || []) as Record<string, unknown>[],
        companies: (payload.companies || []) as Record<string, unknown>[],
        albums: payload.albums || [],
        source_urls: sourceUrls,
        step,
      })
      parts.push(summarizeApplied(res.applied))
    }

    if (Object.keys(fields).length) await run('identity', { fields })
    if (members.length) await run('members', { members })
    if (subunits.length) await run('subunits', { subunits })
    if (companies.length) await run('company', { companies })
    if (albumsMeta.length) await run('albums', { albums: albumsMeta as Record<string, unknown>[] })
    if (fillAlbums.length) await run('tracks', { albums: fillAlbums as Record<string, unknown>[] })

    applyMsg.value = parts.join('；')
    preview.value = null
    emit('applied')
  } catch (e) {
    applyMsg.value = e instanceof Error ? e.message : '写入失败'
  } finally {
    applying.value = false
  }
}

function sourceBadge(t: string) {
  if (t === 'fandom') return 'Fandom'
  if (t === 'wikidata') return 'Wikidata'
  if (t === 'wikipedia') return 'Wikipedia'
  if (t === 'baidu') return '百度'
  return t
}

function requestClose() {
  persistUrls()
  emit('close')
}

const emptyHint = computed(() => {
  if (!preview.value) return ''
  const p = preview.value
  if (checked.value.identity && !p.items.length && !checked.value.members && !checked.value.albums && !checked.value.subunits) {
    return '身份字段没有可写入的变更（可能已同步或均已锁定）'
  }
  return ''
})
</script>

<template>
  <teleport to="body">
    <transition name="gs">
      <div v-if="open" class="gs-scrim" @click.self="requestClose">
        <aside class="gs-panel" role="dialog" aria-label="搜索补全">
          <header class="gs-head">
            <div>
              <div class="gs-kicker">搜索补全</div>
              <h2>{{ groupName }}</h2>
            </div>
            <button class="gs-x" type="button" @click="requestClose">关闭</button>
          </header>

          <div class="gs-body">
            <div class="gs-block">
              <div class="gs-label">要补哪些（已锁定的不出现）</div>
              <p v-if="!availableTargets.length" class="gs-note">可写项都已锁定。</p>
              <label v-for="t in availableTargets" :key="t.key" class="gs-check">
                <input v-model="checked[t.key]" type="checkbox" />
                <span>
                  <b>{{ t.label }}</b>
                  <i>{{ t.hint }}</i>
                </span>
              </label>
            </div>

            <div class="gs-block">
              <div class="gs-label">来源</div>
              <div class="gs-row">
                <input
                  v-model="searchQ"
                  class="gs-input"
                  type="text"
                  placeholder="搜索 Fandom / Wikidata…"
                  @keyup.enter="doSearch"
                />
                <button class="gs-btn" type="button" :disabled="searching || !searchQ.trim()" @click="doSearch">
                  {{ searching ? '搜索中…' : '搜索' }}
                </button>
              </div>
              <textarea
                v-model="srcUrlsInput"
                class="gs-input gs-ta"
                rows="2"
                placeholder="粘贴维基百科 / Fandom / Wikidata / 百度百科链接（每行一个）"
                @change="persistUrls"
              />
              <p v-if="!needsUrls" class="gs-note">只补专辑时可不贴百科链接，将按组合名检索碟志。</p>
              <div v-if="candidates.length" class="gs-cands">
                <button
                  v-for="c in candidates"
                  :key="c.url"
                  type="button"
                  class="gs-cand"
                  @click="addCandidateUrl(c.url)"
                >
                  <span class="gs-badge" :class="c.source_type">{{ sourceBadge(c.source_type) }}</span>
                  <span class="gs-cand-t">{{ c.title }}</span>
                  <span class="gs-cand-a">加入</span>
                </button>
              </div>
              <div class="gs-row end">
                <button
                  class="gs-btn primary"
                  type="button"
                  :disabled="previewing || !selectedTargets.length"
                  @click="doPreview"
                >
                  {{ previewing ? '搜索中…' : '搜索选中项' }}
                </button>
              </div>
            </div>

            <div v-if="previewError" class="gs-err">{{ previewError }}</div>
            <div v-if="applyMsg" class="gs-ok">{{ applyMsg }}</div>
            <div v-if="emptyHint" class="gs-note">{{ emptyHint }}</div>

            <div v-if="preview" class="gs-preview">
              <template v-if="visibleFields.length">
                <div class="gs-label">身份字段</div>
                <table class="gs-table">
                  <thead>
                    <tr>
                      <th>采纳</th>
                      <th>字段</th>
                      <th>当前</th>
                      <th>提案</th>
                      <th>来源</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in visibleFields" :key="fieldKey(item)" :class="{ conflict: item.conflict }">
                      <td>
                        <input
                          v-model="selectedFields[fieldKey(item)]"
                          type="checkbox"
                          @change="toggleConflict(item, ($event.target as HTMLInputElement).checked)"
                        />
                      </td>
                      <td>{{ item.label }}</td>
                      <td class="muted">{{ item.current || '（空）' }}</td>
                      <td class="prop">{{ item.proposed }}</td>
                      <td class="muted">{{ item.sources.join(' + ') }}</td>
                    </tr>
                  </tbody>
                </table>
              </template>

              <template v-if="preview.member_proposals.length">
                <div class="gs-label">成员</div>
                <label
                  v-for="m in preview.member_proposals"
                  :key="m.name"
                  class="gs-grow"
                  :class="{ dim: m.action === 'exists' }"
                >
                  <input
                    v-model="selectedMembers[m.name]"
                    type="checkbox"
                    :disabled="m.action === 'exists'"
                  />
                  <span class="gs-badge act" :class="m.action">{{
                    m.action === 'create' ? '新建' : m.action === 'link' ? '挂接' : '已在库'
                  }}</span>
                  <b>{{ m.name }}</b>
                  <span v-if="m.korean_name" class="muted">{{ m.korean_name }}</span>
                  <span v-if="m.possible_duplicate_of?.length" class="warn">疑似重复</span>
                </label>
              </template>

              <template v-if="preview.subunit_proposals.length">
                <div class="gs-label">小分队</div>
                <label
                  v-for="u in preview.subunit_proposals"
                  :key="u.name"
                  class="gs-grow"
                  :class="{ dim: u.action === 'exists' }"
                >
                  <input
                    v-model="selectedSubunits[u.name]"
                    type="checkbox"
                    :disabled="u.action === 'exists'"
                  />
                  <span class="gs-badge act" :class="u.action">{{ u.action === 'create' ? '新建' : '已在库' }}</span>
                  <b>{{ u.name }}</b>
                </label>
              </template>

              <template v-if="(preview.company_proposals || []).length">
                <div class="gs-label">公司</div>
                <label
                  v-for="c in preview.company_proposals"
                  :key="c.name"
                  class="gs-grow"
                  :class="{ dim: c.action === 'exists' }"
                >
                  <input
                    v-model="selectedCompanies[c.name]"
                    type="checkbox"
                    :disabled="c.action === 'exists'"
                  />
                  <span class="gs-badge act" :class="c.action">{{
                    c.action === 'create' ? '新建' : c.action === 'link' ? '挂接' : '已挂'
                  }}</span>
                  <b>{{ c.name }}</b>
                  <span v-if="c.role" class="muted">{{ c.role }}</span>
                </label>
              </template>

              <template v-if="createOrUpdateAlbums.length">
                <div class="gs-label">专辑（搜到的碟）</div>
                <label
                  v-for="a in createOrUpdateAlbums"
                  :key="albumKey(a)"
                  class="gs-grow"
                  :class="{ dim: a.action === 'exists' }"
                >
                  <input v-model="selectedAlbums[albumKey(a)]" type="checkbox" />
                  <span class="gs-badge act" :class="a.action">{{ a.action === 'create' ? '新建' : '更新' }}</span>
                  <b>{{ a.name }}</b>
                  <span class="muted">{{ a.release_date || a.year || '?' }}</span>
                </label>
              </template>

              <template v-if="fillTrackAlbums.length">
                <div class="gs-label">补曲目 · 先选碟</div>
                <p class="gs-note">点中外部碟后才会拉曲目预览；确认后才写入。</p>
                <div v-for="a in fillTrackAlbums" :key="a.album_id!" class="gs-disc">
                  <div class="gs-disc-name">库内「{{ a.name }}」<span class="muted"> 无曲目</span></div>
                  <div v-if="!(a.candidates || []).length" class="muted">没有同名候选，请改关键词后重新搜索选中项。</div>
                  <button
                    v-for="c in a.candidates || []"
                    :key="String(c.external_id)"
                    type="button"
                    class="gs-cand"
                    :class="{ on: a.album_id != null && pickedDisc[a.album_id] === c.external_id }"
                    @click="a.album_id != null && c.external_id && pickDisc(a.album_id, c.external_id)"
                  >
                    <span class="gs-cand-t">{{ c.name }}</span>
                    <span class="muted">{{ [c.source, c.year, c.track_count != null ? `${c.track_count} 曲` : ''].filter(Boolean).join(' · ') }}</span>
                  </button>
                  <div v-if="a.album_id != null && trackPreview[a.album_id]?.loading" class="muted">曲目预览中…</div>
                  <div v-else-if="a.album_id != null && trackPreview[a.album_id]?.error" class="gs-err">
                    {{ trackPreview[a.album_id].error }}
                  </div>
                  <div v-else-if="a.album_id != null && trackPreview[a.album_id]?.tracks.length" class="gs-tracks">
                    <span v-for="(t, i) in trackPreview[a.album_id].tracks.slice(0, 10)" :key="i">
                      {{ t.track_number }}. {{ t.name }}
                    </span>
                    <span v-if="trackPreview[a.album_id].tracks.length > 10" class="muted">
                      …共 {{ trackPreview[a.album_id].tracks.length }} 首
                    </span>
                    <label class="gs-check tight">
                      <input v-model="confirmFill[a.album_id]" type="checkbox" />
                      <span>确认写入这张碟的曲目</span>
                    </label>
                  </div>
                </div>
              </template>

              <div v-for="(err, i) in preview.errors" :key="i" class="gs-err">{{ err }}</div>

              <div class="gs-foot">
                <button
                  type="button"
                  class="gs-btn primary"
                  :disabled="applying || !selectedCount"
                  @click="applySelected"
                >
                  {{ applying ? '写入中…' : `写入选中项（${selectedCount}）` }}
                </button>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </transition>
  </teleport>
</template>

<style scoped>
.gs-scrim {
  position: fixed;
  inset: 0;
  z-index: 220;
  background: rgba(6, 6, 14, 0.48);
  backdrop-filter: blur(4px);
  display: flex;
  justify-content: flex-end;
}
.gs-panel {
  width: min(560px, 96vw);
  height: 100vh;
  background: var(--sa-elevated, #14141f);
  border-left: 1px solid var(--sa-border, rgba(255, 255, 255, 0.1));
  display: flex;
  flex-direction: column;
  color: var(--sa-text-primary, #eee);
}
.gs-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 18px 20px 12px;
  border-bottom: 1px solid var(--sa-border-subtle, rgba(255, 255, 255, 0.08));
}
.gs-kicker {
  font-size: 11px;
  letter-spacing: 0.12em;
  color: var(--sa-text-tertiary, #888);
  text-transform: uppercase;
  margin-bottom: 4px;
}
.gs-head h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
}
.gs-x {
  border: none;
  background: none;
  color: var(--sa-text-tertiary);
  cursor: pointer;
  font-size: 13px;
}
.gs-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 18px 24px;
}
.gs-block {
  margin-bottom: 16px;
}
.gs-label {
  font-size: 12px;
  font-weight: 700;
  margin: 12px 0 8px;
  color: var(--sa-text-secondary);
}
.gs-check {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 8px 0;
  cursor: pointer;
  font-size: 13px;
}
.gs-check.tight {
  margin-top: 8px;
}
.gs-check b {
  display: block;
}
.gs-check i {
  font-style: normal;
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.gs-row {
  display: flex;
  gap: 8px;
}
.gs-row.end {
  justify-content: flex-end;
  margin-top: 8px;
}
.gs-input {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 10px;
  background: var(--sa-bg, #0e0e16);
  color: inherit;
  font: inherit;
  padding: 8px 10px;
}
.gs-ta {
  width: 100%;
  margin-top: 8px;
  resize: vertical;
}
.gs-btn {
  border: 1px solid var(--sa-border);
  background: var(--sa-subtle, rgba(255, 255, 255, 0.06));
  color: inherit;
  border-radius: 10px;
  padding: 8px 12px;
  font: inherit;
  cursor: pointer;
}
.gs-btn.primary {
  background: var(--sa-accent, #7a5cf0);
  border-color: transparent;
  color: #fff;
  font-weight: 700;
}
.gs-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.gs-note {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  line-height: 1.5;
}
.gs-cands {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}
.gs-cand {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  align-items: baseline;
  text-align: left;
  border: 1px solid var(--sa-border-subtle);
  background: transparent;
  color: inherit;
  border-radius: 10px;
  padding: 8px 10px;
  cursor: pointer;
  font: inherit;
}
.gs-cand.on {
  border-color: var(--sa-accent, #7a5cf0);
  background: rgba(122, 92, 240, 0.1);
}
.gs-cand-t {
  font-weight: 650;
}
.gs-cand-a {
  margin-left: auto;
  font-size: 12px;
  color: var(--sa-accent, #7a5cf0);
}
.gs-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 6px;
  background: var(--sa-subtle);
}
.gs-badge.act.create {
  color: #18a058;
}
.gs-badge.act.update,
.gs-badge.act.link {
  color: var(--sa-accent, #7a5cf0);
}
.gs-err {
  color: #d03050;
  font-size: 12.5px;
  margin: 8px 0;
}
.gs-ok {
  color: #18a058;
  font-size: 12.5px;
  margin: 8px 0;
}
.gs-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.gs-table th,
.gs-table td {
  text-align: left;
  padding: 6px 4px;
  border-bottom: 1px solid var(--sa-border-subtle);
  vertical-align: top;
}
.gs-table .muted,
.muted {
  color: var(--sa-text-tertiary);
  font-size: 12px;
}
.gs-table .prop {
  font-weight: 650;
}
.gs-table tr.conflict td {
  background: rgba(217, 119, 6, 0.08);
}
.gs-grow {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 8px;
  align-items: center;
  padding: 8px 0;
  cursor: pointer;
}
.gs-grow.dim {
  opacity: 0.55;
}
.warn {
  color: #d97706;
  font-size: 12px;
}
.gs-disc {
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.gs-disc-name {
  font-weight: 700;
  margin-bottom: 8px;
}
.gs-tracks {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  margin-top: 8px;
}
.gs-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.gs-enter-active,
.gs-leave-active {
  transition: opacity 0.15s;
}
.gs-enter-from,
.gs-leave-to {
  opacity: 0;
}
</style>
