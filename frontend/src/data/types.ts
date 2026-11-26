/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

export type EvidenceLine = {
  序号: number
  样本指纹: string
  来源: string
  结果: string
  补报时间?: string
  拒收?: boolean
  拒收原因?: string
}

export type EvidenceDownloadRecord = {
  交付批次: string
  下载时间: string
  版本?: string
  样本数: number
  命中数: number
  失配数: number
  指纹摘要?: string
}

export type EvidenceDetail = EntryRow & {
  明细?: EvidenceLine[]
  补报记录?: Record<string, unknown>[]
  下载记录?: EvidenceDownloadRecord[]
  明细范围?: string
  命中率?: string
  分母核对?: {
    ok: boolean
    message: string
    requested: number
    detail_total: number
    supplemented: number
    hit: number
    miss: number
    rate: string
    conflict: boolean
    conflicts: string[]
    rejected_supplements: number
    version_bound: string
    site_bound: string
  }
}

export type EvidenceVerify = {
  ok: boolean
  message: string
  batch?: string
  version?: string
  site?: string
  download_total?: number
  snapshot_count?: number
  file_total?: number
  hit?: number
  miss?: number
  rate?: string
  recomputable?: boolean
  unknown_fingerprints?: string[]
}

export type EvidenceReviewResult = {
  ok: boolean
  message: string
  report_id?: number
  measured?: number
  published?: number
  diff?: number
  hit?: number
  miss?: number
  rate?: string
  batch?: string
  version?: string
}
