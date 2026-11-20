<template>
  <section class="page" data-module="reconcile">
    <header class="page-head">
      <div>
        <h2>交付对账</h2>
        <p class="page-desc">
          按供应方签单的交付编号、应到字段与行标识，核对接入批次的实际到货；
          同一行标识只抵扣一次承诺，字段错位与整行丢失分别复核，发布时冻结对账依据。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openManifestModal">登记签单</button>
        <button class="btn" type="button" @click="reloadManifests">刷新签单列表</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in topStats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reloadManifests">
      <label class="filter-item">
        <span>签单编号 / 供应方</span>
        <input v-model="keyword" placeholder="按签单编号或供应方检索" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="keyword = ''; reloadManifests()">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>签单编号</th>
          <th>供应方</th>
          <th>数据集编号</th>
          <th>应到字段</th>
          <th>承诺行数</th>
          <th>承诺总量</th>
          <th>签单时间</th>
          <th>对账操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in manifests" :key="row.签单编号">
          <td>{{ row.签单编号 }}</td>
          <td>{{ row.供应方 }}</td>
          <td>{{ row.数据集编号 }}</td>
          <td>{{ row.应到字段.join('、') }}</td>
          <td>{{ row.承诺行数 }}</td>
          <td>{{ row.承诺总量 }}</td>
          <td>{{ row.签单时间 }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openStatement(row.签单编号)">批次对账</button>
          </td>
        </tr>
        <tr v-if="!manifests.length">
          <td colspan="8" class="empty-state">暂无签单，可先登记供应方签单</td>
        </tr>
      </tbody>
    </table>

    <Teleport to="body">
      <div v-if="manifestModal" class="modal-overlay" @click.self="manifestModal = false">
        <div class="modal-card modal-wide">
          <header class="modal-head">
            <h3>登记供应方签单</h3>
            <button class="btn ghost" type="button" @click="manifestModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitManifest">
            <label class="modal-field">
              <span>签单编号（交付编号）</span>
              <input v-model="manifestForm.签单编号" placeholder="如 MANI-0003" required />
            </label>
            <label class="modal-field">
              <span>供应方</span>
              <input v-model="manifestForm.供应方" required />
            </label>
            <label class="modal-field">
              <span>数据集编号</span>
              <input v-model="manifestForm.数据集编号" placeholder="如 DATA-0001" required />
            </label>
            <label class="modal-field">
              <span>应到字段（逗号分隔）</span>
              <input v-model="manifestForm.应到字段" placeholder="客户编号,手机号,地区" required />
            </label>
            <div class="modal-field">
              <span>承诺行（行标识 + 承诺数量）</span>
              <div v-for="(line, idx) in manifestLines" :key="idx" class="inline-row">
                <input v-model="line.行标识" placeholder="行标识" required />
                <input v-model.number="line.承诺数量" type="number" min="0" placeholder="承诺数量" required />
                <button class="btn ghost" type="button" @click="manifestLines.splice(idx, 1)">删除</button>
              </div>
              <button class="btn" type="button" @click="manifestLines.push({ 行标识: '', 承诺数量: 100 })">新增承诺行</button>
            </div>
            <p v-if="formError" class="error-text">{{ formError }}</p>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="manifestModal = false">取消</button>
              <button class="btn primary" type="submit">提交签单</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="statementNo" class="modal-overlay modal-overlay-lg" @click.self="statementNo = ''">
        <div class="modal-card modal-xwide">
          <header class="modal-head">
            <h3>批次对账 · {{ statementNo }}</h3>
            <button class="btn ghost" type="button" @click="statementNo = ''">&times;</button>
          </header>
          <div class="modal-body">
            <div class="toolbar">
              <button class="btn primary" type="button" @click="openBatchModal">录入接入批次</button>
              <button class="btn" type="button" @click="refreshStatement">刷新对账单</button>
              <span v-if="formError" class="error-text">{{ formError }}</span>
            </div>

            <h4>对账汇总</h4>
            <div class="stat-row" v-if="statement">
              <article v-for="item in summaryCards" :key="item.label" class="stat-card">
                <span class="stat-label">{{ item.label }}</span>
                <strong class="stat-value">{{ item.value }}</strong>
              </article>
            </div>

            <h4>行级对账（抵扣只认首次到货，可用量随当前版本变化）</h4>
            <table class="data-table compact">
              <thead>
                <tr>
                  <th>行标识</th><th>承诺数量</th><th>已抵扣数量</th><th>当前实际可用</th>
                  <th>抵扣差额</th><th>字段状态</th><th>行状态</th><th>首次凭据</th><th>当前凭据</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="line in statement?.行对账 ?? []" :key="line.行标识">
                  <td>{{ line.行标识 }}</td>
                  <td>{{ line.承诺数量 }}</td>
                  <td>{{ line.已抵扣数量 }}</td>
                  <td>{{ line.当前实际可用数量 }}</td>
                  <td>{{ line.抵扣差额 }}</td>
                  <td :class="{ 'cell-bad': line.字段状态 === '字段错位' }">{{ line.字段状态 }}</td>
                  <td :class="{ 'cell-bad': line.行状态 !== '可交付' && !line.行状态.startsWith('可交付') }">{{ line.行状态 }}</td>
                  <td>{{ line.首次凭据号 }}</td>
                  <td>{{ line.当前凭据号 }}</td>
                </tr>
              </tbody>
            </table>

            <h4>差错清单（字段错位 / 整行丢失 / 重复到货分别复核）</h4>
            <table class="data-table compact">
              <thead>
                <tr><th>差错类型</th><th>行标识</th><th>凭据号</th><th>说明</th><th>复核状态</th><th>操作</th></tr>
              </thead>
              <tbody>
                <tr v-for="disp in statement?.差错清单 ?? []" :key="disp.复核键">
                  <td :class="{ 'cell-bad': disp.差错类型 === '整行丢失' || disp.差错类型 === '字段错位' }">{{ disp.差错类型 }}</td>
                  <td>{{ disp.行标识 }}</td>
                  <td>{{ disp.凭据号 }}</td>
                  <td class="note-cell">{{ disp.说明 }}</td>
                  <td>{{ disp.复核状态 }}</td>
                  <td class="row-actions">
                    <button class="link" type="button" @click="submitReview(disp, '确认差错')">确认差错</button>
                    <button class="link" type="button" @click="submitReview(disp, '驳回差错')">驳回</button>
                  </td>
                </tr>
                <tr v-if="!(statement?.差错清单 ?? []).length">
                  <td colspan="6" class="empty-state">当前对账单没有差错</td>
                </tr>
              </tbody>
            </table>

            <h4>分批凭据（同一签单关联的批次全部可查）</h4>
            <table class="data-table compact">
              <thead>
                <tr><th>凭据号</th><th>交付编号</th><th>类型</th><th>实际字段</th><th>实际行数</th><th>接收时间</th><th>标记</th><th>操作</th></tr>
              </thead>
              <tbody>
                <tr v-for="b in statement?.分批凭据 ?? []" :key="b.凭据号">
                  <td>{{ b.凭据号 }}</td>
                  <td :class="{ 'cell-bad': b.重复交付 }">{{ b.交付编号 }}</td>
                  <td>{{ b.批次类型 }}</td>
                  <td class="note-cell">{{ b.实际字段.join('、') }}</td>
                  <td>{{ b.实际行数 }}</td>
                  <td>{{ b.接收时间 }}</td>
                  <td>
                    <span v-if="b.重复交付" class="tag tag-bad">重复交付</span>
                    <span v-if="b.已冻结依据" class="tag tag-ok">依据已冻结</span>
                  </td>
                  <td class="row-actions">
                    <button class="link" type="button" @click="viewBatch(b.id)">实际明细</button>
                    <button v-if="!b.已冻结依据" class="link" type="button" @click="freezeBasis(b.id)">确认交付冻结依据</button>
                  </td>
                </tr>
              </tbody>
            </table>

            <h4>交付缺行对账单（数据服务可交付编号集合）</h4>
            <p class="deliverable-box">
              可交付：<strong>{{ (statement?.汇总.可交付编号集合 ?? []).join('、') || '—' }}</strong>
              ；缺行：<strong class="cell-bad">{{ (statement?.汇总.缺行编号集合 ?? []).join('、') || '无' }}</strong>
            </p>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="batchModal" class="modal-overlay" @click.self="batchModal = false">
        <div class="modal-card modal-wide">
          <header class="modal-head">
            <h3>录入接入批次 · {{ statementNo }}</h3>
            <button class="btn ghost" type="button" @click="batchModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitBatch">
            <label class="modal-field">
              <span>凭据号</span>
              <input v-model="batchForm.凭据号" placeholder="如 CRED-1005" required />
            </label>
            <label class="modal-field">
              <span>交付编号（供应方签单编号）</span>
              <input v-model="batchForm.交付编号" placeholder="如 DLV-2005" required />
            </label>
            <label class="modal-field">
              <span>批次类型</span>
              <select v-model="batchForm.批次类型">
                <option>交付</option><option>重送</option><option>更正</option>
              </select>
            </label>
            <label class="modal-field">
              <span>实际字段（逗号分隔）</span>
              <input v-model="batchForm.实际字段" required />
            </label>
            <div class="modal-field">
              <span>实际行</span>
              <div v-for="(row, idx) in batchRows" :key="idx" class="inline-row">
                <input v-model="row.行标识" placeholder="行标识" required />
                <input v-model.number="row.实际数量" type="number" min="0" placeholder="实际数量" required />
                <select v-model="row.行类型"><option>原行</option><option>更正行</option></select>
                <button class="btn ghost" type="button" @click="batchRows.splice(idx, 1)">删除</button>
              </div>
              <button class="btn" type="button" @click="batchRows.push({ 行标识: '', 实际数量: 100, 行类型: '原行' })">新增实际行</button>
            </div>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="batchModal = false">取消</button>
              <button class="btn primary" type="submit">提交批次</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="batchDetail" class="modal-overlay" @click.self="batchDetail = null">
        <div class="modal-card modal-wide">
          <header class="modal-head">
            <h3>批次实际明细 · {{ batchDetail.凭据号 }}</h3>
            <button class="btn ghost" type="button" @click="batchDetail = null">&times;</button>
          </header>
          <div class="modal-body">
            <p>交付编号：{{ batchDetail.交付编号 }} ｜ 类型：{{ batchDetail.批次类型 }} ｜ 接收：{{ batchDetail.接收时间 }}</p>
            <p>实际字段：{{ batchDetail.实际字段.join('、') }}</p>
            <table class="data-table compact">
              <thead><tr><th>行标识</th><th>实际数量</th><th>行类型</th></tr></thead>
              <tbody>
                <tr v-for="row in batchDetail.实际行" :key="row.行标识 + row.行类型">
                  <td>{{ row.行标识 }}</td><td>{{ row.实际数量 }}</td><td>{{ row.行类型 }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="batchDetail.对账依据快照" class="basis-box">
              <h4>已冻结对账依据（{{ batchDetail.对账依据快照.冻结时间 }}）</h4>
              <p>冻结时缺行：{{ (batchDetail.对账依据快照.对账单.汇总.缺行编号集合 || []).join('、') || '无' }}</p>
              <p>冻结时可交付：{{ (batchDetail.对账依据快照.对账单.汇总.可交付编号集合 || []).join('、') }}</p>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type ManifestRow = Record<string, any>
type Statement = Record<string, any>
type BatchDetail = Record<string, any> | null

const manifests = ref<ManifestRow[]>([])
const keyword = ref('')
const formError = ref('')

const manifestModal = ref(false)
const manifestForm = ref<Record<string, string>>({})
const manifestLines = ref<Array<{ 行标识: string; 承诺数量: number }>>([])

const statementNo = ref('')
const statement = ref<Statement | null>(null)

const batchModal = ref(false)
const batchForm = ref<Record<string, string>>({})
const batchRows = ref<Array<Record<string, any>>>([])

const batchDetail = ref<BatchDetail>(null)

const topStats = computed(() => [
  { label: '签单数', value: manifests.value.length },
  { label: '对账中签单', value: manifests.value.filter((m) => m.状态 === '对账中').length },
  { label: '当前查看签单差错', value: statement.value?.差错清单?.length ?? 0 },
])

const summaryCards = computed(() => {
  const s = statement.value?.汇总
  if (!s) return []
  return [
    { label: '应收行数', value: s.应收行数 },
    { label: '已抵扣行数', value: s.已抵扣行数 },
    { label: '缺行数', value: s.缺行数 },
    { label: '承诺总量', value: s.承诺总量 },
    { label: '已抵扣总量', value: s.已抵扣总量 },
    { label: '当前可用总量', value: s.当前可用总量 },
    { label: '字段错位', value: s.差错计数['字段错位'] },
    { label: '整行丢失', value: s.差错计数['整行丢失'] },
    { label: '重复到货', value: s.差错计数['重复到货'] },
    { label: '超额行', value: s.差错计数['超额行'] },
  ]
})

async function reloadManifests() {
  const query = keyword.value ? `?keyword=${encodeURIComponent(keyword.value)}` : ''
  const resp = await request(`/api/reconcile/manifests${query}`)
  const data = await resp.json()
  manifests.value = data.items ?? []
}

async function refreshStatement() {
  if (!statementNo.value) return
  const resp = await request(`/api/reconcile/manifests/${statementNo.value}/statement`)
  statement.value = await resp.json()
}

function openManifestModal() {
  manifestForm.value = { 签单编号: '', 供应方: '', 数据集编号: '', 应到字段: '' }
  manifestLines.value = [{ 行标识: '', 承诺数量: 100 }]
  formError.value = ''
  manifestModal.value = true
}

async function submitManifest() {
  formError.value = ''
  const payload = {
    values: {
      签单编号: manifestForm.value.签单编号,
      供应方: manifestForm.value.供应方,
      数据集编号: manifestForm.value.数据集编号,
      应到字段: manifestForm.value.应到字段.split(',').map((x) => x.trim()).filter(Boolean),
      承诺行: manifestLines.value.map((l) => ({ 行标识: l.行标识, 承诺数量: Number(l.承诺数量) })),
    },
  }
  const resp = await request('/api/reconcile/manifests', { method: 'POST', body: JSON.stringify(payload) })
  const data = await resp.json()
  if (!data.ok) {
    formError.value = data.message
    return
  }
  manifestModal.value = false
  await reloadManifests()
}

async function openStatement(no: string) {
  statementNo.value = no
  statement.value = null
  await refreshStatement()
}

function openBatchModal() {
  batchForm.value = { 凭据号: '', 交付编号: '', 批次类型: '交付', 实际字段: '' }
  batchRows.value = [{ 行标识: '', 实际数量: 100, 行类型: '原行' }]
  formError.value = ''
  batchModal.value = true
}

async function submitBatch() {
  formError.value = ''
  const payload = {
    values: {
      凭据号: batchForm.value.凭据号,
      交付编号: batchForm.value.交付编号,
      批次类型: batchForm.value.批次类型,
      实际字段: batchForm.value.实际字段.split(',').map((x) => x.trim()).filter(Boolean),
      实际行: batchRows.value.map((r) => ({ ...r, 实际数量: Number(r.实际数量) })),
    },
  }
  const resp = await request(`/api/reconcile/manifests/${statementNo.value}/batches`,
    { method: 'POST', body: JSON.stringify(payload) })
  const data = await resp.json()
  if (!data.ok) {
    formError.value = data.message
    return
  }
  batchModal.value = false
  await refreshStatement()
}

async function submitReview(disp: Record<string, any>, conclusion: string) {
  const resp = await request(`/api/reconcile/manifests/${statementNo.value}/reviews`, {
    method: 'POST',
    body: JSON.stringify({ values: { 复核键: disp.复核键, 结论: conclusion } }),
  })
  const data = await resp.json()
  if (!data.ok) {
    formError.value = data.message
    return
  }
  await refreshStatement()
}

async function viewBatch(id: number) {
  const resp = await request(`/api/reconcile/batches/${id}`)
  batchDetail.value = await resp.json()
}

async function freezeBasis(id: number) {
  const resp = await request(`/api/reconcile/batches/${id}/freeze`, { method: 'POST', body: JSON.stringify({}) })
  const data = await resp.json()
  if (!data.ok) {
    formError.value = data.message
    return
  }
  await refreshStatement()
}

onMounted(reloadManifests)
</script>

<style scoped>
.modal-wide { width: 560px; }
.modal-xwide { width: 92vw; max-width: 1280px; }
.modal-overlay-lg { padding: 24px; }
.inline-row { display: flex; gap: 8px; margin-bottom: 8px; }
.inline-row input, .inline-row select { flex: 1; padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.compact th, .compact td { padding: 6px 8px; font-size: 12px; }
.cell-bad { color: #b42318; font-weight: 600; }
.note-cell { max-width: 320px; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.tag-bad { background: #fee4e2; color: #b42318; }
.tag-ok { background: #dcfae6; color: #067647; }
.toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.deliverable-box { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; font-size: 13px; }
.basis-box { margin-top: 12px; border: 1px dashed var(--brand); border-radius: 8px; padding: 10px 12px; background: #f5f9ff; }
h4 { margin: 14px 0 8px; font-size: 14px; }
</style>
