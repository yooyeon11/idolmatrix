<script setup lang="ts">
/**
 * 设置 → AI 任务（ai2）
 *
 * 三块：
 * 1. 配置卡：provider / base_url / key / 默认模型 + 核验步模型覆盖（保存到 app_settings.ai2）
 * 2. 自检：跑 selftest 全链路（知识层 → 工具 → LLM → 报告），LLM 未配置时显示跳过
 * 3. 知识层：记忆概况（是否需蒸馏）、工作流清单、规则草案审批（人工闸）
 */
import { onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { ai2Api, type Ai2PendingRule, type Ai2Status, type Ai2TaskRun } from '@/api/ai2'
import { appSettingsApi } from '@/api/appSettings'
import type { AppAi2Settings } from '@/types/models'

const message = useMessage()

const cfg = ref<AppAi2Settings>({
  enabled: false,
  provider: 'openai-compatible',
  base_url: '',
  api_key: '',
  model: '',
  step_models: {},
})
const saving = ref(false)
const status = ref<Ai2Status | null>(null)
const selftestRun = ref<Ai2TaskRun | null>(null)
const selftesting = ref(false)
const pending = ref<Ai2PendingRule[]>([])
const approved = ref<Ai2PendingRule[]>([])
const decidingId = ref<string | null>(null)
const mergingId = ref<string | null>(null)
const distilling = ref(false)

async function loadRules() {
  pending.value = await ai2Api.pendingRules()
  approved.value = await ai2Api.rules('approved')
}

async function mergeRule(r: Ai2PendingRule) {
  mergingId.value = r.id
  try {
    await ai2Api.mergeRule(r.id)
    message.success('已合入知识层（后续任务开工即读）')
    await loadRules()
    status.value = await ai2Api.status()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    mergingId.value = null
  }
}

async function runDistill() {
  distilling.value = true
  try {
    const run = await ai2Api.runTask('memory_distill', { force: true })
    if (run.status === 'done') {
      const r = run.result
      message.success(r?.distilled ? `已蒸馏 ${r.before_chars} → ${r.after_chars} 字（已备份）` : '当前无需蒸馏')
    } else {
      message.warning(run.error || `任务 ${run.status}`)
    }
    status.value = await ai2Api.status()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    distilling.value = false
  }
}
const verifyModel = ref('')

// 任务运行（P3：ingest_audit / P2：artist_create）
const taskBatch = ref('')
const taskRun = ref<Ai2TaskRun | null>(null)
const taskRunning = ref(false)

// 艺人/组合添加（P2）
const acKind = ref<'artist' | 'group'>('artist')
const acKeyword = ref('')
const acDryRun = ref(true)
const acRun = ref<Ai2TaskRun | null>(null)
const acRunning = ref(false)

async function runArtistCreate() {
  if (!acKeyword.value.trim()) {
    message.warning('请填写艺人/组合名')
    return
  }
  acRunning.value = true
  try {
    acRun.value = await ai2Api.runTask('artist_create', {
      kind: acKind.value,
      keyword: acKeyword.value.trim(),
      dry_run: acDryRun.value,
    })
    if (acRun.value.status === 'done') {
      const r = acRun.value.result
      if (r?.reason === 'name_conflict') message.warning('库内已有同名/近似实体，未创建')
      else if (r?.created) message.success(`已创建 #${r.created_id}（报告已落盘）`)
      else message.success('dry-run 完成，未写库（确认报告后取消勾选再跑）')
    } else {
      message.warning(acRun.value.error || `任务 ${acRun.value.status}`)
    }
    status.value = await ai2Api.status()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    acRunning.value = false
  }
}

async function runIngestAudit() {
  if (!taskBatch.value.trim()) {
    message.warning('请填写批次名（如 20261015-kara-honey）')
    return
  }
  taskRunning.value = true
  try {
    taskRun.value = await ai2Api.runTask('ingest_audit', { batch: taskBatch.value.trim() })
    if (taskRun.value.status === 'done') message.success('核对完成，报告已落盘')
    else message.warning(taskRun.value.error || `任务 ${taskRun.value.status}`)
    status.value = await ai2Api.status()
    pending.value = status.value.knowledge.pending_rules
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    taskRunning.value = false
  }
}

async function load() {
  try {
    const s = await appSettingsApi.get()
    if (s.ai2) {
      // 回读是脱敏的（api_key 恒为空串，api_key_set 表示是否已保存）
      cfg.value = {
        enabled: s.ai2.enabled,
        provider: s.ai2.provider || 'openai-compatible',
        base_url: s.ai2.base_url || '',
        api_key: '',
        model: s.ai2.model || '',
        step_models: s.ai2.step_models || {},
      }
      cfg.value.api_key_set = s.ai2.api_key_set
      verifyModel.value = s.ai2.step_models?.verify || ''
    }
  } catch (e) {
    message.error((e as Error).message)
  }
  try {
    status.value = await ai2Api.status()
    await loadRules()
  } catch {
    /* 状态读不到不阻塞 */
  }
}

async function save() {
  saving.value = true
  try {
    const step_models: Record<string, string> = {}
    if (verifyModel.value.trim()) step_models.verify = verifyModel.value.trim()
    // api_key 留空 = 保留已保存密钥（后端 _preserve_secrets 语义）
    await appSettingsApi.patch({ ai2: { ...cfg.value, step_models } })
    message.success('已保存')
    await load()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

async function runSelftest() {
  selftesting.value = true
  try {
    selftestRun.value = await ai2Api.runTask('selftest')
    if (selftestRun.value.status === 'done') message.success('自检完成')
    else if (selftestRun.value.status === 'skipped') message.warning(selftestRun.value.error || 'LLM 未配置，自检跳过模型步骤')
    else message.error(selftestRun.value.error || '自检失败')
    status.value = await ai2Api.status()
    pending.value = status.value.knowledge.pending_rules
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    selftesting.value = false
  }
}

async function decide(rule: Ai2PendingRule, approve: boolean) {
  decidingId.value = rule.id
  try {
    await ai2Api.decideRule(rule.id, approve)
    message.success(approve ? '已批准（请手工合入对应规则文件）' : '已驳回')
    pending.value = await ai2Api.pendingRules()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    decidingId.value = null
  }
}

onMounted(() => void load())
</script>

<template>
  <section id="grp-ai2" class="sa-section sa-section--wide ai2-panel">
    <div class="panel-group-title">AI 任务</div>

    <div class="ai2-grid">
      <label class="ai2-check">
        <input v-model="cfg.enabled" type="checkbox" />
        启用 ai2（保存后任务模板才会调用模型）
      </label>

      <div class="ai2-field">
        <span class="ai2-label">协议</span>
        <select v-model="cfg.provider" class="sa-input">
          <option value="openai-compatible">OpenAI 兼容</option>
          <option value="anthropic">Anthropic</option>
        </select>
      </div>

      <div class="ai2-field">
        <span class="ai2-label">Base URL</span>
        <input v-model="cfg.base_url" class="sa-input" placeholder="https://api.deepseek.com" />
      </div>

      <div class="ai2-field">
        <span class="ai2-label">API Key</span>
        <input
          v-model="cfg.api_key"
          class="sa-input"
          type="password"
          :placeholder="cfg.api_key_set ? '已保存（留空即不改）' : 'sk-…'"
        />
      </div>

      <div class="ai2-field">
        <span class="ai2-label">默认模型</span>
        <input v-model="cfg.model" class="sa-input" placeholder="deepseek-chat" />
      </div>

      <div class="ai2-field">
        <span class="ai2-label">核验步模型（可选，建议更强）</span>
        <input v-model="verifyModel" class="sa-input" placeholder="deepseek-reasoner" />
      </div>
    </div>

    <div class="ai2-actions">
      <button class="sa-btn sa-btn--primary" type="button" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存配置' }}
      </button>
      <button class="sa-btn" type="button" :disabled="selftesting" @click="runSelftest">
        {{ selftesting ? '自检中…' : '跑全链路自检' }}
      </button>
    </div>

    <div v-if="selftestRun" class="ai2-selftest">
      <b>自检结果（{{ selftestRun.status }}，{{ selftestRun.duration_ms }}ms）</b>
      <ul>
        <li v-for="(s, i) in selftestRun.steps" :key="i" :class="{ ok: s.ok, bad: !s.ok }">
          {{ s.step }} — {{ s.detail }}
        </li>
      </ul>
    </div>

    <div class="ai2-task">
      <b>艺人/组合添加（artist_create）</b>
      <p class="ai2-kv">
        查重 → QQ 音乐信源 → LLM 草稿 → 独立核验 →（默认 dry-run）报告。写库需显式取消 dry-run。
      </p>
      <div class="ai2-task-row">
        <select v-model="acKind" class="sa-input" style="max-width: 110px">
          <option value="artist">艺人</option>
          <option value="group">组合</option>
        </select>
        <input v-model="acKeyword" class="sa-input" placeholder="名称，如 Sullyoon" />
        <label class="ai2-check" style="white-space: nowrap">
          <input v-model="acDryRun" type="checkbox" /> dry-run
        </label>
        <button class="sa-btn sa-btn--primary" type="button" :disabled="acRunning" @click="runArtistCreate">
          {{ acRunning ? '执行中…' : '执行' }}
        </button>
      </div>
      <div v-if="acRun" class="ai2-selftest">
        <b>{{ acRun.name }}（{{ acRun.status }}，{{ acRun.duration_ms }}ms）</b>
        <ul>
          <li v-for="(s, i) in acRun.steps" :key="i" :class="{ ok: s.ok, bad: !s.ok }">
            {{ s.step }} — {{ s.detail }}
          </li>
        </ul>
        <p v-if="acRun.result?.report" class="ai2-kv">报告：{{ acRun.result.report }}</p>
      </div>
    </div>

    <div class="ai2-task">
      <b>入库清单核对（ingest_audit）</b>
      <p class="ai2-kv">只读任务：imflow 四维回读核验 + 库内一致性 + LLM 差异归因 → HTML 报告。</p>
      <div class="ai2-task-row">
        <input v-model="taskBatch" class="sa-input" placeholder="批次名，如 20261015-kara-honey" />
        <button class="sa-btn sa-btn--primary" type="button" :disabled="taskRunning" @click="runIngestAudit">
          {{ taskRunning ? '核对中…' : '跑核对' }}
        </button>
      </div>
      <div v-if="taskRun" class="ai2-selftest">
        <b>{{ taskRun.name }}（{{ taskRun.status }}，{{ taskRun.duration_ms }}ms）</b>
        <ul>
          <li v-for="(s, i) in taskRun.steps" :key="i" :class="{ ok: s.ok, bad: !s.ok }">
            {{ s.step }} — {{ s.detail }}
          </li>
        </ul>
        <p v-if="taskRun.result?.report" class="ai2-kv">报告：{{ taskRun.result.report }}</p>
      </div>
    </div>

    <div v-if="status" class="ai2-knowledge">
      <b>知识层</b>
      <p class="ai2-kv">
        记忆 {{ status.knowledge.memory.chars }} 字（上限 {{ status.knowledge.memory.limit }}，
        <span v-if="status.knowledge.memory.needs_distill" class="bad">需蒸馏</span>
        <span v-else>健康</span>）· 工作流 {{ status.knowledge.skills.length }} 个 · 报告
        {{ status.knowledge.reports_count }} 份 · 待批规则 {{ pending.length }} 条
      </p>
      <p class="ai2-kv">
        工作流：{{ status.knowledge.skills.map((s) => s.title).join(' / ') || '（无）' }}
      </p>
      <button class="sa-btn" type="button" :disabled="distilling" @click="runDistill">
        {{ distilling ? '蒸馏中…' : '蒸馏记忆（超限时）' }}
      </button>
      <p class="ai2-kv">已注册工具 {{ status.tools.length }} 个：{{ status.tools.map((t) => t.name).join('、') }}</p>
    </div>

    <div v-if="approved.length" class="ai2-rules">
      <b>已批准 · 待合入知识层</b>
      <div v-for="r in approved" :key="r.id" class="ai2-rule">
        <div class="ai2-rule-head">
          <span class="ai2-rule-kind">{{ r.kind }}</span>
          <span class="ai2-rule-task">来自 {{ r.source_task }}</span>
        </div>
        <p class="ai2-rule-draft">{{ r.draft }}</p>
        <p class="ai2-rule-why">依据：{{ r.rationale }}</p>
        <div class="ai2-rule-actions">
          <button class="sa-btn sa-btn--primary" type="button" :disabled="mergingId === r.id" @click="mergeRule(r)">
            {{ mergingId === r.id ? '合入中…' : '合入知识库' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="pending.length" class="ai2-rules">
      <b>规则草案（人工闸：批准后手工合入规则文件）</b>
      <div v-for="r in pending" :key="r.id" class="ai2-rule">
        <div class="ai2-rule-head">
          <span class="ai2-rule-kind">{{ r.kind }}</span>
          <span class="ai2-rule-task">来自 {{ r.source_task }}</span>
        </div>
        <p class="ai2-rule-draft">{{ r.draft }}</p>
        <p class="ai2-rule-why">依据：{{ r.rationale }}</p>
        <div class="ai2-rule-actions">
          <button class="sa-btn sa-btn--primary" type="button" :disabled="decidingId === r.id" @click="decide(r, true)">
            批准
          </button>
          <button class="sa-btn" type="button" :disabled="decidingId === r.id" @click="decide(r, false)">
            驳回
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped src="./settings-shared.css"></style>
<style scoped>
.ai2-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
  margin: 16px 0;
}
.ai2-check {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
}
.ai2-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ai2-label {
  font-size: 12px;
  color: var(--sa-text-secondary, #9898a8);
}
.ai2-actions {
  display: flex;
  gap: 8px;
  margin: 12px 0 20px;
}
.ai2-selftest ul {
  margin: 8px 0 16px;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.8;
}
.ai2-selftest .ok {
  color: var(--sa-success, #12b76a);
}
.ai2-selftest .bad,
.ai2-knowledge .bad {
  color: var(--sa-danger, #f04438);
}
.ai2-kv {
  font-size: 13px;
  line-height: 1.8;
  margin: 4px 0;
}
.ai2-rules {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ai2-rule {
  border: 1px solid var(--sa-border, #e5e5ef);
  border-radius: 12px;
  padding: 12px 16px;
}
.ai2-rule-head {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  color: var(--sa-text-secondary, #9898a8);
}
.ai2-rule-kind {
  background: var(--sa-hover, #f2f2f7);
  border-radius: 6px;
  padding: 1px 8px;
}
.ai2-rule-draft {
  font-size: 13px;
  margin: 8px 0 4px;
}
.ai2-rule-why {
  font-size: 12px;
  color: var(--sa-text-secondary, #9898a8);
  margin: 0 0 8px;
}
.ai2-rule-actions {
  display: flex;
  gap: 8px;
}
.ai2-task {
  margin-top: 16px;
}
.ai2-task-row {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.ai2-task-row .sa-input {
  flex: 1;
}
</style>
