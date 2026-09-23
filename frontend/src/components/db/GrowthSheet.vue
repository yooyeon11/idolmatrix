<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { dbViewsApi } from '@/api/dbViews'
import type { EntityLockMap } from '@/api/entityLocks'
import { loadGrowthDraft, saveGrowthDraft } from '@/utils/growthDraft'
import { formatPosition } from '@/utils/positions'

const props = defineProps<{
  open: boolean
  uid: string
  groupName: string
  locks: EntityLockMap
  initialStep?: number
  savedUrls?: string[]
  completedSteps?: number[]
}>()

const emit = defineEmits<{
  close: []
  applied: []
  'update:step': [step: number]
  'update:urls': [urls: string[]]
  'update:completedSteps': [steps: number[]]
}>()

const STEPS = [
  { id: 1, key: 'identity', title: '身份与公司', hint: '出道日、多语言名、描述；公司关系后续支持', skippable: false },
  { id: 2, key: 'subunits', title: '小分队', hint: '无则跳过', skippable: true },
  { id: 3, key: 'members', title: '成员轨迹', hint: '现任 / 历史成员', skippable: false },
  { id: 4, key: 'member_details', title: '成员详情', hint: '生日、出生地等；可跳过', skippable: true },
  { id: 5, key: 'albums', title: '专辑', hint: '碟志列表（先建碟）', skippable: false },
  { id: 6, key: 'tracks', title: '歌曲 / 曲目', hint: '为专辑补曲目表', skippable: false },
] as const

const step = ref(props.initialStep && props.initialStep >= 1 && props.initialStep <= 6 ? props.initialStep : 1)
const searchQ = ref(props.groupName || '')
const srcUrlsInput = ref((props.savedUrls || []).join('\n'))
const searching = ref(false)
const candidates = ref<{ source_type: string; title: string; snippet: string; url: string }[]>([])
const preview = ref<Awaited<ReturnType<typeof dbViewsApi.previewSource>> | null>(null)
const previewing = ref(false)
const previewError = ref('')
const applying = ref(false)
const applyMsg = ref('')
const selected = ref<Record<string, boolean>>({})
const selectedMembers = ref<Record<string, boolean>>({})
const selectedSubunits = ref<Record<string, boolean>>({})
const selectedAlbums = ref<Record<string, boolean>>({})
const completedSteps = ref<number[]>([...(props.completedSteps || [])])
/** 离开确认：有未写入提案时暂存目标步骤 */
const leaveConfirm = ref<{ target: number | 'close' } | null>(null)

function persistDraft() {
  const urls = parseUrls()
  emit('update:urls', urls)
  emit('update:step', step.value)
  emit('update:completedSteps', [...completedSteps.value])
  saveGrowthDraft(props.uid, {
    step: step.value,
    urls,
    completedSteps: completedSteps.value,
  })
}

function hydrateFromPropsOrDraft() {
  const draft = loadGrowthDraft(props.uid)
  const nextStep =
    props.initialStep && props.initialStep >= 1 && props.initialStep <= 6
      ? props.initialStep
      : draft?.step && draft.step >= 1 && draft.step <= 6
        ? draft.step
        : step.value
  step.value = nextStep
  const urls =
    props.savedUrls && props.savedUrls.length
      ? props.savedUrls
      : draft?.urls?.length
        ? draft.urls
        : parseUrls()
  srcUrlsInput.value = urls.join('\n')
  const fromProp = props.completedSteps?.length ? props.completedSteps : null
  completedSteps.value = [...(fromProp || draft?.completedSteps || [])]
  searchQ.value = searchQ.value || props.groupName || ''
  persistDraft()
}

watch(
  () => props.open,
  (v) => {
    if (v) {
      hydrateFromPropsOrDraft()
      leaveConfirm.value = null
    } else {
      persistDraft()
    }
  },
)

watch(
  () => props.completedSteps,
  (v) => {
    if (!v) return
    const a = [...v].sort().join(',')
    const b = [...completedSteps.value].sort().join(',')
    if (a !== b) completedSteps.value = [...v]
  },
)

watch(step, (s) => {
  emit('update:step', s)
  preview.value = null
  selected.value = {}
  selectedMembers.value = {}
  selectedSubunits.value = {}
  selectedAlbums.value = {}
  applyMsg.value = ''
  previewError.value = ''
  persistDraft()
})

const stepMeta = computed(() => STEPS[step.value - 1])

function parseUrls(): string[] {
  return srcUrlsInput.value
    .split(/[\n,\s]+/)
    .map((u) => u.trim())
    .filter((u) => u.startsWith('http'))
}

function persistUrls() {
  persistDraft()
}

