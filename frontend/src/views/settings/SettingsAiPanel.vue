<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { useMessage } from 'naive-ui'
import { CheckOutlined, RobotOutlined } from '@/components/icons'
import SaSelect from '@/components/SaSelect.vue'
import { useSettingsStore } from '@/stores/settings'
import { INGEST_DESC_DEFAULT_PROMPT } from './aiLocal'

const message = useMessage()
const settings = useSettingsStore()
const { ingestAi: ai } = storeToRefs(settings)

const aiProviderOptions = [
  { label: 'OpenAI 兼容', value: 'openai-compatible' },
  { label: 'Gemini', value: 'gemini' },
  { label: 'Anthropic Claude', value: 'anthropic' },
  { label: '本地 Ollama', value: 'ollama' },
  { label: '自定义', value: 'custom' },
]

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
  </section>
</template>

<style scoped src="./settings-shared.css"></style>
