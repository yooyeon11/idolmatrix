import http from './index'

export interface DbMemberRow {
  membership_id: number
  artist_id: number
  artist_uid?: string | null
  name: string
  stage_name?: string | null
  avatar_path?: string | null
  join_date?: string | null
  leave_date?: string | null
  status: string
  positions: string[]
  is_dangling: boolean
  chinese_name?: string | null
  english_name?: string | null
  korean_name?: string | null
  birth_date?: string | null
  birth_place?: string | null
  gender?: string | null
  debut_date?: string | null
  occupation?: string | null
  description?: string | null
  tagline?: string | null
  social_media?: Record<string, string> | null
  external_links: { url?: string; source?: string; [k: string]: unknown }[]
}

export interface DbCompanyRow {
  relation_id: number
  company_id: number
  name: string
  role?: string | null
  status: string
  start_date?: string | null
  end_date?: string | null
  is_dangling: boolean
}

export interface DbGroupRow {
  id: number
  uid: string
  name: string
  korean_name?: string | null
  chinese_name?: string | null
  debut_date?: string | null
  group_type?: string | null
  avatar_path?: string | null
  is_subunit: boolean
  parent_name?: string | null
  members_active: number
  members_former: number
  member_sample: { id: number; name: string; avatar_path?: string | null }[]
  members: DbMemberRow[]
  companies: DbCompanyRow[]
  works: { albums: number; songs: number; videos: number }
  completeness: number
  completeness_missing: string[]
  issues: { error: number; warning: number; hint: number }
}

export interface GroupListResponse {
  items: DbGroupRow[]
  total: number
  checklist: string[]
}

export interface DbIssueCount {
  error: number
  warning: number
  hint: number
}

export interface DbArtistGroupBrief {
  group_id: number
  group_uid: string
  name: string
  status: string
}

export interface DbArtistRow {
  id: number
  uid: string
  name: string
  chinese_name?: string | null
  english_name?: string | null
  korean_name?: string | null
  stage_name?: string | null
  birth_date?: string | null
  debut_date?: string | null
  gender?: string | null
  avatar_path?: string | null
  groups: DbArtistGroupBrief[]
  works: { albums: number; songs: number; videos: number }
  completeness: number
  completeness_missing: string[]
  issues: DbIssueCount
}

export interface ArtistListResponse {
  items: DbArtistRow[]
  total: number
  checklist: string[]
}

export interface DbAlbumRow {
  id: number
  uid: string
  name: string
  chinese_name?: string | null
  korean_name?: string | null
  album_type?: string | null
  release_date?: string | null
  cover_path?: string | null
  release_artist_type?: string | null
  release_artist_name?: string | null
  release_artist_uid?: string | null
  works: { tracks: number; videos: number }
  completeness: number
  completeness_missing: string[]
  issues: DbIssueCount
}

export interface AlbumListResponse {
  items: DbAlbumRow[]
  total: number
  checklist: string[]
}

export interface DbSongPerformer {
  subject_type: string
  subject_id: number
  subject_uid?: string | null
  name: string
  role: string
}

export interface DbSongRow {
  id: number
  uid: string
  name: string
  chinese_name?: string | null
  korean_name?: string | null
  song_type?: string | null
  release_date?: string | null
  duration?: number | null
  performers: DbSongPerformer[]
  works: { albums: number; videos: number }
  completeness: number
  completeness_missing: string[]
  issues: DbIssueCount
}

export interface SongListResponse {
  items: DbSongRow[]
  total: number
  checklist: string[]
}

export interface AlbumWorkspaceSubject {
  type: 'group' | 'artist' | string
  id: number
  uid: string
  name: string
}

export interface AlbumWorkspaceLabel {
  id: number
  name: string
}

export interface AlbumWorkspaceTrack {
  track_id: number
  song_id?: number | null
  song_uid?: string | null
  song_name?: string | null
  song_chinese_name?: string | null
  song_duration?: number | null
  disc_number: number
  track_number: number
  is_dangling: boolean
}

export interface AlbumWorkspaceVideo {
  id: number
  uid: string
  name: string
  video_type?: string | null
}

