import http from './index'
import type { Company } from '@/types/models'

export interface CompanyRelation {
  id: number
  company_id: number
  company_name: string
  role?: string | null
  status: string
  start_date?: string | null
  end_date?: string | null
}

export interface CompanyRelationPatch {
  role?: string | null
  status?: string
  start_date?: string | null
  end_date?: string | null
}

/** 组合/艺人与公司的关系行增删改（资料库工作台用）。 */
export const companyRelationsApi = {
  /** 公司模糊搜索（添加关系时选公司用）；空关键字返回前 50 家，保证点开下拉就有候选 */
  async searchCompanies(q: string): Promise<Company[]> {
    return http.get<Company[]>('/companies/brief', { params: { q: q.trim() || undefined } }).then((r) => r.data)
  },

  async createForGroup(payload: {
    group_id: number
    company_id: number
    role?: string | null
    status?: string
    start_date?: string | null
    end_date?: string | null
  }) {
    return http.post<CompanyRelation>('/company-relations/group', payload).then((r) => r.data)
  },
  updateForGroup(relationId: number, patch: CompanyRelationPatch) {
    return http
      .patch<CompanyRelation>(`/company-relations/group/${relationId}`, patch)
      .then((r) => r.data)
  },
  async removeForGroup(relationId: number) {
    await http.delete(`/company-relations/group/${relationId}`)
  },

  async createForArtist(payload: {
    artist_id: number
    company_id: number
    role?: string | null
    status?: string
    start_date?: string | null
    end_date?: string | null
  }) {
    return http.post<CompanyRelation>('/company-relations/artist', payload).then((r) => r.data)
  },
  updateForArtist(relationId: number, patch: CompanyRelationPatch) {
    return http
      .patch<CompanyRelation>(`/company-relations/artist/${relationId}`, patch)
      .then((r) => r.data)
  },
  async removeForArtist(relationId: number) {
    await http.delete(`/company-relations/artist/${relationId}`)
  },
}
