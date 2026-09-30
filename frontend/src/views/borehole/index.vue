<template>
  <section class="page" data-module="borehole">
    <header class="page-head">
      <div>
        <h2>钻孔编录管理</h2>
        <p class="page-desc">钻孔编号以现场确认值为准，别名仅用于查找；编录列表、孔口详情、孔位图与钻探日志待办始终按同一版本摆放。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">补录钻孔</button>
        <button class="btn" type="button" @click="exportRows">导出钻孔编录清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="void reload()">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}或别名检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="content-grid">
      <div class="panel">
        <h3 class="panel-title">钻孔编录列表<span class="panel-hint">版本以列表数据为准</span></h3>
        <table class="data-table">
          <thead>
            <tr>
              <th v-for="column in columns" :key="column">{{ column }}</th>
              <th>版本</th>
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="String(row.id)">
              <td v-for="column in columns" :key="column">
                <template v-if="column === '钻孔编号'">
                  <button class="link" type="button" @click="openDetail(row)">{{ row[column] ?? '—' }}</button>
                  <span v-if="aliasText(row)" class="alias-hint" :title="`别名（仅查找）：${aliasText(row)}`">别名 {{ aliasList(row).length }}</span>
                </template>
                <template v-else>{{ row[column] ?? '—' }}</template>
              </td>
              <td><span class="version-tag">v{{ row.version ?? 1 }}</span></td>
              <td class="row-actions">
                <button class="link" type="button" @click="openDetail(row)">详情</button>
                <button
                  v-for="action in actions"
                  :key="action"
                  class="link"
                  type="button"
                  @click="runAction(action, row)"
                >
                  {{ action }}
                </button>
              </td>
            </tr>
            <tr v-if="!rows.length">
              <td :colspan="columns.length + 2" class="empty-state">暂无钻孔编录数据，可先补录钻孔</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="panel side-panel">
        <h3 class="panel-title">孔位图<span class="panel-hint">与列表同版本</span></h3>
        <div class="map-box">
          <svg viewBox="0 0 320 260" class="map-svg" role="img" aria-label="钻孔孔位图">
            <line x1="0" y1="130" x2="320" y2="130" class="map-axis" />
            <line x1="160" y1="0" x2="160" y2="260" class="map-axis" />
            <g v-for="point in mapPoints" :key="String(point.id)">
              <circle
                class="map-point"
                :class="{ active: detail?.id === point.id }"
                :cx="point.x === null ? grid(point).x : scaleX(point.x)"
                :cy="point.y === null ? grid(point).y : scaleY(point.y)"
                r="7"
                @click="openDetailById(point.id)"
              />
              <text
                class="map-label"
                :x="point.x === null ? grid(point).x : scaleX(point.x)"
                :y="(point.y === null ? grid(point).y : scaleY(point.y)) - 10"
              >{{ point.钻孔编号 }}·v{{ point.version }}</text>
            </g>
          </svg>
          <p v-if="!mapPoints.length" class="empty-state map-empty">暂无可摆放的钻孔</p>
          <p class="map-note">坐标无法解析的孔按列表顺序网格摆放，编号标签带当前版本号。</p>
        </div>

        <h3 class="panel-title todo-title">钻探日志待办<span class="panel-hint">动作回写，随孔同版本</span></h3>
        <ul class="todo-list">
          <li v-for="todo in todoRows" :key="String(todo.id)" class="todo-item">
            <span class="todo-code">{{ todo.钻孔编号 }}</span>
            <span class="version-tag">v{{ todo.钻孔版本 ?? '—' }}</span>
            <span class="todo-no">{{ todo.日志编号 }}</span>
            <span class="todo-status" :data-status="todo.status">{{ todo.status }}</span>
          </li>
          <li v-if="!todoRows.length" class="empty-state">暂无待填写的钻探日志</li>
        </ul>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条钻孔编录记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 补录表单：新建与对既有孔补录共用，既有孔带版本锁 -->
    <div v-if="formOpen" class="modal-mask" @click.self="closeForm">
      <div class="modal">
        <h3 class="panel-title">{{ formTarget ? `补录钻孔 ${formTarget.钻孔编号}` : '补录新钻孔' }}</h3>
        <p class="form-hint">
          孔号按现场确认值填写；与当前孔号写法不同且不勾选确认时，只会记为别名用于查找。
          <template v-if="formTarget">当前版本 v{{ formTarget.version ?? 1 }}，提交携带版本锁。</template>
        </p>
        <div class="form-grid">
          <label v-for="field in formFields" :key="field.key" :class="{ wide: field.wide }">
            <span>{{ field.label }}<em v-if="field.required">*</em></span>
            <input v-model="formValues[field.key]" :placeholder="field.placeholder ?? `请输入${field.label}`" />
          </label>
          <label class="check-line">
            <input v-model="formCodeConfirmed" type="checkbox" />
            <span>该孔号为现场确认值（作为规范孔号；不勾选则仅作别名登记）</span>
          </label>
          <label class="check-line">
            <input v-model="formCoordConfirmed" type="checkbox" :disabled="!!formTarget && !!formTarget['坐标已确认']" />
            <span>孔口坐标已现场确认{{ formTarget && formTarget['坐标已确认'] ? '（已确认，不能被补录覆盖）' : '' }}</span>
          </label>
        </div>
        <p v-if="formError" class="error-text">{{ formError }}</p>
        <div class="form-actions">
          <button class="btn ghost" type="button" @click="closeForm">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitForm">
            {{ submitting ? '提交中…' : '提交补录' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 详情抽屉：每次打开都重新拉取，归并后的旧孔号会直接打开存活孔 -->
    <div v-if="detailOpen" class="modal-mask" @click.self="closeDetail">
      <div class="drawer">
        <header class="drawer-head">
          <h3>钻孔详情 {{ detail?.钻孔编号 ?? '' }}</h3>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </header>
        <div v-if="detailLoading" class="empty-state">读取最新版本…</div>
        <template v-else-if="detail">
          <p v-if="detail.原孔号" class="merge-note">
            原孔号「{{ detail.原孔号 }}」已归并到「{{ detail.钻孔编号 }}」，当前展示的是存活孔最新版本。
          </p>
          <div class="detail-version">
            <span class="version-tag">v{{ detail.version ?? 1 }}</span>
            <span :class="detail.孔号已确认 ? 'confirm-yes' : 'confirm-no'">
              {{ detail.孔号已确认 ? '孔号已现场确认' : '孔号待现场确认' }}
            </span>
            <span :class="detail.坐标已确认 ? 'confirm-yes' : 'confirm-no'">
              {{ detail.坐标已确认 ? '坐标已现场确认' : '坐标待现场确认' }}
            </span>
          </div>
          <table class="data-table detail-table">
            <tbody>
              <tr v-for="field in detailFields" :key="field">
                <th>{{ field }}</th>
                <td>{{ detail[field] ?? '—' }}</td>
              </tr>
              <tr>
                <th>别名（仅查找）</th>
                <td>{{ aliasText(detail) || '—' }}</td>
              </tr>
              <tr v-if="mergedIds(detail).length">
                <th>归并自</th>
                <td>钻孔记录 {{ mergedIds(detail).join('、') }} 已按当时孔号迁入</td>
              </tr>
            </tbody>
          </table>
          <div class="detail-actions">
            <button class="btn" type="button" @click="supplementFromDetail">补录本孔</button>
            <button
              v-for="action in actions"
              :key="action"
              class="btn primary"
              type="button"
              @click="runAction(action, detail)"
            >
              {{ action }}
            </button>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | string[] | null>
interface MapPoint extends Row {
  id: number
  x: number | null
  y: number | null
}

const ENDPOINT = '/api/borehole'
const LOG_ENDPOINT = '/api/drilling_log'
const columns = ["钻孔编号", "勘探区", "孔口坐标", "设计孔深", "终孔深度", "开孔日期", "终孔日期", "钻孔状态"]
const actions = ["开始钻进", "登记终孔", "执行封孔"]
const stats = [{"label": "施工中钻孔", "value": 0}, {"label": "已终孔钻孔", "value": 0}, {"label": "已封孔钻孔", "value": 0}]

interface FormField {
  key: string
  label: string
  required?: boolean
  wide?: boolean
  placeholder?: string
}

const formFields: FormField[] = [
  { key: '钻孔编号', label: '钻孔编号', required: true },
  { key: '勘探区', label: '勘探区', required: true },
  { key: '孔口坐标', label: '孔口坐标（X,Y）', required: true },
  { key: '设计孔深', label: '设计孔深' },
  { key: '终孔深度', label: '终孔深度' },
  { key: '开孔日期', label: '开孔日期' },
  { key: '终孔日期', label: '终孔日期' },
]

const detailFields = ["钻孔编号", "勘探区", "孔口坐标", "设计孔深", "终孔深度", "开孔日期", "终孔日期", "钻孔状态", "status"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const mapPoints = ref<MapPoint[]>([])
const todoRows = ref<Row[]>([])

// 详情
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref<Row | null>(null)

// 补录表单
const formOpen = ref(false)
const submitting = ref(false)
const formError = ref('')
const formValues = ref<Record<string, string>>({})
const formCodeConfirmed = ref(true)
const formCoordConfirmed = ref(true)
const formTarget = ref<Row | null>(null)
let formToken = ''

function aliasList(row: Row | null): string[] {
  const value = row?.['别名']
  return Array.isArray(value) ? (value as unknown[]).filter((v): v is string => typeof v === 'string') : []
}
function aliasText(row: Row | null): string {
  return aliasList(row).join('、')
}
function mergedIds(row: Row | null): number[] {
  const value = row?.['归并自']
  return Array.isArray(value) ? (value as unknown[]).filter((v): v is number => typeof v === 'number') : []
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

// 三处统一刷新：列表、孔位图、钻探日志待办在同一个刷新轮次里重读，
// 保证页面上不会同时出现新旧两套版本。
async function reload(keepDetail = false) {
  errorMessage.value = ''
  try {
    const [boreRes, mapRes, logRes] = await Promise.all([
      request(`${ENDPOINT}?${new URLSearchParams(filters.value as Record<string, string>).toString()}`),
      request(`${ENDPOINT}/map`),
      request(`${LOG_ENDPOINT}?size=200`),
    ])
    if (!boreRes.ok || !mapRes.ok || !logRes.ok) {
      throw new Error('钻孔数据读取失败')
    }
    const listPayload = await boreRes.json()
    const mapPayload = await mapRes.json()
    const logPayload = await logRes.json()
    rows.value = listPayload.items ?? []
    total.value = listPayload.total ?? rows.value.length
    mapPoints.value = mapPayload.items ?? []
    todoRows.value = (logPayload.items ?? []).filter(
      (item: Row) => item.status === '待填写' || item.status === '退回补充',
    )
    if (keepDetail && detail.value) {
      await refreshDetail(Number(detail.value.id))
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '钻孔编录列表读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        values: { action, expected_version: row.version ?? 1 },
      }),
    })
    const payload = await response.json().catch(() => null)
    if (response.status === 409) {
      // 版本锁：别人已写过，先同步再提示
      await reload(true)
      errorMessage.value = payload?.detail ? `版本冲突：${payload.detail}` : '版本冲突，请按最新版本重试'
      return
    }
    if (!response.ok || payload?.ok === false) {
      throw new Error(payload?.message ?? '钻孔动作未生效，请稍后重试')
    }
    // 动作回写后重读三处，列表重开不再残留旧值
    await reload(true)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '钻孔编录操作失败'
  }
}

// --------------------------------------------------------------- 详情

async function refreshDetail(id: number) {
  detailLoading.value = true
  try {
    const response = await request(`${ENDPOINT}/${id}`)
    if (!response.ok) {
      throw new Error('钻孔详情读取失败')
    }
    // 服务端对已归并孔号直接返回存活孔，旧详情不会再被打开
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '钻孔详情读取失败'
    detail.value = null
  } finally {
    detailLoading.value = false
  }
}

async function openDetail(row: Row) {
  detailOpen.value = true
  detail.value = row
  await refreshDetail(Number(row.id))
}

async function openDetailById(id: number) {
  detailOpen.value = true
  await refreshDetail(id)
}

function closeDetail() {
  detailOpen.value = false
  detail.value = null
}

// --------------------------------------------------------------- 补录

function emptyForm() {
  formValues.value = {}
  for (const field of formFields) {
    formValues.value[field.key] = ''
  }
  formCodeConfirmed.value = true
  formCoordConfirmed.value = true
  formError.value = ''
}

function openCreate() {
  formTarget.value = null
  emptyForm()
  formToken = newToken()
  formOpen.value = true
}

function supplementFromDetail() {
  if (!detail.value) return
  formTarget.value = detail.value
  emptyForm()
  for (const field of formFields) {
    const value = detail.value[field.key]
    formValues.value[field.key] = typeof value === 'string' ? value : ''
  }
  formCodeConfirmed.value = Boolean(detail.value['孔号已确认'])
  // 再次提交同一坐标、坐标已确认：允许提交（不产生变化），但不能改成别的坐标
  formCoordConfirmed.value = Boolean(detail.value['坐标已确认'])
  formToken = newToken()
  formOpen.value = true
}

function closeForm() {
  formOpen.value = false
  formTarget.value = null
}

async function submitForm() {
  formError.value = ''
  const values: Record<string, unknown> = { ...formValues.value }
  values['孔号已确认'] = formCodeConfirmed.value
  values['坐标已确认'] = formCoordConfirmed.value
  if (formTarget.value) {
    values.id = formTarget.value.id
    values.expected_version = formTarget.value.version ?? 1
  }
  submitting.value = true
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      // token 让同一张补录单的网络重试只生效一次，不再生成重复孔
      body: JSON.stringify({ values, remark: formToken }),
    })
    const payload = await response.json().catch(() => null)
    if (response.status === 409) {
      formError.value = payload?.detail ? `版本冲突：${payload.detail}` : '版本冲突，请刷新后重试'
      await reload(true)
      if (formTarget.value && detail.value && Number(formTarget.value.id) === Number(detail.value.id)) {
        formTarget.value = detail.value
      }
      return
    }
    if (!response.ok || payload?.ok === false) {
      formError.value = payload?.message ?? '补录未生效，请检查必填字段'
      return
    }
    closeForm()
    await reload(true)
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '补录提交失败'
  } finally {
    submitting.value = false
  }
}