function clearPreviewState() {
  preview.value = null
  selected.value = {}
  selectedMembers.value = {}
  selectedSubunits.value = {}
  selectedAlbums.value = {}
}

function goToStep(target: number) {
  if (target < 1 || target > 6 || target === step.value) return
  clearPreviewState()
  step.value = target
}

function requestStepChange(target: number) {
  if (target < 1 || target > 6 || target === step.value) return
  if (hasUnappliedPreview.value) {
    leaveConfirm.value = { target }
    return
  }
  goToStep(target)
}

function requestClose() {
  if (hasUnappliedPreview.value) {
    leaveConfirm.value = { target: 'close' }
    return
  }
  persistDraft()
  emit('close')
}

async function confirmLeaveDiscard() {
  const pending = leaveConfirm.value
  leaveConfirm.value = null
  if (!pending) return
  clearPreviewState()
  if (pending.target === 'close') {
    persistDraft()
    emit('close')
    return
  }
  goToStep(pending.target)
}

async function confirmLeaveApplyFirst() {
  const pending = leaveConfirm.value
  if (!pending) return
  const ok = await applySelected()
  if (!ok) return
  leaveConfirm.value = null
  emit('applied')
  if (pending.target === 'close') {
    persistDraft()
    emit('close')
    return
  }
  goToStep(pending.target)
}

function cancelLeaveConfirm() {
  leaveConfirm.value = null
}

function isStepCompleted(id: number) {
  return completedSteps.value.includes(id)
}

function markStepCompleted(n: number) {
  if (!completedSteps.value.includes(n)) {
    completedSteps.value = [...completedSteps.value, n].sort((a, b) => a - b)
  }
  emit('update:completedSteps', [...completedSteps.value])
}

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

function toggleConflict(item: { field: string; proposed: string; conflict: boolean }, checked: boolean) {
  if (!item.conflict || !checked) return
  for (const other of preview.value?.items ?? []) {
    if (other.field === item.field && other.proposed !== item.proposed) {
      selected.value[fieldKey(other)] = false
    }
  }
}

async function doPreview() {
  const urls = parseUrls()
  persistUrls()
  // 步 4 允许无链接（对在籍成员搜索 Fandom）；步 5/6 按组合名检索碟志
  if (!urls.length && step.value < 4) {
    previewError.value = '请先搜索并加入链接，或粘贴百科 URL'
    return
  }
  previewing.value = true
  previewError.value = ''
  candidates.value = []
  try {
    preview.value = await dbViewsApi.previewSource(props.uid, urls, step.value)
    const nextFields: Record<string, boolean> = {}
    for (const item of preview.value.items) {
      nextFields[fieldKey(item)] = item.is_new && !item.conflict && !props.locks[item.field]
    }
    selected.value = nextFields
    const nextMembers: Record<string, boolean> = {}
    for (const m of preview.value.member_proposals) {
      nextMembers[m.name] =
        m.action !== 'exists' && !m.possible_duplicate_of?.length
    }
    selectedMembers.value = nextMembers
    const nextSubunits: Record<string, boolean> = {}
    for (const u of preview.value.subunit_proposals) {
      nextSubunits[u.name] = u.action !== 'exists'
    }
    selectedSubunits.value = nextSubunits
    const nextAlbums: Record<string, boolean> = {}
    for (const a of preview.value.album_proposals) {
      const key = albumKey(a)
      // 默认勾选可改进项；纯「已在库」可选手动强制覆盖
      nextAlbums[key] =
        a.action === 'create' ||
        a.action === 'update' ||
        (a.action === 'fill_tracks' && !!a.external_id)
    }
    selectedAlbums.value = nextAlbums
  } catch (e) {
    preview.value = null
    previewError.value = e instanceof Error ? e.message : '抓取失败'
  } finally {
    previewing.value = false
  }
}

function albumKey(a: { name: string; album_id?: number | null; external_id?: string | null }) {
  return a.album_id != null ? `id:${a.album_id}` : `${a.name}::${a.external_id || ''}`
}

const selectedCount = computed(() => {
  const p = preview.value
  if (!p) return 0
  return (
    Object.values(selected.value).filter(Boolean).length +
    Object.entries(selectedMembers.value).filter(
      ([name, v]) => v && p.member_proposals.find((m) => m.name === name)?.action !== 'exists',
    ).length +
    Object.entries(selectedSubunits.value).filter(
      ([name, v]) => v && p.subunit_proposals.find((u) => u.name === name)?.action !== 'exists',
    ).length +
    Object.entries(selectedAlbums.value).filter(([key, v]) => {
      if (!v) return false
      const a = p.album_proposals.find((x) => albumKey(x) === key)
      // exists 也可强制覆盖；无 external_id 的 fill_tracks 不可写
      return !!a && !(a.action === 'fill_tracks' && !a.external_id)
    }).length
  )
})