export interface AlbumWorkspaceResponse {
  album: {
    id: number
    uid: string
    name: string
    chinese_name?: string | null
    english_name?: string | null
    korean_name?: string | null
    release_date?: string | null
    album_type?: string | null
    description?: string | null
    cover_path?: string | null
  }
  subject: AlbumWorkspaceSubject | null
  label: AlbumWorkspaceLabel | null
  tracks: AlbumWorkspaceTrack[]
  videos: AlbumWorkspaceVideo[]
  completeness: number
  completeness_missing: string[]
  issues: {
    severity: 'error' | 'warning' | 'hint'
    title: string
    suggestion: string
  }[]
  issue_counts: Record<string, number>
  completeness_checklist: string[]
}

export const albumWorkspaceApi = {
  getAlbumWorkspace(uid: string) {
    return http.get<AlbumWorkspaceResponse>(`/db/albums/${uid}/workspace`).then((r) => r.data)
  },
}

export interface SongWorkspaceResponse {
  song: {
    id: number
    uid: string
    name: string
    chinese_name?: string | null
    english_name?: string | null
    korean_name?: string | null
    song_type?: string | null
    release_date?: string | null
    duration?: number | null
    description?: string | null
  }
  performers: {
    relation_id: number
    subject_type: 'artist' | 'group' | 'dangling' | string
    subject_id?: number | null
    subject_uid?: string | null
    name: string
    role: string
    order: number
    is_dangling: boolean
  }[]
  albums: {
    track_id: number
    id: number
    uid: string
    name: string
    release_date?: string | null
    album_type?: string | null
    disc_number: number
    track_number: number
  }[]
  videos: {
    id: number
    uid: string
    name: string
    video_type?: string | null
  }[]
  credits: {
    id: number
    name: string
    role: string
    artist_id?: number | null
    artist_name?: string | null
  }[]
  completeness: number
  completeness_missing: string[]
  issues: {
    severity: 'error' | 'warning' | 'hint'
    title: string
    suggestion: string
  }[]
  issue_counts: Record<string, number>
  completeness_checklist: string[]
}

export const songWorkspaceApi = {
  getSongWorkspace(uid: string) {
    return http.get<SongWorkspaceResponse>(`/db/songs/${uid}/workspace`).then((r) => r.data)
  },
}

export interface FieldSuggestResult {
  field: string
  value: string | null
  source_label: string | null
  source_url: string | null
}

export const dbFieldSuggestApi = {
  /** 单字段搜索：只检索建议值，不写库；确认后由前端走常规保存 */
  suggestGroupField(uid: string, field: string) {
    return http.post<FieldSuggestResult>(`/db/groups/${uid}/suggest-field`, { field }).then((r) => r.data)
  },
  suggestArtistField(uid: string, field: string) {
    return http.post<FieldSuggestResult>(`/db/artists/${uid}/suggest-field`, { field }).then((r) => r.data)
  },
}

export interface ArtistSourceDiffItem {
  field: string
  label: string
  current?: string | null
  proposed: string
  sources: string[]
  source_urls: string[]
  conflict: boolean
  is_new: boolean
  same?: boolean
}

export interface ArtistSourcePreview {
  sources: { source_type: string; label: string; url: string }[]
  items: ArtistSourceDiffItem[]
  extra: Record<string, unknown>
  errors: string[]
}

export interface ArtistApplyResult {
  fields: string[]
  fields_skipped_locked: string[]
}

export const artistGrowthApi = {
  /** 艺人生长预览：urls 为空时按名称自动检索 Fandom/维基/TMDB */
  preview(uid: string, urls: string[]) {
    return http
      .post<ArtistSourcePreview>(`/db/artists/${uid}/source/preview`, { urls }, { timeout: 180_000 })
      .then((r) => r.data)
  },
  apply(uid: string, fields: Record<string, string>, sourceUrls: string[]) {
    return http
      .post<{ applied: ArtistApplyResult }>(
        `/db/artists/${uid}/source/apply`,
        { fields, source_urls: sourceUrls },
        { timeout: 120_000 },
      )
      .then((r) => r.data)
  },
  /** solo 专辑生长：iTunes / Deezer / MusicBrainz 聚合检索 */
  suggestAlbums(uid: string) {
    return http
      .post<{ albums: AlbumProposal[]; note: string | null }>(
        `/db/artists/${uid}/albums/suggest`,
        {},
        { timeout: 180_000 },
      )
      .then((r) => r.data)
  },
  applyAlbums(uid: string, albums: AlbumProposal[]) {
    return http
      .post<{
        albums_created: number
        albums_updated: number
        albums_skipped: number
        songs_created: number
        tracks_created: number
        tracklist_failed: number
      }>(
        `/db/artists/${uid}/albums/apply`,
        { albums, include_tracks: true },
        { timeout: 180_000 },
      )
      .then((r) => r.data)
  },
}

