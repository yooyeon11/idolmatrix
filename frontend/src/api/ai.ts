import http from './index'
import type {
  AiAnalyzeRequest,
  AiAnalyzeResult,
  AiSearchAlbumRequest,
  AiSearchAlbumResult,
  AiSuggestRequest,
  AiSuggestResult,
} from '@/types/models'

export interface AiEntityTaglinePayload {
  provider: string
  base_url: string
  api_key?: string
  model: string
  prompt?: string
  entity_type: 'artist' | 'group'
  entity_id: number
}

export const aiApi = {
  suggest(payload: AiSuggestRequest) {
    return http
      .post<AiSuggestResult>('/ai/suggest', payload, { timeout: 120_000 })
      .then((r) => r.data)
  },
  searchAlbums(payload: AiSearchAlbumRequest) {
    return http
      .post<AiSearchAlbumResult>('/ai/search-albums', payload, { timeout: 120_000 })
      .then((r) => r.data)
  },
  analyzeEntity(payload: AiAnalyzeRequest) {
    return http
      .post<AiAnalyzeResult>('/ai/analyze-entity', payload, { timeout: 120_000 })
      .then((r) => r.data)
  },
  entityTagline(payload: AiEntityTaglinePayload) {
    return http
      .post<{ tagline: string }>('/ai/entity-tagline', payload, { timeout: 120_000 })
      .then((r) => r.data)
  },
  suggestSocial(payload: AiSuggestSocialPayload) {
    return http
      .post<AiSuggestSocialResult>('/ai/suggest-social', payload, { timeout: 120_000 })
      .then((r) => r.data)
  },
}


export interface AiSuggestSocialPayload {
  entity_type: 'artist' | 'group'
  entity_id: number
  provider?: string
  base_url?: string
  api_key?: string
  model?: string
  use_saved_ingest_ai?: boolean
}

export interface AiSuggestSocialResult {
  links: Record<string, string>
  basis?: string
  sources?: { field?: string; value?: string; source?: string }[]
}