const hasUnappliedPreview = computed(() => !!preview.value && selectedCount.value > 0)

async function applySelected(): Promise<boolean> {
  const p = preview.value
  if (!p) return false
  applying.value = true
  applyMsg.value = ''
  try {
    const fields: Record<string, string> = {}
    for (const item of p.items) {
      if (selected.value[fieldKey(item)]) fields[item.field] = item.proposed
    }
    const members = p.member_proposals.filter(
      (m) => m.action !== 'exists' && selectedMembers.value[m.name],
    )
    const subunits = p.subunit_proposals.filter(
      (u) => u.action !== 'exists' && selectedSubunits.value[u.name],
    )
    const albums = p.album_proposals.filter(
      (a) =>
        selectedAlbums.value[albumKey(a)] &&
        !(a.action === 'fill_tracks' && !a.external_id),
    )
    if (
      !Object.keys(fields).length &&
      !members.length &&
      !subunits.length &&
      !albums.length
    ) {
      applyMsg.value = '没有勾选任何提案'
      return false
    }
    const sourceUrls = [...new Set([...p.sources.map((s) => s.url), ...parseUrls()])]
    const appliedStep = step.value
    const res = await dbViewsApi.applySource(props.uid, {
      fields,
      members,
      subunits,
      albums,
      source_urls: sourceUrls,
      step: appliedStep,
    })
    const a = res.applied
    const parts: string[] = []
    if (a.fields.length) parts.push(`字段 ${a.fields.length}`)
    if (a.members_created) parts.push(`新建成员 ${a.members_created}`)
    if (a.members_linked) parts.push(`挂接成员 ${a.members_linked}`)
    if (a.albums_created) parts.push(`新专辑 ${a.albums_created}`)
    if (a.albums_updated) parts.push(`更新专辑 ${a.albums_updated}`)
    if (a.songs_created) parts.push(`新歌曲 ${a.songs_created}`)
    if (a.tracks_created) parts.push(`曲目 ${a.tracks_created}`)
    if (a.subunits_created) parts.push(`小分队 ${a.subunits_created}`)
    if (a.artist_fields_filled) parts.push(`档案补全 ${a.artist_fields_filled}`)
    if (a.members_skipped) parts.push(`跳过 ${a.members_skipped}`)
    if (a.tracklist_failed) parts.push(`曲目表失败 ${a.tracklist_failed}`)
    if (a.albums_year_only) parts.push(`仅年份专辑 ${a.albums_year_only}（未写 01-01）`)
    markStepCompleted(appliedStep)
    applyMsg.value = parts.length
      ? `第 ${appliedStep} 步已保存到资料库 · ${parts.join(' · ')}`
      : `第 ${appliedStep} 步已保存到资料库`
    clearPreviewState()
    persistDraft()
    return true
  } catch (e) {
    applyMsg.value = e instanceof Error ? e.message : '合并失败'
    return false
  } finally {
    applying.value = false
  }
}

async function onApplyOnly() {
  const ok = await applySelected()
  if (ok) emit('applied')
}

async function onApplyAndContinue() {
  const ok = await applySelected()
  if (!ok) return
  // 先推进再通知父级刷新，避免 load 把 step 拉回旧值
  const savedMsg = applyMsg.value
  nextAfterApply()
  if (savedMsg) applyMsg.value = savedMsg
  emit('applied')
}

function skipStep() {
  if (step.value < 6) requestStepChange(step.value + 1)
  else requestClose()
}

function nextAfterApply() {
  if (step.value < 6) goToStep(step.value + 1)
  else {
    persistDraft()
    emit('close')
  }
}

function onNextClick() {
  if (step.value >= 6) return
  requestStepChange(step.value + 1)
}

function onPrevClick() {
  if (step.value <= 1) return
  requestStepChange(step.value - 1)
}

function sourceBadge(t: string) {
  if (t === 'fandom') return 'Fandom'
  if (t === 'wikidata') return 'Wikidata'
  if (t === 'wikipedia') return 'Wikipedia'
  if (t === 'baidu') return '百度'
  return t
}

const needsUrls = computed(() => step.value <= 3)

type MemberFieldChip = {
  key: string
  label: string
  value: string
  origin: string
}

function fieldOriginLabel(origin: string) {
  if (origin === 'ai') return 'AI'
  if (origin === 'fandom') return 'Fandom'
  if (origin === 'wikidata') return 'Wikidata'
  if (origin === 'wikipedia') return '百科'
  if (origin === 'baidu') return '百度'
  if (origin === 'crawl') return '百科'
  return '百科'
}

