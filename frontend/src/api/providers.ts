import http from './index'
import type { AlbumExternalCandidate, AlbumTracklist, ProviderDetail, ProviderSearchItem } from '@/types/models'

export const providersApi = {
  searchAudiodb(q: string, type: 'artist' | 'group') {
    return http
      .get<ProviderSearchItem[]>('/providers/audiodb/search', {
        params: { q, type },
      })
      .then((r) => r.data)
  },
  audiodbDetail(externalId: string) {
    return http
      .get<ProviderDetail>(`/providers/audiodb/${encodeURIComponent(externalId)}`)
      .then((r) => r.data)
  },
  albumSearch(q: string, source: 'itunes' | 'deezer') {
    return http
      .get<AlbumExternalCandidate[]>('/providers/albums/search', {
        params: { q, source },
      })
      .then((r) => r.data)
  },
  albumTracklist(externalId: string) {
    return http
      .get<AlbumTracklist>(`/providers/albums/${encodeURIComponent(externalId)}/tracklist`)
      .then((r) => r.data)
  },
  tmdbTest(apiKey: string) {
    return http.post<{ ok: boolean }>('/providers/tmdb/test', { api_key: apiKey }).then((r) => r.data)
  },
}
