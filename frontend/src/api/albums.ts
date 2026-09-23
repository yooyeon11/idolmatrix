import http from './index'
import type {
  Album,
  AlbumAlignTracksResult,
  AlbumTrack,
  AlbumBrief,
  AlbumFuzzyHit,
  AlbumImportPreviewItem,
  AlbumImportTrackItem,
  AlbumImportTracksResult,
  AlbumTrackDetail,
  CoverSearchItem,
  Pagination,
} from '@/types/models'

export interface ListParams {
  q?: string
  album_type?: string
  sort?: string
  page?: number
  page_size?: number
}

export const albumsApi = {
  list(params: ListParams = {}) {
    return http.get<Pagination<Album>>('/albums', { params }).then((r) => r.data)
  },
  /**
   * 专辑精简搜索。
   * @param q 关键字
   * @param songHits 同时按「专辑内曲目名」匹配 —— 只记得歌名时也能找到专辑，
   *                 命中项会带 `matched_song_*`（主体 / 歌曲名）
   */
  brief(q?: string, songHits = false) {
    return http
      .get<AlbumBrief[]>('/albums/brief', { params: { q, song_hits: songHits || undefined } })
      .then((r) => r.data)
  },
  fuzzy(q: string) {
    return http.get<AlbumFuzzyHit[]>('/albums/fuzzy', { params: { q } }).then((r) => r.data)
  },
  songAlbums(songId: number) {
    return http.get<AlbumBrief[]>(`/songs/${songId}/albums`).then((r) => r.data)
  },
  get(id: number) {
    return http.get<Album>(`/albums/${id}`).then((r) => r.data)
  },
  getByUid(uid: string) {
    return http.get<Album>(`/albums/by-uid/${encodeURIComponent(uid)}`).then((r) => r.data)
  },
  tracks(id: number) {
    return http.get<AlbumTrackDetail[]>(`/albums/${id}/tracks`).then((r) => r.data)
  },
  addTrack(payload: { album_id: number; song_id: number; disc_number?: number; track_number: number }) {
    return http.post<AlbumTrack>('/albums/tracks', payload).then((r) => r.data)
  },
  removeTrack(trackId: number) {
    return http.delete(`/albums/tracks/${trackId}`).then((r) => r.data)
  },
  create(payload: Partial<Album>) {
    return http.post<Album>('/albums', payload).then((r) => r.data)
  },
  update(id: number, payload: Partial<Album>) {
    return http.patch<Album>(`/albums/${id}`, payload).then((r) => r.data)
  },
  remove(id: number) {
    return http.delete(`/albums/${id}`).then((r) => r.data)
  },
  coverSearch(query: string, source: 'netease' | 'itunes') {
    return http.post<CoverSearchItem[]>('/albums/covers/search', { query, source }).then((r) => r.data)
  },
  coverApply(id: number, coverUrl: string) {
    return http.post<Album>(`/albums/${id}/cover`, { cover_url: coverUrl }).then((r) => r.data)
  },
  coverUrl(id: number) {
    return `/api/albums/${id}/cover`
  },
  coverRemove(id: number) {
    return http.delete<Album>(`/albums/${id}/cover`).then((r) => r.data)
  },
  importPreview(id: number, tracks: AlbumImportTrackItem[]) {
    return http
      .post<AlbumImportPreviewItem[]>(`/albums/${id}/tracks/import-preview`, tracks)
      .then((r) => r.data)
  },
  importTracks(id: number, source: string | null, tracks: AlbumImportTrackItem[]) {
    return http
      .post<AlbumImportTracksResult>(`/albums/${id}/tracks/import`, { source, tracks })
      .then((r) => r.data)
  },
  alignExternal(id: number, tracks: AlbumImportTrackItem[]) {
    return http
      .post<AlbumAlignTracksResult>(`/albums/${id}/tracks/align-external`, { tracks })
      .then((r) => r.data)
  },
}
