<script setup lang="ts">
import { ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useMessage } from 'naive-ui'
import { CheckOutlined, RobotOutlined } from '@/components/icons'
import SaSelect from '@/components/SaSelect.vue'
import { useSettingsStore } from '@/stores/settings'
import {
  INGEST_DESC_DEFAULT_PROMPT,
  INGEST_TITLE_DEFAULT_PROMPT,
} from './aiLocal'
import { loadScoringAi, saveScoringAi, type ScoringAiSettings } from './scoringLocal'
import { appSettingsApi } from '@/api/appSettings'
import { computed, onMounted } from 'vue'

const message = useMessage()
const settings = useSettingsStore()
const { ingestAi: ai } = storeToRefs(settings)

// ===== 视频评分（实验功能）：本地配置，不进后端设置 =====
const scoring = ref<ScoringAiSettings>(loadScoringAi())
const aiProviderOptions = [
  { label: 'OpenAI 兼容', value: 'openai-compatible' },
  { label: 'Gemini', value: 'gemini' },
  { label: 'Anthropic Claude', value: 'anthropic' },
  { label: '本地 Ollama', value: 'ollama' },
  { label: '自定义', value: 'custom' },
]

const frameModeOptions = [
  { label: '自适应（按时长 6~16 帧）', value: 'auto' },
  { label: '固定 8 帧', value: '8' },
  { label: '固定 12 帧', value: '12' },
  { label: '固定 16 帧', value: '16' },
]

function onScoringProviderChange() {
  if (scoring.value.provider === 'ollama' && !scoring.value.base_url.trim()) {
    scoring.value.base_url = 'http://127.0.0.1:11434/v1'
  }
}

function saveScoring() {
  saveScoringAi({ ...scoring.value })
  message.success('视频评分设置已保存（仅保存在本机）')
}

// ===== 外部写入 API（实验）：Token 存服务端 app_settings，供 workbuddy 等回写评分 =====
const externalApi = ref<{ token: string }>({ token: '' })
const externalTokenLoaded = ref(false)
const endpointUrl = computed(
  () => `${window.location.origin}/api/music-videos/{视频id}/ai-score/result`,
)

onMounted(async () => {
  try {
    const all = await appSettingsApi.get()
    externalApi.value = { token: all.external_api?.token || '' }
  } catch {
    externalApi.value = { token: '' }
  } finally {
    externalTokenLoaded.value = true
  }
})

function genToken() {
  const bytes = new Uint8Array(24)
  crypto.getRandomValues(bytes)
  externalApi.value.token = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  void saveExternalToken()
}

async function saveExternalToken() {
  try {
    await appSettingsApi.patch({ external_api: { token: externalApi.value.token } })
    message.success(externalApi.value.token ? '外部写入 Token 已保存' : '外部写入已关闭（Token 已清空）')
  } catch (e) {
    message.error((e as Error).message)
  }
}

async function copyToken() {
  if (!externalApi.value.token) return
  try {
    await navigator.clipboard.writeText(externalApi.value.token)
    message.success('Token 已复制')
  } catch {
    message.error('复制失败，请手动选择复制')
  }
}

/** 从入库 AI 一键复制连接信息（key 拿不到服务端值，复制 base_url/provider/model） */
function copyFromIngestAi() {
  scoring.value.provider = ai.value.provider
  scoring.value.base_url = ai.value.base_url
  scoring.value.model = ai.value.model
  message.info('已复制服务商 / Base URL / 模型，API Key 请自行填写')
}

async function saveAi() {
  try {
    await settings.saveIngestAi()
    message.success('入库 AI 设置已保存（各设备共用）')
  } catch (e) {
    message.error((e as Error).message)
  }
}
async function resetAi() {
  try {
    await settings.resetIngestAi()
    message.success('已恢复默认')
  } catch (e) {
    message.error((e as Error).message)
  }
}
function onIngestAiProviderChange() {
  if (ai.value.provider === 'gemini' && !ai.value.base_url.trim()) {
    ai.value.base_url = 'https://generativelanguage.googleapis.com/v1beta/openai'
  }
  if (ai.value.provider === 'ollama' && !ai.value.base_url.trim()) {
    ai.value.base_url = 'http://127.0.0.1:11434/v1'
  }
}

