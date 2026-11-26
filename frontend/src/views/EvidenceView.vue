<template>
  <section class="page" data-module="parse_evidence">
    <header class="page-head">
      <div>
        <h2>证据交付单</h2>
        <p class="page-desc">按审计窗口申请站点样本证据包，交付给采集量指标复核；包内总量始终等于实际样本明细数。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="apply-evidence" type="button" @click="openApply">
          申请证据导出
        </button>
        <button class="btn" data-testid="refresh" type="button" @click="reload">刷新</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">交付单总数</span>
        <strong class="stat-value">{{ total }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">可复核</span>
        <strong class="stat-value">{{ countsByStatus['可复核'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已复核</span>
        <strong class="stat-value">{{ countsByStatus['已复核'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待下载</span>
        <strong class="stat-value">{{ countsByStatus['待下载'] ?? 0 }}</strong>
      </article>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>申请单号</th>
          <th>站点 / 模板版本</th>
          <th>窗口</th>
          <th>申请分母</th>
          <th>交付分母</th>
          <th>命中 / 失配</th>
          <th>命中率</th>
          <th>交付状态</th>
          <th>明细范围</th>
          <th>关联指标复核</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.id"
          :data-testid="`evidence-${row.id}`"
          :data-status="String(row.status)"
          :class="{ selected: detail?.id === row.id }"
          @click="selectRow(row)"
        >
          <td>{{ row['申请单号'] }}</td>
          <td>{{ row['站点名称'] }} / {{ row['模板版本'] }}</td>
          <td>{{ row['窗口标识'] }}</td>
          <td>{{ row['申请样本数'] }}</td>
          <td>{{ row['交付样本数'] || '—' }}</td>
          <td>{{ row['命中样本数'] || '0' }} / {{ row['失配样本数'] || '0' }}</td>
          <td>{{ row['命中率'] || '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="scope-cell">{{ row['明细范围'] }}</td>
          <td>
            <span v-if="row['指标复核记录']" class="link-text">#{{ row['指标复核记录'] }}</span>
            <button
              class="link"
              type="button"
              :data-testid="`goto-metric-${row.id}`"
              @click.stop="gotoMetric(row)"
            >
              去指标页复核
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="10" class="empty-state">暂无证据交付单，可先从解析质量审计窗口申请证据导出</td>
        </tr>
      </tbody>
    </table>

    <section v-if="detail" class="detail-panel" data-testid="evidence-detail">
      <header class="detail-head">
        <h3>{{ detail['申请单号'] }} · {{ detail['站点名称'] }} / {{ detail['模板名称'] }}（{{ detail['模板版本'] }}）</h3>
        <span class="status-chip">{{ detail.status }}</span>
      </header>

      <div class="detail-grid">
        <div>
          <span class="stat-label">窗口</span>
          <strong>{{ detail['窗口标识'] }}</strong>
        </div>
        <div>
          <span class="stat-label">申请分母</span>
          <strong>{{ detail['申请样本数'] }}</strong>
        </div>
        <div>
          <span class="stat-label">验收分母（明细数）</span>
          <strong data-testid="acceptance-denominator">{{ detail['交付样本数'] || '0' }}</strong>
        </div>
        <div>
          <span class="stat-label">命中 / 失配</span>
          <strong>{{ detail['命中样本数'] || '0' }} / {{ detail['失配样本数'] || '0' }}</strong>
        </div>
        <div>
          <span class="stat-label">命中率</span>
          <strong>{{ detail['命中率'] || '—' }}</strong>
        </div>
        <div>
          <span class="stat-label">指纹摘要</span>
          <strong>{{ detail['指纹摘要'] || '—' }}</strong>
        </div>
      </div>

      <p class="scope-line" data-testid="detail-scope">{{ detail['明细范围'] }}</p>

      <p
        v-if="detail['分母核对']"
        class="reconcile-banner"
        :class="detail['分母核对'].conflict ? 'warn' : 'ok'"
        data-testid="reconcile-banner"
      >
        {{ detail['分母核对'].message }}
        <template v-if="detail['分母核对'].conflict">
          · 申请 {{ detail['分母核对'].requested }} / 明细 {{ detail['分母核对'].detail_total }}
          · 补报增量 {{ detail['分母核对'].supplemented }}
        </template>
        <template v-else-if="detail['分母核对'].rejected_supplements">
          · 已拒收补报 {{ detail['分母核对'].rejected_supplements }} 条（留档不进包）
        </template>
      </p>

      <div class="detail-actions">
        <button class="btn" type="button" data-testid="confirm-export" :disabled="busy" @click="confirmExport(true)">
          确认继续导出
        </button>
        <button class="btn" type="button" :disabled="busy" @click="confirmExport(false)">
          拒绝补报 / 按原分母
        </button>
        <button class="btn primary" type="button" data-testid="download-package" :disabled="busy" @click="download">
          下载证据包
        </button>
        <button class="btn" type="button" data-testid="verify-file" :disabled="busy" @click="verifyFile">
          按下载文件核对
        </button>
        <button class="btn" type="button" data-testid="link-review" :disabled="busy" @click="linkReview">
          关联指标复核
        </button>
      </div>

      <form class="supplement-form" data-testid="supplement-form" @submit.prevent="submitSupplement">
        <label>
          <span>补报样本指纹</span>
          <input v-model="supplement.fingerprint" data-testid="supplement-fingerprint" placeholder="留空按窗口续号生成" />
        </label>
        <label>
          <span>补报结果</span>
          <select v-model="supplement.result" data-testid="supplement-result">
            <option value="命中">命中</option>
            <option value="失配">失配</option>
          </select>
        </label>
        <button class="btn" type="submit" data-testid="supplement-submit" :disabled="busy">
          补报一项
        </button>
        <span class="error-text" v-if="message">{{ message }}</span>
      </form>

      <table class="data-table compact">
        <thead>
          <tr><th>交付批次</th><th>下载时间</th><th>版本</th><th>样本数</th><th>命中</th><th>失配</th><th>指纹摘要</th></tr>
        </thead>
        <tbody>
          <tr v-for="record in detail['下载记录'] || []" :key="record['交付批次']">
            <td>{{ record['交付批次'] }}</td>
            <td>{{ record['下载时间'] }}</td>
            <td>{{ record['版本'] }}</td>
            <td>{{ record['样本数'] }}</td>
            <td>{{ record['命中数'] }}</td>
            <td>{{ record['失配数'] }}</td>
            <td>{{ record['指纹摘要'] }}</td>
          </tr>
          <tr v-if="!(detail['下载记录'] || []).length">
            <td colspan="7" class="empty-state">尚未下载，下载后才会生成交付批次</td>
          </tr>
        </tbody>
      </table>

      <div v-if="verifyResult" class="verify-box" data-testid="verify-result">
        <strong>下载文件核对：{{ verifyResult.message }}</strong>
        <span>批次 {{ verifyResult.batch }} · 版本 {{ verifyResult.version }} · 文件 {{ verifyResult.file_total }} 条 · 快照 {{ verifyResult.snapshot_count }} 条 · 命中 {{ verifyResult.hit }} · 失配 {{ verifyResult.miss }} · 命中率 {{ verifyResult.rate }}</span>
        <span>可复算：{{ verifyResult.recomputable ? '是' : '否' }}</span>
      </div>

      <details class="detail-lines" v-if="(detail['明细'] || []).length">
        <summary>证据明细（{{ detail['明细']?.length }} 条）</summary>
        <table class="data-table compact">
          <thead>
            <tr><th>序号</th><th>样本指纹</th><th>来源</th><th>结果</th><th>补报时间</th><th>是否进包</th></tr>
          </thead>
          <tbody>
            <tr v-for="line in detail['明细'] || []" :key="line['样本指纹']">
              <td>{{ line['序号'] }}</td>
              <td>{{ line['样本指纹'] }}</td>
              <td>{{ line['来源'] }}</td>
              <td>{{ line['结果'] }}</td>
              <td>{{ line['补报时间'] || '—' }}</td>
              <td>{{ line['拒收'] ? '拒收（留档）' : '进包' }}</td>
            </tr>
          </tbody>
        </table>
      </details>
    </section>

    <div v-if="showApply" class="modal-backdrop" data-testid="apply-modal">
      <form class="modal" @submit.prevent="submitApply">
        <h3>申请证据导出</h3>
        <label v-for="field in applyFields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="applyValues[field]" :data-testid="`apply-${field}`" :required="field !== '命中样本数'" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showApply = false">取消</button>
          <button class="btn primary" type="submit" data-testid="apply-submit" :disabled="busy">
            {{ busy ? '提交中…' : '确认申请' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 张证据交付单</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  applyEvidencePackage,
  confirmEvidence,
  downloadEvidencePackage,
  getEvidence,
  listEvidence,
  linkMetricToReview,
  supplementEvidence,
  verifyEvidenceFile,
} from '@/api/evidence-service'
import type {
  EntryRow,
  EvidenceDetail,
  EvidenceVerify,
} from '@/data/types'

const router = useRouter()
const route = useRoute()
const rows = ref<EvidenceDetail[]>([])
const detail = ref<EvidenceDetail | null>(null)
const total = ref(0)
const busy = ref(false)
const message = ref('')
const errorMessage = ref('')
const verifyResult = ref<EvidenceVerify | null>(null)
const showApply = ref(false)

const applyFields = ['申请单号', '站点名称', '模板名称', '模板版本', '窗口标识', '申请样本数', '命中样本数']
const applyValues = reactive<Record<string, string>>({
  申请单号: '',
  站点名称: '',
  模板名称: '',
  模板版本: '',
  窗口标识: '',
  申请样本数: '20',
  命中样本数: '',
})
const supplement = reactive({ fingerprint: '', result: '命中' })

const countsByStatus = computed(() => {
  const counts: Record<string, number> = {}
  for (const row of rows.value) {
    const status = String(row.status)
    counts[status] = (counts[status] ?? 0) + 1
  }
  return counts
})

function openApply() {
  errorMessage.value = ''
  showApply.value = true
}

async function submitApply() {
  busy.value = true
  errorMessage.value = ''
  try {
    const entry = await applyEvidencePackage({ ...applyValues })
    showApply.value = false
    await reload()
    await selectRow(entry)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '证据包申请失败'
  } finally {
    busy.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEvidence()
    rows.value = payload.items as EvidenceDetail[]
    total.value = payload.total
    if (detail.value) {
      const still = payload.items.find((item) => item.id === detail.value?.id)
      if (still) detail.value = await getEvidence(Number(still.id))
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '证据交付单读取失败'
  }
}

async function selectRow(row: EntryRow | EvidenceDetail) {
  message.value = ''
  verifyResult.value = null
  try {
    detail.value = await getEvidence(Number(row.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交付单详情读取失败'
  }
}

async function confirmExport(acceptSupplement: boolean) {
  if (!detail.value) return
  busy.value = true
  message.value = ''
  try {
    const result = await confirmEvidence(Number(detail.value.id), !acceptSupplement)
    message.value = result.message
    await reload()
    await refreshDetail()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '确认导出失败'
  } finally {
    busy.value = false
  }
}

async function submitSupplement() {
  if (!detail.value) return
  busy.value = true
  message.value = ''
  try {
    const result = await supplementEvidence(Number(detail.value.id), {
      样本指纹: supplement.fingerprint,
      结果: supplement.result,
    })
    message.value = result.message
    supplement.fingerprint = ''
    await reload()
    await refreshDetail()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '补报失败'
  } finally {
    busy.value = false
  }
}

async function refreshDetail() {
  if (!detail.value) return
  detail.value = await getEvidence(Number(detail.value.id))
}

async function download() {
  if (!detail.value) return
  busy.value = true
  message.value = ''
  try {
    const { filename, text } = await downloadEvidencePackage(Number(detail.value.id))
    await refreshDetail()
    const batch = detail.value?.['下载记录']?.slice(-1)[0]?.['交付批次'] ?? ''
    // 实际下载文件留存到会话，指标页复核只认这份文件，不回指标表重取。
    sessionStorage.setItem(`evidence-file:${detail.value?.id}`, JSON.stringify({ filename, batch, text }))
    message.value = `已下载 ${filename}，可在指标页按该文件复核（批次 ${batch}）`
  } catch (error) {
    message.value = error instanceof Error ? error.message : '证据包下载失败'
  } finally {
    busy.value = false
  }
}

function currentFile(): string {
  if (!detail.value) return ''
  const raw = sessionStorage.getItem(`evidence-file:${detail.value.id}`)
  if (!raw) return ''
  try {
    return (JSON.parse(raw) as { text?: string }).text ?? ''
  } catch {
    return ''
  }
}

async function verifyFile() {
  if (!detail.value) return
  const text = currentFile()
  if (!text) {
    message.value = '请先下载证据包，验收端只按实际下载文件复核'
    return
  }
  busy.value = true
  try {
    verifyResult.value = await verifyEvidenceFile(Number(detail.value.id), text)
    message.value = verifyResult.value.message
  } catch (error) {
    message.value = error instanceof Error ? error.message : '证据文件核对失败'
  } finally {
    busy.value = false
  }
}

async function linkReview() {
  if (!detail.value) return
  busy.value = true
  message.value = ''
  try {
    const text = currentFile()
    if (!text) {
      message.value = '请先下载证据包，验收端只按实际下载文件复核'
      return
    }
    const result = await linkMetricToReview(Number(detail.value.id), text)
    message.value = `${result.message}（指标记录 #${result.metric_id ?? '—'}）`
    await reload()
    await refreshDetail()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '关联指标复核失败'
  } finally {
    busy.value = false
  }
}

function gotoMetric(row: EvidenceDetail) {
  const stored = sessionStorage.getItem(`evidence-file:${row.id}`)
  let batch = ''
  if (stored) {
    try {
      batch = (JSON.parse(stored) as { batch?: string }).batch ?? ''
    } catch {
      batch = ''
    }
  }
  // 指标页只认这份实际下载文件；没有下载记录时只带交付单，让指标页提示先下载。
  void router.push({
    path: '/collect_metrics/review',
    query: { evidence: String(row.id), batch },
  })
}

onMounted(async () => {
  await reload()
  // 从审计页跳进来时带着 evidence 查询参数，直接展开对应交付单。
  const wanted = Number(route.query.evidence ?? 0)
  const target = rows.value.find((row) => Number(row.id) === wanted) ?? rows.value[0]
  if (target) await selectRow(target)
})
</script>

<style scoped>
.selected { background: #eef5ff; }
.scope-cell { max-width: 320px; color: var(--muted); font-size: 12px; }
.detail-panel { margin-top: 16px; background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
.detail-head { display: flex; justify-content: space-between; align-items: center; }
.detail-head h3 { margin: 0 0 8px; font-size: 15px; }
.status-chip { background: #eef2f7; border-radius: 999px; padding: 2px 10px; font-size: 12px; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin: 12px 0; }
.detail-grid strong { display: block; font-size: 15px; }
.scope-line { font-size: 12px; color: var(--muted); background: #f8fafc; border-radius: 6px; padding: 8px 10px; }
.reconcile-banner { font-size: 12px; padding: 8px 10px; border-radius: 6px; }
.reconcile-banner.ok { background: #ecfdf3; color: #166534; }
.reconcile-banner.warn { background: #fef3c7; color: #92400e; }
.detail-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.supplement-form { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-bottom: 12px; }
.supplement-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.supplement-form input, .supplement-form select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; }
.verify-box { display: flex; flex-direction: column; gap: 4px; background: #eef5ff; border-radius: 8px; padding: 10px 12px; font-size: 12px; margin-top: 10px; }
.detail-lines { margin-top: 12px; font-size: 12px; }
.detail-lines summary { cursor: pointer; color: var(--brand); }
.data-table.compact th, .data-table.compact td { padding: 5px 8px; font-size: 12px; }
.link-text { color: var(--muted); margin-right: 8px; }
</style>