export interface AlbumProposal {
  action: 'create' | 'update' | 'exists' | 'fill_tracks' | string
  name: string
  year?: string | null
  release_date?: string | null
  record_type?: string | null
  track_count?: number | null
  external_id?: string | null
  cover_url?: string | null
  sources: string[]
  album_id?: number | null
  patch?: Record<string, string>
}

export interface GroupWorkspaceResponse {
  group: {
    id: number
    uid: string
    name: string
    chinese_name?: string | null
    english_name?: string | null
    korean_name?: string | null
    debut_date?: string | null
    group_type?: string | null
    gender_type?: string | null
    origin_country?: string | null
    description?: string | null
    tagline?: string | null
    avatar_path?: string | null
    banner_path?: string | null
    social_media?: Record<string, string> | null
    hide_from_home?: boolean
    external_links: { url?: string; source?: string; [k: string]: unknown }[]
  }
  row: DbGroupRow | null
  sub_units: {
    uid: string
    id: number
    name: string
    group_type?: string | null
    completeness: number
    members_active: number
    member_ids: number[]
  }[]
  albums: {
    id: number
    uid: string
    name: string
    release_date?: string | null
    album_type?: string | null
    track_count: number
    video_count: number
  }[]
  issues: {
    check: string
    severity: 'error' | 'warning' | 'hint'
    entity_type: string
    entity_id: number
    title: string
    detail: string
    suggestion: string
    deep_link?: string | null
  }[]
  issue_counts: { error: number; warning: number; hint: number }
  completeness_checklist: string[]
}

export const dbViewsApi = {
  getGroupRows() {
    return http.get<GroupListResponse>('/db/groups').then((r) => r.data)
  },
  getArtistRows() {
    return http.get<ArtistListResponse>('/db/artists').then((r) => r.data)
  },
  getAlbumRows() {
    return http.get<AlbumListResponse>('/db/albums').then((r) => r.data)
  },
  getSongRows() {
    return http.get<SongListResponse>('/db/songs').then((r) => r.data)
  },
  getGroupWorkspace(uid: string) {
    return http.get<GroupWorkspaceResponse>(`/db/groups/${uid}/workspace`).then((r) => r.data)
  },
  searchSourceCandidates(uid: string, q: string) {
    return http
      .get<{ items: { source_type: string; title: string; snippet: string; url: string }[] }>(
        `/db/groups/${uid}/source/search`,
        { params: { q } },
      )
      .then((r) => r.data)
  },
  /** step=4 / member_details 时 urls 可为空：后端按在籍成员搜索 Fandom 补全档案 */
  previewSource(uid: string, urls: string[], step?: string | number, targets?: string[]) {
    return http
      .post<{
        sources: { source_type: string; label: string; url: string }[]
        items: {
          field: string
          label: string
          current?: string | null
          proposed: string
          sources: string[]
          source_urls: string[]
          conflict: boolean
          is_new: boolean
        }[]
        member_proposals: {
          action: 'create' | 'link' | 'exists' | 'enrich'
          name: string
          korean_name?: string | null
          join_date?: string | null
          leave_date?: string | null
          status: string
          sources: string[]
          artist_id?: number | null
          artist_uid?: string | null
          positions?: string[] | null
          artist_fields?: {
            birth_date?: string | null
            chinese_name?: string | null
            english_name?: string | null
            stage_name?: string | null
            birth_place?: string | null
            occupation?: string | null
            description?: string | null
          }
          /** 字段来源：ai | fandom | wikidata | wikipedia | baidu | crawl */
          field_origins?: Record<string, string>
          ai_basis?: string | null
          possible_duplicate_of?: string[]
        }[]
        subunit_proposals: {
          action: 'create' | 'exists'
          name: string
          member_names: string[]
          sources: string[]
        }[]
        company_proposals: {
          action: 'create' | 'link' | 'exists'
          name: string
          company_id?: number | null
          role?: string | null
          sources: string[]
        }[]
        album_proposals: {
          action: 'create' | 'exists' | 'fill_tracks' | 'update'
          name: string
          year?: string | null
          release_date?: string | null
          track_count?: number | null
          external_id?: string | null
          cover_url?: string | null
          sources: string[]
          album_id?: number | null
          db_release_date?: string | null
          patch?: Record<string, unknown>
          tracks_preview?: { name?: string; track_number?: number; disc_number?: number }[]
          candidates?: {
            external_id?: string | null
            name?: string | null
            artist?: string | null
            year?: string | null
            track_count?: number | null
            cover_url?: string | null
            source?: string | null
          }[]
          needs_tracks?: boolean
        }[]
        extra: {
          members?: { name?: string; join_date?: string | null; leave_date?: string | null }[]
          company_raw?: string
          fandom_name?: string
          biography?: string
          thumb?: string
          description?: string
          step?: string | null
          baidu_note?: string | null
          ai_gap_fill?: {
            status?: 'skipped' | 'ok' | 'error' | string
            filled_fields?: number
            members_touched?: number
            error?: string | null
          } | null
          [k: string]: unknown
        }
        errors: string[]
      }>(
        `/db/groups/${uid}/source/preview`,
        {
          urls,
          step: step != null ? String(step) : undefined,
          targets: targets?.length ? targets : undefined,
        },
        {
          timeout:
            String(step) === '4' || String(step) === 'member_details' ? 300_000 : 180_000,
        },
      )
      .then((r) => r.data)
  },
  previewTracklist(uid: string, albumId: number, externalId: string) {
    return http
      .post<{
        album_id: number
        album_name: string
        external_id: string
        release_date?: string | null
        tracks_preview: { name?: string; track_number?: number; disc_number?: number }[]
      }>(
        `/db/groups/${uid}/source/tracklist-preview`,
        { album_id: albumId, external_id: externalId },
        { timeout: 120_000 },
      )
      .then((r) => r.data)
  },
  applySource(
    uid: string,
    payload: {
      fields: Record<string, string>
      members: Record<string, unknown>[]
      albums: Record<string, unknown>[]
      subunits: Record<string, unknown>[]
      companies?: Record<string, unknown>[]
      source_urls: string[]
      step?: string | number
    },
  ) {
    const { step, ...rest } = payload
    return http
      .post<{ applied: GrowthApplyResult }>(
        `/db/groups/${uid}/source/apply`,
        { ...rest, step: step != null ? String(step) : undefined },
        { timeout: 180_000 },
      )
      .then((r) => r.data)
  },
}