// 只重置「中文简介」规则这一条，不动服务商 / 模型 / 密钥（那是上面那个「恢复默认」的事）
function restoreDescPrompt() {
  ai.value.desc_prompt = INGEST_DESC_DEFAULT_PROMPT
  message.success('中文简介提示词已恢复默认，记得点「保存设置」')
}

// 只重置「标题规范」这一条，其余设置不动
function restoreTitlePrompt() {
  ai.value.title_prompt = INGEST_TITLE_DEFAULT_PROMPT
  message.success('标题规范提示词已恢复默认，记得点「保存设置」')
}
</script>

<template>
  <section class="sa-section ai-panel">
    <!-- 分区名由左侧导航/移动端标签条给出，这里不重复标题，只留说明角标 -->
    <div class="section-head section-head--tools">
      <span class="section-tag">按用途分开配置</span>
    </div>
    <div class="panel">
      <div class="panel-subtitle">入库 AI</div>
      <div class="panel-grid">
        <div class="field field--wide">
          <div class="field-row">
            <span class="field-label">启用入库 AI</span>
            <label class="sa-switch">
              <input v-model="ai.enabled" type="checkbox" />
              <span class="track"><span class="thumb" /></span>
            </label>
          </div>
          <div class="field-hint">开启后在待整理详情页可使用「AI 入库」生成元数据建议</div>
        </div>
        <div class="field">
          <span class="field-label">服务商</span>
          <SaSelect v-model="ai.provider" :options="aiProviderOptions" @change="onIngestAiProviderChange" />
        </div>
        <div class="field">
          <span class="field-label">模型</span>
          <input v-model="ai.model" class="sa-input" type="text" placeholder="如 gpt-4o-mini" />
        </div>
        <div class="field field--wide">
          <span class="field-label">API Base URL</span>
          <input v-model="ai.base_url" class="sa-input" type="text" placeholder="https://api.openai.com/v1" />
        </div>
        <div class="field field--wide">
          <span class="field-label">API Key</span>
          <input
            v-model="ai.api_key"
            class="sa-input"
            type="password"
            :placeholder="ai.api_key_set ? '已保存，留空则不修改' : 'sk-…（保存在服务器，各设备共用）'"
            autocomplete="off"
          />
        </div>
        <div class="field field--wide">
          <div class="field-row">
            <span class="field-label">中文简介提示词</span>
            <button class="sa-btn sa-btn--ghost" @click="restoreDescPrompt">恢复默认</button>
          </div>
          <textarea
            v-model="ai.desc_prompt"
            class="sa-textarea"
            rows="4"
            spellcheck="false"
            placeholder="留空 = 使用内置默认口径"
          />
          <div class="field-hint">
            待整理页「AI 填写」生成中文简介（chinese_description）时遵循的规则，可整行替换。
            只写内容时会自动补上「- chinese_description:」前缀；留空或点「恢复默认」= 使用内置默认。
          </div>
        </div>
        <div class="field field--wide">
          <div class="field-row">
            <span class="field-label">标题规范提示词</span>
            <button class="sa-btn sa-btn--ghost" @click="restoreTitlePrompt">恢复默认</button>
          </div>
          <textarea
            v-model="ai.title_prompt"
            class="sa-textarea sa-textarea--tall"
            rows="12"
            spellcheck="false"
            placeholder="留空 = 使用内置默认规范"
          />
          <div class="field-hint">
            待整理页「AI 填写」生成主标题（name）时遵循的规范，默认「YYMMDD Artist - Song [Event Name Type]」，
            可整段替换。留空或点「恢复默认」= 使用内置默认。
          </div>
        </div>
      </div>
      <div class="panel-foot">
        <div class="panel-note">
          <RobotOutlined :size="13" />
          当前为「入库 AI」，只用于待整理视频的元数据建议。
        </div>
        <div class="panel-actions">
          <button class="sa-btn sa-btn--ghost" @click="resetAi">恢复默认</button>
          <button class="sa-btn sa-btn--primary" @click="saveAi">
            <CheckOutlined :size="14" />
            保存设置
          </button>
        </div>
      </div>
    </div>

    <!-- 视频评分（实验功能） -->
    <div class="panel">
      <div class="panel-subtitle">
        视频评分
        <span class="scoring-badge">实验功能</span>
      </div>
      <div class="panel-grid">
        <div class="field field--wide">
          <div class="field-row">
            <span class="field-label">启用视频评分</span>
            <label class="sa-switch">
              <input v-model="scoring.enabled" type="checkbox" />
              <span class="track"><span class="thumb" /></span>
            </label>
          </div>
          <div class="field-hint">
            开启后在视频播放页可对单条视频做 AI 质量评分（清晰度 / 曝光色彩 / 人物遮挡 /
            机位角度 / 构图）。按抽帧策略取若干帧（768px 宽）发给视觉模型，帧数越多覆盖越全、成本越高。
          </div>
        </div>
        <div class="field">
          <span class="field-label">服务商</span>
          <SaSelect v-model="scoring.provider" :options="aiProviderOptions" @change="onScoringProviderChange" />
        </div>
        <div class="field">
          <span class="field-label">视觉模型</span>
          <input v-model="scoring.model" class="sa-input" type="text" placeholder="如 qwen-vl-max / gpt-4o-mini" />
        </div>
        <div class="field">
          <span class="field-label">抽帧策略</span>
          <SaSelect v-model="scoring.frames_mode" :options="frameModeOptions" />
        </div>
        <div class="field field--wide">
          <span class="field-label">API Base URL</span>
          <input v-model="scoring.base_url" class="sa-input" type="text" placeholder="https://…/v1" />
        </div>
        <div class="field field--wide">
          <span class="field-label">API Key</span>
          <input v-model="scoring.api_key" class="sa-input" type="password" placeholder="sk-…（仅保存在本机浏览器）" autocomplete="off" />
        </div>
      </div>
      <!-- 外部集成 Token（workbuddy 等外部系统调用库操作接口） -->
      <div class="ext-block">
        <div class="field-row">
          <span class="field-label">外部集成 Token</span>
          <span class="field-hint">外部系统（workbuddy 等）免登录调用库操作接口的凭据</span>
        </div>
        <div class="ext-row">
          <code class="ext-token">{{ externalApi.token || '未生成' }}</code>
          <button class="sa-btn sa-btn--ghost" type="button" @click="copyToken" :disabled="!externalApi.token">复制</button>
          <button class="sa-btn sa-btn--ghost" type="button" @click="genToken">
            {{ externalApi.token ? '重新生成' : '生成 Token' }}
          </button>
          <button
            v-if="externalApi.token"
            class="sa-btn sa-btn--ghost danger-text"
            type="button"
            @click="externalApi.token = ''; void saveExternalToken()"
          >
            清空（停用）
          </button>
        </div>
        <div class="field-hint">
          覆盖范围：待整理/入库（/api/library/…）、视频与资料库编辑（music-videos / artists / groups /
          songs / albums / video-collections）。请求带请求头 <code>X-API-Token: 上述 Token</code> 即可，
          <b>不创建会话、不影响 Web 端登录</b>（外部登录会挤掉最老的会话导致网页被登出）。
          评分回写示例：<code>POST {{ endpointUrl }}</code>，body：<code>{"model": "…", "dims": {"sharpness": {"score": 8, "reason": "…"}}}</code>
          。清空 Token 即停用外部集成。
        </div>
      </div>

      <div class="panel-foot">
        <div class="panel-note">
          <RobotOutlined :size="13" />
          实验模块：配置只保存在本机浏览器；模型必须支持图片输入（多模态）。外部 Token 存服务端（各设备共用）。
        </div>
        <div class="panel-actions">
          <button class="sa-btn sa-btn--ghost" @click="copyFromIngestAi">从入库 AI 复制连接</button>
          <button class="sa-btn sa-btn--primary" @click="saveScoring">
            <CheckOutlined :size="14" />
            保存设置
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ext-block {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px dashed var(--sa-border-subtle);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ext-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.ext-token {
  font-family: ui-monospace, monospace;
  font-size: 12px;
  padding: 5px 10px;
  border-radius: 6px;
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  max-width: 380px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.danger-text {
  color: #f05252;
}
.ext-block .field-hint code {
  font-family: ui-monospace, monospace;
  font-size: 11px;
  background: var(--sa-subtle);
  padding: 1px 6px;
  border-radius: 4px;
  overflow-wrap: anywhere;
}

.scoring-badge {
  display: inline-block;
  margin-left: 8px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: var(--sa-accent);
  border: 1px solid var(--sa-accent-border);
  vertical-align: 2px;
}
</style>

<style scoped src="./settings-shared.css"></style>