function artistFieldChips(m: {
  artist_fields?: Record<string, string | null | undefined> | null
  positions?: string[] | null
  field_origins?: Record<string, string> | null
}): MemberFieldChip[] {
  const af = m.artist_fields || {}
  const origins = m.field_origins || {}
  const chips: MemberFieldChip[] = []
  const push = (key: string, label: string, raw: string | null | undefined) => {
    if (!raw) return
    const value = raw.replace(/\s+/g, ' ').trim()
    if (!value) return
    chips.push({
      key,
      label,
      value: value.length > 48 ? `${value.slice(0, 48)}…` : value,
      origin: origins[key] || 'crawl',
    })
  }
  push('birth_date', '生日', af.birth_date)
  push('birth_place', '出生地', af.birth_place)
  push('chinese_name', '中文名', af.chinese_name)
  push('english_name', '英文名', af.english_name)
  push('stage_name', '艺名', af.stage_name)
  push('occupation', '职业', af.occupation)
  if (m.positions?.length) {
    push('positions', '担当', m.positions.map(formatPosition).join(' / '))
  }
  push('description', '简介', af.description)
  return chips
}

/** @deprecated 保留给空态检测 */
function artistFieldSnippet(m: {
  artist_fields?: Record<string, string | null | undefined> | null
  positions?: string[] | null
  field_origins?: Record<string, string> | null
}) {
  return artistFieldChips(m).map((c) => `${c.label} ${c.value}`)
}
// 空态检测保留此函数；当前无调用点，显式引用避免 TS6133
void artistFieldSnippet

function hasUsefulArtistFields(m: {
  artist_fields?: Record<string, string | null | undefined> | null
  positions?: string[] | null
}) {
  const af = m.artist_fields || {}
  return !!(
    af.birth_date ||
    af.birth_place ||
    af.chinese_name ||
    af.english_name ||
    af.stage_name ||
    af.occupation ||
    af.description ||
    (m.positions && m.positions.length)
  )
}

const aiGapFillNote = computed(() => {
  if (step.value !== 4 || !preview.value) return ''
  const ag = preview.value.extra?.ai_gap_fill
  const st = ag?.status
  const err = (ag?.error || '').trim()
  if (st === 'skipped' || !st) {
    return err
      ? `入库 AI 未参与：${err}`
      : '入库 AI 未参与（未启用或未配置 base_url/model），仅使用百科抓取。'
  }
  if (st === 'error') {
    return err
      ? `入库 AI 调用失败：${err}（已回退为百科抓取结果）`
      : '入库 AI 调用失败，已回退为百科抓取结果。'
  }
  const n = ag?.filled_fields || 0
  if (n > 0) return `AI 已提案补全 ${n} 个空缺字段（紫色 AI 标记，写入前请核对）。`
  if (err) return `AI 已运行但未补到字段：${err}`
  return 'AI 已运行，当前无新增可填空缺（或百科已覆盖）。'
})

const aiGapFillTone = computed(() => {
  if (step.value !== 4 || !preview.value) return ''
  const st = preview.value.extra?.ai_gap_fill?.status
  if (st === 'error' || st === 'skipped' || !st) return 'warn'
  if ((preview.value.extra?.ai_gap_fill?.filled_fields || 0) > 0) return 'ok'
  return 'info'
})

const previewBtnLabel = computed(() => {
  if (previewing.value) return '提案生成中…'
  if (step.value === 4) return '补全在籍成员（百科 + AI）'
  return '生成本步提案'
})

const needsFandomMemberWarning = computed(() => {
  if (step.value !== 4 || !preview.value) return false
  const p = preview.value
  const hasUseful = p.member_proposals.some((m) => hasUsefulArtistFields(m))
  const srcs = p.sources || []
  const hasFandomMemberPage = srcs.some((s) => {
    const u = (s.url || '').toLowerCase()
    return s.source_type === 'fandom' && u.includes('kpop.fandom.com/wiki/')
  })
  const onlyGroupWikiBaidu =
    srcs.length > 0 &&
    srcs.every((s) => s.source_type === 'wikipedia' || s.source_type === 'baidu') &&
    !hasFandomMemberPage
  return !hasUseful || onlyGroupWikiBaidu
})

const emptyPreviewHint = computed(() => {
  if (!preview.value) return ''
  const p = preview.value
  if (step.value === 1 && !p.items.length) return '本步无字段变更（可能已同步）'
  if (step.value === 2 && !p.subunit_proposals.length) return '未发现小分队，可跳过'
  if (step.value === 3 && !p.member_proposals.length) return '未发现可生长成员'
  if (step.value === 4 && !p.member_proposals.length) return '暂无成员详情可补；可添加 kpop.fandom.com 成员页后再试，或跳过'
  if (step.value === 5 && !p.album_proposals.length) return '未找到专辑候选'
  if (step.value === 6 && !p.album_proposals.length) return '没有需要补曲目的专辑（或已全部有曲）'
  return ''
})
</script>

