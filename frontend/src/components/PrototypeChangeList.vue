<script setup lang="ts">
/**
 * The files one version changed, one line each, with an icon and the action.
 *
 * The history row and the restore confirm both draw it, so the icons and the
 * colours live here once.
 */
import type { PrototypeFileChange } from '../types'

/**
 * Tailwind builds a `lucide-*` class only when it reads the literal string in
 * this project's source, so the names live here and not in the API response.
 */
const ACTION_ICONS: Record<string, string> = {
  added: 'lucide-file-plus',
  modified: 'lucide-file-pen-line',
  deleted: 'lucide-file-minus',
}

const ACTION_COLORS: Record<string, string> = {
  added: 'text-ink-green-7',
  modified: 'text-ink-blue-7',
  deleted: 'text-ink-red-7',
}

defineProps<{ changes: PrototypeFileChange[] }>()

function actionIcon(action: string): string {
  return ACTION_ICONS[action] ?? 'lucide-file'
}

function actionColor(action: string): string {
  return ACTION_COLORS[action] ?? 'text-ink-gray-6'
}
</script>

<template>
  <ul class="space-y-1 rounded-4 bg-surface-gray-2 px-3 py-2">
    <li
      v-for="change in changes"
      :key="change.path"
      class="flex items-center gap-2 text-xs text-ink-gray-7"
    >
      <span
        :class="[actionIcon(change.action), actionColor(change.action), 'size-3.5 shrink-0']"
        aria-hidden="true"
      />
      <span class="truncate">{{ change.path }}</span>
      <span class="ml-auto shrink-0 text-ink-gray-5">{{ change.action }}</span>
    </li>
  </ul>
</template>
