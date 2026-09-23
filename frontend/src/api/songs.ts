import http from './index'
import type { Pagination, Song, SongBrief, SongFuzzyHit } from '@/types/models'

export interface ListParams {
  q?: string
  song_type?: string
  artist_id?: number
  group_id?: number
  album_id?: number
  sort?: string
  page?: number
  page_size?: number
  only_with_videos?: boolean
  filter?: 'all' | 'linked' | 'orphan'
}

export interface SongBriefOptions {
  /** q 额外匹配英文名 / 韩文名 / 别名 —— 只知道别名/原名时也能找到歌 */
  deep?: boolean
  /** 结果带 `owner`（主体展示名）；同名歌曲（I AM / Supernova）靠它区分 */
  withOwner?: boolean
  /** q 额外匹配「所属专辑名」，命中项带 `matched_album_*` */
  albumHits?: boolean
}

export const songsApi = {
  list(params: ListParams = {}) {
    return http.get<Pagination<Song>>('/songs', { params }).then((r) => r.data)
  },
  /**
   * 歌曲精简搜索。
   * @param q 关键字
   * @param opts 三个开关默认全关 —— 只按歌曲名 / 中文名匹配，AI 关联等老调用行为不变；
   *             下拉「手动找歌」建议三个都开（别名/韩文名/专辑名都能搜，且带主体名消歧）
   */
  brief(q?: string, opts: SongBriefOptions = {}) {
    return http
      .get<SongBrief[]>('/songs/brief', {
        params: {
          q,
          deep: opts.deep || undefined,
          with_owner: opts.withOwner || undefined,
          album_hits: opts.albumHits || undefined,
        },
      })
      .then((r) => r.data)
  },
  fuzzy(q: string) {
    return http.get<SongFuzzyHit[]>('/songs/fuzzy', { params: { q } }).then((r) => r.data)
  },
  get(id: number) {
    return http.get<Song>(`/songs/${id}`).then((r) => r.data)
  },
  getByUid(uid: string) {
    return http.get<Song>(`/songs/by-uid/${encodeURIComponent(uid)}`).then((r) => r.data)
  },
  create(payload: Partial<Song>) {
    return http.post<Song>('/songs', payload).then((r) => r.data)
  },
  update(id: number, payload: Partial<Song>) {
    return http.patch<Song>(`/songs/${id}`, payload).then((r) => r.data)
  },
  remove(id: number) {
    return http.delete(`/songs/${id}`).then((r) => r.data)
  },

  // ===== 演出者关系（SongArtistRelation）=====

  listRelations(songId: number) {
    return http
      .get<SongArtistRelationRead[]>(`/songs/${songId}/relations`)
      .then((r) => r.data)
  },
  createRelation(payload: {
    song_id: number
    artist_id?: number | null
    group_id?: number | null
    role?: string
    order?: number
  }) {
    return http
      .post<SongArtistRelationRead>('/songs/relations', payload)
      .then((r) => r.data)
  },
  removeRelation(relationId: number) {
    return http.delete(`/songs/relations/${relationId}`)
  },

  // ===== 创作人员（Credits）=====

  listCredits(songId: number) {
    return http.get<CreditsRead[]>(`/songs/${songId}/credits`).then((r) => r.data)
  },
  createCredit(
    songId: number,
    payload: { name: string; role: string; artist_id?: number | null },
  ) {
    return http
      .post<CreditsRead>('/songs/credits', { song_id: songId, ...payload })
      .then((r) => r.data)
  },
  removeCredit(creditId: number) {
    return http.delete(`/songs/credits/${creditId}`)
  },
}

export interface SongArtistRelationRead {
  id: number
  song_id: number
  artist_id?: number | null
  group_id?: number | null
  role: string
  order: number
}

export interface CreditsRead {
  id: number
  song_id: number
  artist_id?: number | null
  name: string
  role: string
}