// --------------------------------------------------------------- 孔位图

const SCALE = 24
const ORIGIN_X = 160
const ORIGIN_Y = 130

function scaleX(x: number): number {
  return Math.min(310, Math.max(10, ORIGIN_X + x * SCALE))
}
function scaleY(y: number): number {
  return Math.min(250, Math.max(20, ORIGIN_Y - y * SCALE))
}
function grid(point: MapPoint): { x: number; y: number } {
  const index = mapPoints.value
    .filter((item) => item.x === null)
    .findIndex((item) => item.id === point.id)
  const col = index % 5
  const rowNo = Math.floor(index / 5)
  return { x: 36 + col * 60, y: 40 + rowNo * 44 }
}

function newToken(): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `t-${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `sup-${random}`
}

onMounted(() => void reload())
</script>

<style scoped>
.content-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(320px, 1fr);
  gap: 12px;
  align-items: start;
}
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
}
.panel-title {
  margin: 0 0 8px;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.panel-hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
}
.alias-hint {
  margin-left: 6px;
  font-size: 11px;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0 6px;
}
.version-tag {
  display: inline-block;
  font-size: 11px;
  color: #1f6feb;
  background: #eef4ff;
  border-radius: 10px;
  padding: 0 8px;
  line-height: 18px;
}
.side-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.map-box {
  border: 1px dashed var(--border);
  border-radius: 6px;
  padding: 6px;
}
.map-svg {
  width: 100%;
  height: auto;
  display: block;
}
.map-axis {
  stroke: #e2e8f0;
  stroke-width: 1;
}
.map-point {
  fill: #1f6feb;
  cursor: pointer;
}
.map-point.active {
  fill: #d92d20;
  stroke: #7a0c00;
  stroke-width: 2;
}
.map-label {
  font-size: 9px;
  fill: #334155;
  text-anchor: middle;
}
.map-empty {
  padding: 20px 0;
}
.map-note {
  margin: 6px 0 0;
  font-size: 11px;
  color: var(--muted);
}
.todo-title {
  margin-top: 4px;
}
.todo-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 240px;
  overflow: auto;
}
.todo-item {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
}
.todo-code {
  font-weight: 600;
}
.todo-no {
  color: var(--muted);
  font-size: 12px;
}
.todo-status {
  margin-left: auto;
  font-size: 11px;
  border-radius: 10px;
  padding: 0 8px;
  background: #fef3c7;
  color: #92400e;
}
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  justify-content: flex-end;
  z-index: 20;
}
.modal {
  width: 460px;
  max-width: 92vw;
  background: #fff;
  height: 100%;
  padding: 18px;
  overflow: auto;
}
.drawer {
  width: 520px;
  max-width: 94vw;
  background: #fff;
  height: 100%;
  padding: 18px;
  overflow: auto;
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.drawer-head h3 {
  margin: 0;
  font-size: 16px;
}
.form-hint {
  font-size: 12px;
  color: var(--muted);
  margin: 0 0 12px;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.form-grid label span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 2px;
}
.form-grid label em {
  color: #d92d20;
  font-style: normal;
}
.form-grid input[type='text'],
.form-grid input:not([type]) {
  width: 100%;
}
.form-grid label.wide {
  grid-column: 1 / -1;
}
.form-grid input {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
}
.check-line {
  grid-column: 1 / -1;
  display: flex;
  gap: 6px;
  align-items: flex-start;
  font-size: 12px;
}
.form-actions,
.detail-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
.detail-version {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
  font-size: 12px;
}
.confirm-yes {
  color: #027a48;
}
.confirm-no {
  color: #b54708;
}
.merge-note {
  background: #fffaeb;
  border: 1px solid #fedf89;
  color: #92400e;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  margin: 0 0 10px;
}
.detail-table th {
  width: 140px;
  background: #f8fafc;
}
</style>
