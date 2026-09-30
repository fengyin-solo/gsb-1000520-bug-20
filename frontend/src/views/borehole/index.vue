<template>
  <section class="page" data-module="borehole">
    <header class="page-head">
      <div>
        <h2>钻孔编录管理</h2>
        <p class="page-desc">维护钻孔，围绕钻孔编号、勘探区、孔口坐标做登记、补录归并、孔位图与钻探待办同步。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记/补录钻孔</button>
        <button class="btn" type="button" @click="exportRows">导出钻孔编录清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in visibleStats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>钻孔编号/别名</span>
        <input v-model="filters.keyword" placeholder="按主孔号或别名检索" />
      </label>
      <label class="filter-item">
        <span>勘探区</span>
        <input v-model="filters.area" placeholder="按勘探区检索" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="content-grid">
      <div>
        <table class="data-table">
          <thead>
            <tr>
              <th v-for="column in columns" :key="column">{{ column }}</th>
              <th>版本</th>
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="`${row.id}@${row.version}`">
              <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
              <td>v{{ row.version }}</td>
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
              <td :colspan="columns.length + 2" class="empty-state">暂无钻孔编录数据，可先登记钻孔</td>
            </tr>
          </tbody>
        </table>

        <section class="panel todo-panel">
          <div class="panel-head">
            <h3>钻探日志待办（同一版本）</h3>
            <button class="btn ghost" type="button" @click="reload">刷新待办</button>
          </div>
          <table class="data-table compact">
            <thead>
              <tr>
                <th>日志编号</th>
                <th>钻孔编号</th>
                <th>钻孔版本</th>
                <th>钻进深度</th>
                <th>日志状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="todo in todos" :key="String(todo.id)">
                <td>{{ todo['日志编号'] }}</td>
                <td>{{ todo['钻孔编号'] }}</td>
                <td>{{ todo['钻孔版本'] ? `v${todo['钻孔版本']}` : '—' }}</td>
                <td>{{ todo['钻进深度'] ?? '—' }}</td>
                <td>{{ todo['日志状态'] }}</td>
              </tr>
              <tr v-if="!todos.length">
                <td colspan="5" class="empty-state">暂无钻探待办</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <aside class="side-column">
        <section class="panel">
          <h3>孔位图</h3>
          <div class="map-canvas">
            <button
              v-for="point in mapPoints"
              :key="`${point.id}@${point.version}`"
              class="map-point"
              type="button"
              :class="{ unplaced: point.坐标X === null || point.坐标Y === null }"
              :style="pointStyle(point)"
              :title="`${point['钻孔编号']} · v${point.version}`"
              @click="openDetail(point)"
            >
              {{ point['钻孔编号'] }}<small>v{{ point.version }}</small>
            </button>
            <p v-if="!mapPoints.length" class="empty-state">暂无可显示孔位</p>
          </div>
        </section>

        <section v-if="detail" class="panel detail-panel">
          <div class="panel-head">
            <h3>钻孔详情</h3>
            <button class="link" type="button" @click="detail = null">关闭</button>
          </div>
          <dl>
            <template v-for="field in detailFields" :key="field">
              <dt>{{ field }}</dt>
              <dd>{{ detail[field] ?? '—' }}</dd>
            </template>
            <dt>钻孔别名</dt>
            <dd>{{ aliasText(detail) || '—' }}</dd>
            <dt>版本</dt>
            <dd>v{{ detail.version }}</dd>
            <dt>坐标状态</dt>
            <dd>{{ detail['坐标已确认'] ? '现场已确认' : '待确认' }}</dd>
          </dl>
          <button class="btn primary" type="button" @click="openSupplement(detail)">按当前版本补录</button>
        </section>
      </aside>
    </div>

    <div v-if="formOpen" class="modal-backdrop" @click.self="formOpen = false">
      <form class="modal" @submit.prevent="submitForm">
        <h3>{{ form.id ? '补录钻孔' : '登记钻孔' }}</h3>
        <label>
          <span>钻孔编号/旧孔号/别名（用于查找）</span>
          <input v-model="form.lookup" required placeholder="例如 OLD-01" />
        </label>
        <label>
          <span>现场确认钻孔编号（留空则沿用原孔号）</span>
          <input v-model="form.confirmedNumber" placeholder="例如 ZK-01" />
        </label>
        <label>
          <span>勘探区</span>
          <input v-model="form.area" required />
        </label>
        <label>
          <span>孔口坐标（x,y）</span>
          <input v-model="form.coordinate" required placeholder="例如 520100.12,3420100.45" />
        </label>
        <label>
          <span>其他别名（逗号分隔）</span>
          <input v-model="form.aliases" />
        </label>
        <label class="check-line">
          <input v-model="form.coordinateConfirmed" type="checkbox" />
          <span>该坐标已经现场确认，可作为主坐标</span>
        </label>
        <p v-if="form.version" class="form-hint">将基于 v{{ form.version }} 提交；他人已更新时会拒绝写入。</p>
        <p v-if="formMessage" class="error-text">{{ formMessage }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="formOpen = false">取消</button>
          <button class="btn primary" type="submit" :disabled="saving">{{ saving ? '提交中…' : '提交' }}</button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条钻孔编录记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | string[] | null>

interface ApiResult {
  items: Row[]
  total: number
}

interface FormState {
  id: number | null
  lookup: string
  confirmedNumber: string
  area: string
  coordinate: string
  aliases: string
  coordinateConfirmed: boolean
  version: number | null
}

const ENDPOINT = '/api/borehole'
const LOG_ENDPOINT = '/api/drilling_log'
const columns = ['钻孔编号', '勘探区', '孔口坐标', '设计孔深', '终孔深度', '开孔日期', '终孔日期', '钻孔状态']
const detailFields = ['id', ...columns]
const actions = ['开始钻进', '登记终孔', '执行封孔']
const statuses = ['待施工', '钻进中', '已终孔', '已封孔', '已废弃']
const stats = [
  { label: '施工中钻孔', value: 0 },
  { label: '已终孔钻孔', value: 0 },
  { label: '已封孔钻孔', value: 0 },
]

const rows = ref<Row[]>([])
const mapPoints = ref<Row[]>([])
const todos = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const detail = ref<Row | null>(null)
const formOpen = ref(false)
const formMessage = ref('')
const saving = ref(false)
const requestId = ref('')
const filters = reactive({ keyword: '', area: '', status: '' })

const form = reactive<FormState>({
  id: null,
  lookup: '',
  confirmedNumber: '',
  area: '',
  coordinate: '',
  aliases: '',
  coordinateConfirmed: false,
  version: null,
})

const statsView = computed(() => [
  { ...stats[0], value: rows.value.filter((row) => row['钻孔状态'] === '钻进中').length },
  { ...stats[1], value: rows.value.filter((row) => row['钻孔状态'] === '已终孔').length },
  { ...stats[2], value: rows.value.filter((row) => row['钻孔状态'] === '已封孔').length },
])
const visibleStats = statsView

function resetFilters() {
  filters.keyword = ''
  filters.area = ''
  filters.status = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function emptyForm() {
  form.id = null
  form.lookup = ''
  form.confirmedNumber = ''
  form.area = ''
  form.coordinate = ''
  form.aliases = ''
  form.coordinateConfirmed = false
  form.version = null
  requestId.value = globalThis.crypto?.randomUUID?.() ?? `request-${Date.now()}-${Math.random()}`
}

function aliasText(row: Row) {
  const aliases = row['钻孔别名']
  return Array.isArray(aliases) ? aliases.join(',') : String(aliases ?? '')
}

function openCreate() {
  emptyForm()
  formMessage.value = ''
  formOpen.value = true
}

function openSupplement(row: Row) {
  emptyForm()
  form.id = Number(row.id)
  form.lookup = String(row['钻孔编号'] ?? '')
  form.confirmedNumber = ''
  form.area = String(row['勘探区'] ?? '')
  form.coordinate = String(row['孔口坐标'] ?? '')
  form.aliases = aliasText(row)
  form.coordinateConfirmed = Boolean(row['坐标已确认'])
  form.version = Number(row.version)
  formMessage.value = ''
  formOpen.value = true
  detail.value = row
}

function openDetail(row: Row) {
  detail.value = row
}

async function readJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(payload?.detail ?? `接口返回 ${response.status}`)
  }
  return (await response.json()) as T
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.keyword) query.set('keyword', filters.keyword)
  if (filters.area) query.set('area', filters.area)
  if (filters.status) query.set('status', filters.status)
  try {
    const [boreholes, map, logTodos] = await Promise.all([
      readJson<ApiResult>(`${ENDPOINT}?${query.toString()}`),
      readJson<ApiResult>(`${ENDPOINT}/map?area=${encodeURIComponent(filters.area)}`),
      readJson<ApiResult>(`${LOG_ENDPOINT}?pending=true&size=200`),
    ])
    rows.value = boreholes.items
    total.value = boreholes.total
    mapPoints.value = map.items
    todos.value = logTodos.items
    if (detail.value) {
      const latest = rows.value.find((row) => Number(row.id) === Number(detail.value?.id))
        ?? mapPoints.value.find((row) => Number(row.id) === Number(detail.value?.id))
      detail.value = latest ?? null
    }
  } catch (error) {
    rows.value = []
    mapPoints.value = []
    todos.value = []
    total.value = 0
    errorMessage.value = error instanceof Error ? error.message : '钻孔数据读取失败'
  }
}