export interface GrowthApplyResult {
  fields: string[]
  fields_skipped_locked: string[]
  members_created: number
  members_linked: number
  members_skipped: number
  albums_created: number
  albums_updated?: number
  albums_skipped: number
  songs_created: number
  tracks_created: number
  tracklist_failed: number
  subunits_created: number
  subunit_members_created: number
  companies_created?: number
  companies_linked?: number
  companies_skipped?: number
  artist_fields_filled: number
  albums_year_only?: number
  note_year_only_albums?: string
}

export interface ArtistWorkspaceResponse {
  artist: {
    id: number
    uid: string
    name: string
    korean_name?: string | null
    chinese_name?: string | null
    english_name?: string | null
    stage_name?: string | null
    birth_date?: string | null
    birth_place?: string | null
    gender?: string | null
    debut_date?: string | null
    occupation?: string | null
    description?: string | null
    tagline?: string | null
    avatar_path?: string | null
    banner_path?: string | null
    social_media?: Record<string, string> | null
    hide_from_home?: boolean
    external_links: { url?: string; source?: string; [k: string]: unknown }[]
  }
  memberships: {
    membership_id: number
    group_id: number
    group_uid: string
    group_name: string
    group_type?: string | null
    join_date?: string | null
    leave_date?: string | null
    status: string
    positions: string[]
  }[]
  companies: {
    id: number
    company_id: number
    company_name: string
    role?: string | null
    status: string
    start_date?: string | null
    end_date?: string | null
  }[]
  albums: {
    id: number
    uid: string
    name: string
    release_date?: string | null
    album_type?: string | null
    track_count: number
    video_count: number
  }[]
  songs: {
    id: number
    uid: string
    name: string
    chinese_name?: string | null
    release_date?: string | null
    duration?: number | null
  }[]
  songs_count: number
  videos: {
    id: number
    uid: string
    name: string
    video_type?: string | null
  }[]
  completeness: number
  completeness_missing: string[]
  issues: {
    severity: 'error' | 'warning' | 'hint'
    title: string
    suggestion: string
  }[]
  issue_counts: Record<string, number>
  completeness_checklist: string[]
}

export const artistWorkspaceApi = {
  getArtistWorkspace(uid: string) {
    return http.get<ArtistWorkspaceResponse>(`/db/artists/${uid}/workspace`).then((r) => r.data)
  },
}
