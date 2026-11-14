/* 静态导出用的「场次与机位」渲染器（无框架）
 * 依赖 window.__SM__ = { matrix, avatars, origin, currentId, scopes[] }
 * 同时复刻 StageMatrixDock.vue 的 class 名，以便复用导出页里已有的 scoped CSS。
 */
(function () {
  const P = window.__SM__
  const root = document.getElementById('sm-root')
  if (!root || !P || !P.matrix) return

  const SCOPES = P.scopes || []
  const M = P.matrix
  const ORIGIN = P.origin || ''
  const AVATARS = P.avatars || {}

  const KIND_ORDER = ['stage', 'official', 'fancam']
  const KIND_LABEL = { stage: '打歌舞台', official: '官方影像', fancam: '粉丝直拍' }
  const KIND_BY_TYPE = {
    performancevideo: 'stage',
    fancam: 'fancam',
    officialmv: 'official',
    specialstage: 'official',
    collabstage: 'official',
  }
  const CAMERA_LABEL = {
    mv: '官方 MV',
    stage: '官方舞台',
    portrait_fancam: '竖屏直拍',
    landscape_fancam: '横屏直拍',
    facecam: '面部直拍',
    onetake: '一镜到底',
  }
  // 与前端 CAMERA_ANGLE_OPTIONS 一致（入库真值优先）
  const CAMERA_ANGLE_LABEL = {
    FanCam: '粉丝直拍',
    FaceCam: '面部特写机位',
    FullCam: '全景机位',
    BroadcastCam: '放送机位',
    'Tower Cam': '塔式机位',
    'K-Choreo': '编舞镜头',
    OneTake: '一镜到底',
    MultiCam: '多机位',
    SkyCam: '航拍机位',
    其他: '其他',
  }

  const dayList = Array.isArray(M.days) ? M.days : []

  function el(tag, cls, text) {
    const e = document.createElement(tag)
    if (cls) e.className = cls
    for (const s of SCOPES) e.setAttribute(s, '')
    if (text != null) e.textContent = text
    return e
  }

  function kindOf(v) {
    const primary = String(v.video_type || (v.video_types || [])[0] || '').toLowerCase()
    return KIND_BY_TYPE[primary] || 'stage'
  }
  function countOf(kind) {
    let n = 0
    for (const d of dayList) for (const v of d.videos || []) if (kindOf(v) === kind) n++
    return n
  }
  function officialTypeLabel(types) {
    if (types.indexOf('officialmv') >= 0) return '官方 MV'
    if (types.indexOf('specialstage') >= 0) return '特别舞台'
    if (types.indexOf('collabstage') >= 0) return '合作舞台'
    return null
  }
  function cameraLabel(v) {
    const real = CAMERA_ANGLE_LABEL[v.camera]
    if (real) return real
    const types = (v.video_types || []).map((t) => String(t).toLowerCase())
    const t = officialTypeLabel(types)
    if (t) return t
    return CAMERA_LABEL[v.camera] || null
  }
  function orientLabel(v) {
    if (v.orientation === 'portrait') return '9:16 竖屏'
    if (v.orientation === 'landscape') return '16:9 横屏'
    return ''
  }
  function fmtDate(d) {
    if (!d) return '未标注日期'
    return String(d).slice(0, 10).replace(/-/g, '/')
  }
  function fmtDur(sec) {
    if (sec == null || isNaN(sec)) return '--'
    const s = Math.floor(sec)
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const ss = s % 60
    return h > 0 ? h + ':' + String(m).padStart(2, '0') + ':' + String(ss).padStart(2, '0')
      : m + ':' + String(ss).padStart(2, '0')
  }
  function subline(v) {
    const parts = [cameraLabel(v) || '机位']
    if (v.duration) parts.push(fmtDur(v.duration))
    return parts.join(' · ')
  }
  function bracketLabels(videos) {
    const out = []
    for (const v of videos) {
      const matches = [...String(v.name || '').matchAll(/\[([^\]]+)\]/g)]
      const text = matches.length ? matches[matches.length - 1][1].trim() : ''
      if (text && out.indexOf(text) < 0) out.push(text)
    }
    return out
  }
  function avatarKey(m) {
    return m.kind + '-' + m.id
  }
  function initialOf(name) {
    return (name || '?').trim().charAt(0).toUpperCase()
  }

  function allVideos() {
    const out = []
    for (const d of dayList) for (const v of d.videos || []) out.push(v)
    return out
  }
  function initialKind() {
    const cur = allVideos().find((v) => v.current)
    if (cur) return kindOf(cur)
    return KIND_ORDER.find((k) => countOf(k) > 0) || 'stage'
  }
  function visibleKinds() {
    return KIND_ORDER.filter((k) => countOf(k) > 0)
  }
  function tabDays(kind) {
    return dayList
      .map((d) => ({ key: d.key, date: d.date, event_name: d.event_name, videos: (d.videos || []).filter((v) => kindOf(v) === kind) }))
      .filter((d) => d.videos.length > 0)
  }

  let kind = initialKind()
  let slotKey = null // null = 自动（当前视频所在场次 / 第一场）
  let pickerOpen = false

  function daysOfKind() {
    return tabDays(kind)
  }
  function activeDay() {
    const list = daysOfKind()
    if (slotKey != null) {
      const hit = list.find((d) => d.key === slotKey)
      if (hit) return hit
    }
    return list.find((d) => d.videos.some((v) => v.current)) || list[0] || null
  }

  function optionLabel(d, official) {
    if (official) {
      return [fmtDate(d.date)].concat(bracketLabels(d.videos)).filter(Boolean).join(' · ')
    }
    return [fmtDate(d.date), d.event_name || (d.date ? '未标注' : '')].filter(Boolean).join(' · ')
  }

  function camRow(v, index, official) {
    const active = !!v.current
    const row = el('div', 'cam-item' + (active ? ' is-active' : ''))
    row.setAttribute('role', 'button')
    row.setAttribute('tabindex', '0')
    row.title = active ? '当前播放' : '在新窗口打开线上视频'

    row.appendChild(el('span', 'cam-no', String(index + 1).padStart(2, '0')))

    const av = el('span', 'cam-avatar' + (v.member.kind === 'group' ? ' is-group' : ''))
    const key = avatarKey(v.member)
    if (v.member.avatar_path && AVATARS[key]) {
      const img = document.createElement('img')
      for (const s of SCOPES) img.setAttribute(s, '')
      img.src = AVATARS[key]
      img.alt = ''
      img.loading = 'lazy'
      av.appendChild(img)
    } else {
      const span = el('span', null, v.member.kind === 'group' ? 'ALL' : initialOf(v.member.name))
      av.appendChild(span)
    }
    row.appendChild(av)

    const main = el('span', 'cam-main')
    const name = el('span', 'cam-name', v.member.name)
    if (v.member.confidence === 'low') {
      name.appendChild(el('span', 'cam-flag', '待确认'))
    }
    if (active) {
      const wave = el('span', 'sound-wave')
      wave.appendChild(el('i'))
      wave.appendChild(el('i'))
      wave.appendChild(el('i'))
      name.appendChild(wave)
    }
    main.appendChild(name)
    main.appendChild(el('span', 'cam-sub', subline(v)))
    row.appendChild(main)

    const badge = orientLabel(v)
    if (badge) row.appendChild(el('span', 'cam-badge', badge))

    if (!active && v.uid) {
      const open = () => window.open(ORIGIN + '/videos/' + v.uid, '_blank', 'noopener')
      row.addEventListener('click', open)
      row.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() }
      })
    }
    return row
  }

  function listSection(videos, title, unit, official) {
    if (!videos.length) return null
    const sec = el('section', 'sheet-block')
    const head = el('div', 'block-head')
    head.appendChild(el('span', 'block-title', title))
    head.appendChild(
      el('span', 'block-count', videos.length + ' ' + unit + (official ? '' : '已索引')),
    )
    sec.appendChild(head)
    const list = el('div', 'cam-list')
    videos.forEach((v, i) => list.appendChild(camRow(v, i, official)))
    sec.appendChild(list)
    return sec
  }

  function render() {
    root.textContent = ''
    const official = kind === 'official'
    const list = daysOfKind()
    const day = activeDay()

    // ① 分栏 + 场次选择
    const top = el('section', 'sheet-block')
    const head = el('div', 'block-head')
    const tabs = el('div', 'kind-tabs')
    for (const k of visibleKinds()) {
      const b = el('button', 'kind-tab' + (k === kind ? ' is-active' : ''))
      b.type = 'button'
      b.appendChild(el('span', null, KIND_LABEL[k]))
      b.appendChild(el('span', 'kind-count', String(countOf(k))))
      b.addEventListener('click', () => {
        if (kind === k) return
        kind = k
        slotKey = null
        pickerOpen = false
        render()
      })
      tabs.appendChild(b)
    }
    head.appendChild(tabs)
    head.appendChild(el('span', 'block-count', official ? countOf(kind) + ' 条' : list.length + ' 场'))
    top.appendChild(head)

    // 场次选择（自绘下拉，避免依赖 SPA 的 GlassSelect）
    const pick = el('div', 'session-pick')
    const sel = el('div', 'sm-sel' + (pickerOpen ? ' is-open' : ''))
    const trigger = el('button', 'sm-sel-btn')
    trigger.type = 'button'
    trigger.setAttribute('aria-haspopup', 'listbox')
    trigger.setAttribute('aria-expanded', pickerOpen ? 'true' : 'false')
    trigger.appendChild(el('span', 'sm-sel-prefix', official ? '日期' : '场次'))
    trigger.appendChild(el('span', 'sm-sel-label', day ? optionLabel(day, official) : '无场次'))
    trigger.appendChild(el('span', 'sm-sel-caret', '▾'))
    trigger.addEventListener('click', (e) => {
      e.stopPropagation()
      pickerOpen = !pickerOpen
      render()
    })
    sel.appendChild(trigger)

    if (pickerOpen) {
      const panel = el('div', 'sm-sel-panel')
      panel.setAttribute('role', 'listbox')
      for (const d of list) {
        const opt = el('button', 'sm-sel-opt' + (day && d.key === day.key ? ' is-active' : ''))
        opt.type = 'button'
        opt.appendChild(el('span', 'sm-sel-opt-label', optionLabel(d, official)))
        opt.appendChild(el('span', 'sm-sel-opt-count', String(d.videos.length)))
        opt.addEventListener('click', (e) => {
          e.stopPropagation()
          slotKey = d.key
          pickerOpen = false
          render()
        })
        panel.appendChild(opt)
      }
      sel.appendChild(panel)
    }
    pick.appendChild(sel)
    top.appendChild(pick)
    root.appendChild(top)

    const videos = day ? day.videos : []
    const groups = videos.filter((v) => v.member.kind === 'group')
    const members = videos.filter((v) => v.member.kind !== 'group')
    const unit = official ? '条' : '机位'

    const g = listSection(groups, official ? '组合作品' : '组合直拍', unit, official)
    if (g) root.appendChild(g)
    const m = listSection(members, official ? '成员作品' : '成员直拍', unit, official)
    if (m) root.appendChild(m)
  }

  // 静态页里 dock 的两个 tab（场次与机位 / 视频档案）原本靠 Vue 切，这里补上
  function wireDockTabs() {
    const tabs = [...document.querySelectorAll('.dock-tab')]
    const panels = [...document.querySelectorAll('.dock-body > .dock-panel')]
    if (tabs.length < 2 || panels.length < 2) return
    const show = (idx) => {
      tabs.forEach((t, i) => t.classList.toggle('dock-tab--active', i === idx))
      panels.forEach((p, i) => {
        p.style.display = i === idx ? '' : 'none'
      })
    }
    tabs.forEach((t, i) => t.addEventListener('click', () => show(i)))
  }
  wireDockTabs()

  document.addEventListener('click', (e) => {
    if (!pickerOpen) return
    if (!root.contains(e.target)) {
      pickerOpen = false
      render()
    }
  })
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && pickerOpen) {
      pickerOpen = false
      render()
    }
  })

  render()
})()