async function submitForm() {
  saving.value = true
  formMessage.value = ''
  const values: Record<string, unknown> = {
    钻孔编号: form.lookup,
    勘探区: form.area,
    孔口坐标: form.coordinate,
    钻孔别名: form.aliases.split(',').map((item) => item.trim()).filter(Boolean),
    坐标已确认: form.coordinateConfirmed,
  }
  if (form.confirmedNumber.trim()) values['现场确认钻孔编号'] = form.confirmedNumber.trim()

  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({
        values,
        expected_version: form.version,
        request_id: requestId.value,
      }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string; entry?: Row }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '钻孔补录未生效')
    }
    formOpen.value = false
    await reload()
    if (payload.entry) detail.value = payload.entry
  } catch (error) {
    formMessage.value = error instanceof Error ? error.message : '钻孔补录失败'
  } finally {
    saving.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action, expected_version: row.version }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string; entry?: Row }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '钻孔编录动作未生效，请刷新后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '钻孔编录操作失败'
  }
}

function coordinateRange() {
  const placed = mapPoints.value
    .map((point) => [Number(point.坐标X), Number(point.坐标Y)])
    .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y))
  if (!placed.length) return null
  const xs = placed.map(([x]) => x)
  const ys = placed.map(([, y]) => y)
  return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) }
}

function pointStyle(point: Row) {
  const range = coordinateRange()
  const x = Number(point.坐标X)
  const y = Number(point.坐标Y)
  if (!range || !Number.isFinite(x) || !Number.isFinite(y)) {
    return { left: '12px', top: `${12 + (Number(point.id) % 8) * 28}px` }
  }
  const spanX = Math.max(range.maxX - range.minX, 1)
  const spanY = Math.max(range.maxY - range.minY, 1)
  return {
    left: `${8 + ((x - range.minX) / spanX) * 82}%`,
    top: `${8 + (1 - (y - range.minY) / spanY) * 76}%`,
  }
}

onMounted(reload)
</script>