<template>
  <teleport to="body">
    <transition name="gs">
      <div v-if="open" class="gs-scrim" @click.self="requestClose">
        <aside class="gs-panel" role="dialog" aria-label="数据生长">
          <header class="gs-head">
            <div>
              <div class="gs-kicker">数据生长</div>
              <h2>{{ groupName }}</h2>
            </div>
            <button class="gs-x" type="button" @click="requestClose">关闭</button>
          </header>

          <nav class="gs-steps">
            <button
              v-for="s in STEPS"
              :key="s.id"
              type="button"
              class="gs-step"
              :class="{ on: step === s.id, done: isStepCompleted(s.id), past: step > s.id && !isStepCompleted(s.id) }"
              @click="requestStepChange(s.id)"
            >
              <i>{{ isStepCompleted(s.id) ? '✓' : s.id }}</i>
              <span>{{ s.title }}</span>
            </button>
          </nav>

          <div class="gs-body">
            <div class="gs-step-title">
              <b>第 {{ step }} 步 · {{ stepMeta.title }}</b>
              <span>{{ stepMeta.hint }}</span>
            </div>

            <div class="gs-block">
              <div class="gs-label">来源链接</div>
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
              <p v-if="step === 4" class="gs-note">
                可直接按在籍成员搜索 Fandom 补全档案；空缺字段可经入库 AI 提案（均带 AI 标记，需人审勾选后写入）。若有成员个人页链接（kpop.fandom.com）请一并粘贴，效果更稳。
              </p>
              <p v-else-if="!needsUrls" class="gs-note">本步可直接按组合名检索碟志；有百科链接亦可一并带上。</p>
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
                  <span class="gs-cand-s">{{ c.snippet }}</span>
                  <span class="gs-cand-a">加入</span>
                </button>
              </div>
              <div class="gs-row end">
                <button
                  class="gs-btn primary"
                  type="button"
                  :disabled="previewing || (needsUrls && !srcUrlsInput.trim())"
                  @click="doPreview"
                >
                  {{ previewBtnLabel }}
                </button>
              </div>
            </div>

            <div v-if="previewError" class="gs-err">{{ previewError }}</div>
            <div v-if="applyMsg" class="gs-ok">{{ applyMsg }}</div>

            <div v-if="preview" class="gs-preview">
              <div v-if="emptyPreviewHint" class="gs-empty-hint">{{ emptyPreviewHint }}</div>
              <p
              v-if="aiGapFillNote"
              class="gs-note gs-ai-note"
              :class="{
                'gs-ai-note--warn': aiGapFillTone === 'warn',
                'gs-ai-note--ok': aiGapFillTone === 'ok',
              }"
            >{{ aiGapFillNote }}</p>

              <template v-if="step === 1 && preview.items.length">
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
                    <tr v-for="item in preview.items" :key="fieldKey(item)" :class="{ conflict: item.conflict }">
                      <td>
                        <input
                          v-model="selected[fieldKey(item)]"
                          type="checkbox"
                          :disabled="!!locks[item.field]"
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
                <div v-if="preview.extra.company_raw" class="gs-note">
                  来源页公司：{{ preview.extra.company_raw }}（公司关系写入 P1）
                </div>
              </template>

              <div v-if="needsFandomMemberWarning" class="gs-warn-banner">
                <b>生日等精确日期仍需 Fandom / 个人百科页</b>
                <p>
                  组合级维基/百度页通常不含成员生日。请加入
                  <code>kpop.fandom.com</code> 成员个人页，或点上方「补全在籍成员」自动检索；空缺的中文名/出生地等可由入库 AI 提案（紫色 AI 标记）。
                </p>
              </div>

              <template v-if="(step === 3 || step === 4) && preview.member_proposals.length">
                <label
                  v-for="m in preview.member_proposals"
                  :key="m.name"
                  class="gs-grow col"
                  :class="{ dim: m.action === 'exists' }"
                >
                  <div class="gs-grow-line">
                    <input
                      v-model="selectedMembers[m.name]"
                      type="checkbox"
                      :disabled="m.action === 'exists'"
                    />
                    <span class="gs-badge act" :class="m.action">{{
                      m.action === 'create'
                        ? '新建'
                        : m.action === 'link'
                          ? '挂接'
                          : m.action === 'enrich'
                            ? '补全'
                            : '已在库'
                    }}</span>
                    <b>{{ m.name }}</b>
                    <span v-if="m.korean_name" class="muted">{{ m.korean_name }}</span>
                    <span v-if="step === 3" class="muted"
                      >{{ m.join_date || '?' }} →
                      {{ m.leave_date || (m.status === 'Former' ? '?' : '至今') }}</span
                    >
                    <span v-if="m.possible_duplicate_of?.length" class="warn">疑似重复</span>
                  </div>
                  <div v-if="artistFieldChips(m).length" class="gs-afields">
                    <span
                      v-for="chip in artistFieldChips(m)"
                      :key="chip.key"
                      class="gs-afield"
                      :class="{ ai: chip.origin === 'ai' }"
                      :title="chip.origin === 'ai' ? (m.ai_basis || 'AI 提案') : fieldOriginLabel(chip.origin)"
                    >
                      <i class="gs-origin" :class="chip.origin === 'ai' ? 'ai' : 'enc'">{{
                        chip.origin === 'ai' ? 'AI' : fieldOriginLabel(chip.origin)
                      }}</i>
                      <span>{{ chip.label }} {{ chip.value }}</span>
                    </span>
                  </div>
                  <p v-if="m.ai_basis && step === 4" class="gs-ai-basis">依据：{{ m.ai_basis }}</p>
                </label>
              </template>

              <template v-if="step === 2 && preview.subunit_proposals.length">
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
                  <span class="muted">{{ u.member_names.slice(0, 6).join('、') }}</span>
                </label>
              </template>

              <template v-if="(step === 5 || step === 6) && preview.album_proposals.length">
                <label
                  v-for="a in preview.album_proposals"
                  :key="albumKey(a)"
                  class="gs-grow col"
                  :class="{ dim: a.action === 'exists' }"
                >
                  <div class="gs-grow-line">
                    <input
                      v-model="selectedAlbums[albumKey(a)]"
                      type="checkbox"
                      :disabled="a.action === 'fill_tracks' && !a.external_id"
                    />
                    <span class="gs-badge act" :class="a.action">{{
                      a.action === 'create'
                        ? '新建'
                        : a.action === 'update'
                          ? '更新'
                          : a.action === 'fill_tracks'
                            ? '补曲目'
                            : '已在库'
                    }}</span>
                    <b>{{ a.name }}</b>
                    <span class="muted"
                      >{{ a.release_date || a.year || '?' }}<template v-if="a.db_release_date && a.action === 'update'">
                        · 库内 {{ a.db_release_date }}</template
                      ><template v-if="a.track_count"> · {{ a.track_count }} 曲</template></span
                    >
                  </div>
                  <div v-if="a.tracks_preview?.length" class="gs-tracks">
                    <span v-for="(t, i) in a.tracks_preview.slice(0, 8)" :key="i">{{ t.track_number }}. {{ t.name }}</span>
                    <span v-if="a.tracks_preview.length > 8" class="muted">…共 {{ a.tracks_preview.length }} 首</span>
                  </div>
                </label>
              </template>

              <div v-for="(err, i) in preview.errors" :key="i" class="gs-err">{{ err }}</div>

              <div class="gs-foot">
                <button type="button" class="gs-btn ghost" @click="clearPreviewState">放弃本步提案</button>
                <button
                  type="button"
                  class="gs-btn"
                  :disabled="applying || !selectedCount"
                  @click="onApplyOnly"
                >
                  {{ applying ? '写入中…' : `写入本步（${selectedCount}）` }}
                </button>
                <button
                  type="button"
                  class="gs-btn primary"
                  :disabled="applying || !selectedCount"
                  @click="onApplyAndContinue"
                >
                  {{ applying ? '写入中…' : '写入并下一步' }}
                </button>
              </div>
            </div>
          </div>

          <footer class="gs-bar">
            <button type="button" class="gs-btn ghost" :disabled="step <= 1" @click="onPrevClick">上一步</button>
            <button v-if="stepMeta.skippable" type="button" class="gs-btn ghost" @click="skipStep">跳过此步</button>
            <button type="button" class="gs-btn" :disabled="step >= 6" @click="onNextClick">下一步</button>
          </footer>

          <div v-if="leaveConfirm" class="gs-confirm" role="alertdialog" aria-label="未写入提案">
            <div class="gs-confirm-card">
              <p class="gs-confirm-title">当前提案尚未写入，离开将丢失。</p>
              <p class="gs-confirm-sub">可先写入本步，或丢弃提案后继续。</p>
              <div class="gs-confirm-actions">
                <button type="button" class="gs-btn ghost" @click="cancelLeaveConfirm">取消</button>
                <button type="button" class="gs-btn" @click="confirmLeaveDiscard">仍要离开</button>
                <button type="button" class="gs-btn primary" :disabled="applying" @click="confirmLeaveApplyFirst">
                  {{ applying ? '写入中…' : '先写入' }}
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
  position: relative;
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
.gs-steps {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 4px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--sa-border-subtle, rgba(255, 255, 255, 0.08));
}
.gs-step {
  border: 1px solid transparent;
  background: transparent;
  border-radius: 10px;
  padding: 6px 4px;
  cursor: pointer;
  color: var(--sa-text-tertiary);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  font-family: inherit;
}
.gs-step i {
  font-style: normal;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 800;
  background: var(--sa-subtle, rgba(255, 255, 255, 0.06));
}
.gs-step span {
  font-size: 10px;
  line-height: 1.2;
  text-align: center;
}
.gs-step.on {
  color: var(--sa-text-primary);
  border-color: rgba(122, 92, 240, 0.35);
  background: rgba(122, 92, 240, 0.08);
}
.gs-step.on i {
  background: var(--sa-accent, #7a5cf0);
  color: #fff;
}
.gs-step.done i {
  background: rgba(24, 160, 88, 0.2);
  color: #18a058;
}
.gs-step.past i {
  background: var(--sa-subtle, rgba(255, 255, 255, 0.06));
  color: var(--sa-text-tertiary, #888);
}
.gs-confirm {
  position: absolute;
  inset: 0;
  z-index: 5;
  background: rgba(6, 6, 14, 0.55);
  backdrop-filter: blur(3px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.gs-confirm-card {
  width: min(360px, 100%);
  border-radius: 14px;
  border: 1px solid var(--sa-border, rgba(255, 255, 255, 0.12));
  background: var(--sa-elevated, #14141f);
  padding: 18px 18px 14px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);
}
.gs-confirm-title {
  margin: 0 0 6px;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.45;
}
.gs-confirm-sub {
  margin: 0 0 14px;
  font-size: 12px;
  color: var(--sa-text-tertiary, #888);
  line-height: 1.5;
}
.gs-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}
.gs-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 18px 24px;
}
.gs-step-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 12px;
}
.gs-step-title b {
  font-size: 14px;
}
.gs-step-title span {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.gs-block {
  border: 1px solid var(--sa-border-subtle, rgba(255, 255, 255, 0.08));
  border-radius: 12px;
  padding: 12px;
  margin-bottom: 12px;
}
.gs-label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--sa-text-tertiary);
  margin-bottom: 8px;
}
.gs-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.gs-row.end {
  justify-content: flex-end;
  margin-bottom: 0;
  margin-top: 8px;
}
.gs-input {
  flex: 1;
  border: 1px solid var(--sa-border, rgba(255, 255, 255, 0.12));
  border-radius: 9px;
  background: var(--sa-bg, #0e0e16);
  color: inherit;
  padding: 8px 11px;
  font-size: 13px;
  font-family: inherit;
  outline: none;
}
.gs-input:focus {
  border-color: var(--sa-accent, #7a5cf0);
}
.gs-ta {
  width: 100%;
  resize: vertical;
  box-sizing: border-box;
}
.gs-btn {
  border: 1px solid var(--sa-border, rgba(255, 255, 255, 0.12));
  border-radius: 9px;
  background: transparent;
  color: inherit;
  font-size: 12.5px;
  font-weight: 700;
  padding: 0 14px;
  height: 36px;
  cursor: pointer;
  white-space: nowrap;
  font-family: inherit;
}
.gs-btn.primary {
  background: var(--sa-accent, #7a5cf0);
  border-color: var(--sa-accent, #7a5cf0);
  color: #fff;
}
.gs-btn.ghost {
  background: none;
}
.gs-btn:disabled {
  opacity: 0.45;
  cursor: default;
}
.gs-note {
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
  margin: 6px 0 0;
  line-height: 1.5;
}
.gs-cands {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 8px;
}
.gs-cand {
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9px;
  background: none;
  color: inherit;
  padding: 7px 10px;
  cursor: pointer;
  font-family: inherit;
}
.gs-cand:hover {
  border-color: var(--sa-accent, #7a5cf0);
}
.gs-badge {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 800;
  border-radius: 6px;
  padding: 2px 7px;
}
.gs-badge.fandom {
  background: rgba(224, 90, 0, 0.14);
  color: #e05a00;
}
.gs-badge.wikidata {
  background: rgba(0, 102, 51, 0.14);
  color: #6fcf97;
}
.gs-badge.wikipedia {
  background: rgba(64, 120, 255, 0.14);
  color: #6ea0ff;
}
.gs-badge.baidu {
  background: rgba(41, 120, 255, 0.14);
  color: #4d9fff;
}
.gs-badge.act.create,
.gs-badge.act.fill_tracks,
.gs-badge.act.enrich,
.gs-badge.act.update {
  background: rgba(122, 92, 240, 0.14);
  color: var(--sa-accent, #7a5cf0);
}
.gs-badge.act.link {
  background: rgba(24, 160, 88, 0.12);
  color: #18a058;
}
.gs-badge.act.exists {
  background: var(--sa-subtle);
  color: var(--sa-text-tertiary);
}
.gs-cand-t {
  font-weight: 700;
  font-size: 12.5px;
  white-space: nowrap;
}
.gs-cand-s {
  flex: 1;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gs-cand-a {
  color: var(--sa-accent, #7a5cf0);
  font-size: 11px;
  font-weight: 700;
}
.gs-err {
  margin: 8px 0;
  padding: 8px 11px;
  border-radius: 8px;
  background: rgba(208, 48, 80, 0.1);
  color: #d03050;
  font-size: 12px;
}
.gs-ok {
  margin: 8px 0;
  padding: 8px 11px;
  border-radius: 8px;
  background: rgba(24, 160, 88, 0.12);
  color: #18a058;
  font-size: 12.5px;
}
.gs-preview {
  border: 1px solid rgba(122, 92, 240, 0.35);
  border-radius: 12px;
  padding: 12px;
}
.gs-empty-hint {
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 12.5px;
  padding: 10px 0;
}
.gs-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.gs-table th {
  text-align: left;
  font-size: 10px;
  color: var(--sa-text-tertiary);
  padding: 4px 6px;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.gs-table td {
  padding: 6px;
  border-bottom: 1px solid var(--sa-border-subtle);
  vertical-align: top;
}
.gs-table tr.conflict {
  background: rgba(217, 119, 6, 0.06);
}
.muted {
  color: var(--sa-text-tertiary);
}
.prop {
  color: var(--sa-accent, #7a5cf0);
  font-weight: 600;
}
.warn {
  color: #d97706;
  font-size: 11px;
}
.gs-grow {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 4px;
  border-radius: 8px;
  font-size: 12.5px;
  cursor: pointer;
}
.gs-grow.col {
  flex-direction: column;
  align-items: stretch;
}
.gs-grow-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.gs-grow:hover {
  background: rgba(122, 92, 240, 0.06);
}
.gs-grow.dim {
  opacity: 0.5;
}
.gs-afields {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  padding: 0 0 2px 28px;
}
.gs-afield {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--sa-text-secondary);
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  padding: 2px 7px;
  max-width: 100%;
  line-height: 1.4;
}
.gs-afield.ai {
  background: rgba(122, 92, 240, 0.1);
  border-color: rgba(122, 92, 240, 0.28);
  color: #c4b5fd;
}
.gs-origin {
  font-style: normal;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.04em;
  border-radius: 4px;
  padding: 0 4px;
  line-height: 1.5;
  flex-shrink: 0;
}
.gs-origin.enc {
  background: rgba(148, 163, 184, 0.18);
  color: #94a3b8;
}
.gs-origin.ai {
  background: rgba(122, 92, 240, 0.22);
  color: #ddd6fe;
}
.gs-ai-basis {
  margin: 2px 0 0 28px;
  font-size: 11px;
  color: var(--sa-text-tertiary, #888);
  line-height: 1.4;
}
.gs-ai-note {
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid rgba(122, 92, 240, 0.22);
  background: rgba(122, 92, 240, 0.08);
  font-size: 12.5px;
  line-height: 1.45;
}
.gs-ai-note--warn {
  border-color: rgba(217, 119, 6, 0.35);
  background: rgba(217, 119, 6, 0.1);
  color: #b45309;
  font-weight: 600;
}
.gs-ai-note--ok {
  border-color: rgba(16, 185, 129, 0.3);
  background: rgba(16, 185, 129, 0.08);
}
.gs-warn-banner {
  margin: 0 0 12px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(217, 119, 6, 0.28);
  background: rgba(217, 119, 6, 0.07);
  color: var(--sa-text-primary, inherit);
}
.gs-warn-banner b {
  display: block;
  font-size: 12.5px;
  font-weight: 700;
  color: #b45309;
  margin-bottom: 4px;
}
.gs-warn-banner p {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--sa-text-secondary);
}
.gs-warn-banner code {
  font-size: 11px;
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.06);
}
.gs-tracks {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  padding: 2px 0 4px 28px;
  font-size: 11px;
  color: var(--sa-text-secondary);
}
.gs-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
.gs-bar {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  padding: 12px 16px;
  border-top: 1px solid var(--sa-border-subtle);
}
.gs-enter-active,
.gs-leave-active {
  transition: opacity 0.2s ease;
}
.gs-enter-active .gs-panel,
.gs-leave-active .gs-panel {
  transition: transform 0.22s ease;
}
.gs-enter-from,
.gs-leave-to {
  opacity: 0;
}
.gs-enter-from .gs-panel,
.gs-leave-to .gs-panel {
  transform: translateX(28px);
}
</style>
