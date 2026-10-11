<script setup lang="ts">
/**
 * HeroUI 风格确认对话框（危险操作二次确认）。
 *
 * 深色实心危险按钮 + 明确的后果描述；不做输入校验码（项目内删除对象是
 * 待整理文件，误删代价可控，但按钮必须与常规操作隔离开）。
 */
import { NButton, NModal } from 'naive-ui'

const props = defineProps<{
  show: boolean
  title: string
  /** 后果说明（多行，支持 \n） */
  detail: string
  confirmText?: string
  cancelText?: string
  loading?: boolean
}>()

const emit = defineEmits<{
  'update:show': [v: boolean]
  confirm: []
  cancel: []
}>()
</script>

<template>
  <n-modal
    :show="props.show"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <div class="danger-dialog">
      <div class="danger-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
          <path
            d="M12 2 1 21h22L12 2zm1 14h-2v2h2v-2zm0-6h-2v4h2v-4z"
          />
        </svg>
      </div>
      <h3 class="danger-title">{{ props.title }}</h3>
      <p class="danger-detail">
        <template v-for="(line, i) in (props.detail || '').split('\n')" :key="i">
          {{ line }}<br v-if="i < (props.detail || '').split('\n').length - 1" />
        </template>
      </p>
      <div class="danger-actions">
        <n-button quaternary :disabled="props.loading" @click="emit('cancel')">
          {{ props.cancelText || '取消' }}
        </n-button>
        <n-button
          class="danger-confirm"
          :loading="props.loading"
          @click="emit('confirm')"
        >
          {{ props.confirmText || '确认删除' }}
        </n-button>
      </div>
    </div>
  </n-modal>
</template>

<style scoped>
.danger-dialog {
  width: min(420px, calc(100vw - 48px));
  padding: 28px 28px 22px;
  border-radius: 18px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.4);
  text-align: center;
}
.danger-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  border-radius: 999px;
  background: rgba(240, 82, 82, 0.14);
  color: #f05252;
  margin-bottom: 14px;
}
.danger-title {
  margin: 0 0 10px;
  font-size: 17px;
  font-weight: 800;
  color: var(--sa-text-primary);
}
.danger-detail {
  margin: 0 0 22px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--sa-text-secondary);
  overflow-wrap: anywhere;
}
.danger-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
}
/* 危险按钮：HeroUI 式实心红 */
.danger-confirm :deep(.n-button__content) {
  color: #fff;
}
.danger-confirm {
  --n-color: #e5484d;
  --n-color-hover: #ef5e63;
  --n-color-pressed: #cf3f44;
  --n-border: none;
  background: #e5484d;
  border: none;
}
.danger-confirm:hover {
  background: #ef5e63;
}
.danger-confirm:active {
  background: #cf3f44;
}
</style>
