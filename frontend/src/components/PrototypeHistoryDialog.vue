<script setup lang="ts">
/**
 * The version history of one Prototype, newest first.
 *
 * The agent records a version at the end of each user request. The prompt is
 * what the person recognises, so it leads the row. Everything else is meta.
 *
 * The newest row is Current. Every other row that kept a snapshot has a
 * Restore button, which opens the confirm step (`PrototypeRestoreDialog`). A
 * restore is recorded as a new version on top, so it is never a loss: while
 * that restore is Current, its row carries an Undo that restores the version
 * just below it.
 *
 * Undo lives on the row and not in a toast. A modal reka dialog sets
 * `pointer-events: none` on the body and its overlay catches every click, so a
 * toast action under an open dialog cannot be clicked: the click closes the
 * dialog instead.
 *
 * The list sits in a fixed-height scroll box, so the loading, empty, error and
 * loaded states all have the same size and the dialog never jumps.
 */
import { computed, ref, watch } from 'vue'
import {
  Badge,
  Button,
  Dialog,
  ErrorMessage,
  LoadingText,
  toast,
  useCall,
} from 'frappe-ui'
import PrototypeChangeList from './PrototypeChangeList.vue'
import PrototypeRestoreDialog from './PrototypeRestoreDialog.vue'
import { method } from '../store'
import type { Prototype, PrototypeVersion, RestoreResult } from '../types'

/**
 * The glyph before the text of a row the agent did not write a request for.
 * A request row has none. Tailwind builds a `lucide-*` class only when it
 * reads the literal string in this project's source, so the names live here.
 */
const KIND_ICONS: Record<string, string> = {
  restore: 'lucide-undo-2',
  created: 'lucide-sparkles',
}

const props = defineProps<{ prototype: Prototype }>()
const open = defineModel<boolean>('open', { required: true })
// The card reloads the gallery on this, so the thumbnail and "Updated" move.
const emit = defineEmits<{ restored: [] }>()

const history = useCall<PrototypeVersion[], { slug: string }>({
  url: method('list_versions'),
  params: () => ({ slug: props.prototype.slug }),
  immediate: false,
  initialData: [],
})

// Same method as the confirm step, with the dry run off. Undo needs no preview:
// it puts back the tree the person was just looking at, as a new version.
const undoRestore = useCall<RestoreResult, { slug: string; sequence: number; dry_run: 0 }>({
  url: method('restore_version'),
  method: 'POST',
  immediate: false,
})

/** The version the confirm step is about. Kept after close, for the fade. */
const restoring = ref<PrototypeVersion | null>(null)
const confirmOpen = ref(false)

/** Versions whose full prompt is shown, by version name. */
const openPrompts = ref<Set<string>>(new Set())
/** Versions whose file list is shown, by version name. */
const openFiles = ref<Set<string>>(new Set())

// The card holds one dialog per Prototype, so the fetch waits for the open.
// Nothing loads while the gallery only renders the cards.
watch(open, (value) => {
  if (!value) return
  openPrompts.value = new Set()
  openFiles.value = new Set()
  history.reload()
})

/** True when some row has no snapshot, so the footer can say why. */
const hasLegacy = computed(() => history.data?.some((version) => !version.restorable))

function askRestore(version: PrototypeVersion): void {
  restoring.value = version
  confirmOpen.value = true
}

/**
 * The version Undo puts back: the row under a Current restore, which is the
 * tree as it stood right before that restore (a pending change was committed
 * as its own version first). None when row 0 is not a restore, or when the
 * row under it is a legacy row with no snapshot.
 */
const undoTarget = computed(() => {
  const [current, before] = history.data ?? []
  if (current?.kind !== 'restore' || !before?.restorable) return null
  return before
})

/** Show the new version and tell the card. The new row carries the Undo. */
async function onRestored(): Promise<void> {
  const target = restoring.value
  await history.reload()
  emit('restored')
  toast.success(`Restored to version ${target?.sequence}`)
}

async function undo(): Promise<void> {
  const target = undoTarget.value
  if (!target) return
  try {
    await undoRestore.submit({ slug: props.prototype.slug, sequence: target.sequence, dry_run: 0 })
  } catch (error) {
    toast.error((error as Error).message)
    return
  }
  await history.reload()
  emit('restored')
  toast.success('Undid the restore')
}

function toggle(set: Set<string>, name: string): Set<string> {
  const next = new Set(set)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  return next
}

/**
 * A prompt long enough to clamp. Three lines is the clamp. Only a request row
 * can be long: a restore or created row is one short line the server wrote.
 */
function isLong(version: PrototypeVersion): boolean {
  if (version.kind !== 'request') return false
  return version.prompt.split('\n').length > 3 || version.prompt.length > 180
}

/**
 * The disclosure glyph, shared by both toggles in a row.
 *
 * Tailwind only builds a `lucide-*` class it reads as a literal in this
 * project's source, so both names stay written out here.
 */
function chevronFor(isOpen: boolean): string {
  return isOpen ? 'lucide-chevron-down' : 'lucide-chevron-right'
}

function fileLabel(count: number): string {
  return `${count} ${count === 1 ? 'file' : 'files'}`
}
</script>

<template>
  <Dialog v-model:open="open" size="xl" :title="`${prototype.title} history`">
    <template #default>
      <div class="h-96 overflow-y-auto">
        <LoadingText v-if="history.loading && !history.data?.length" />

        <ErrorMessage v-else-if="history.error" :message="history.error.message" />

        <!--
          The one empty-state recipe the app uses: 48px circle, 24px glyph,
          `gap-3`. It matches the gallery's empty state on the Prototypes
          screen. `py-16` becomes `h-full` because the scroll box above
          already fixes the height.
        -->
        <div
          v-else-if="!history.data?.length"
          class="flex h-full flex-col items-center justify-center gap-3 text-center"
        >
          <span
            class="grid size-12 place-items-center rounded-full bg-surface-gray-2 text-ink-gray-5"
          >
            <span class="lucide-history size-6" aria-hidden="true" />
          </span>
          <p class="text-base-medium text-ink-gray-8">No version yet</p>
          <p class="max-w-sm text-p-sm text-ink-gray-5">
            The agent records a version each time it changes this prototype.
          </p>
        </div>

        <!--
          A read-only list: the row itself is not a target, so it carries no
          hover surface and no gutter bleed. The separator is `divide-y` on the
          list, not `border-b` on a rounded row, which used to draw a straight
          rule across a rounded corner.
        -->
        <ul v-else class="divide-y divide-outline-gray-1">
          <li
            v-for="(version, index) in history.data"
            :key="version.name"
            class="py-3 first:pt-0"
          >
            <div class="flex items-start gap-3">
              <!--
                A restore or created row leads with a glyph and a quieter ink,
                so it reads as a mark in the timeline and not as something the
                person asked for. `mt-0.5` centres the 16px glyph on the first
                line of `text-p-base`.
              -->
              <div class="flex min-w-0 flex-1 items-start gap-2">
                <span
                  v-if="KIND_ICONS[version.kind]"
                  :class="[KIND_ICONS[version.kind], 'mt-0.5 size-4 shrink-0 text-ink-gray-5']"
                  aria-hidden="true"
                />
                <p
                  class="min-w-0 flex-1 whitespace-pre-line text-p-base"
                  :class="[
                    version.kind === 'request' ? 'text-ink-gray-8' : 'text-ink-gray-7',
                    openPrompts.has(version.name) ? '' : 'line-clamp-3',
                  ]"
                >
                  {{ version.prompt }}
                </p>
              </div>
              <!--
                A fixed trailing column, per DESIGN.md principle 5. `created`
                is `pretty_date` in long form (`sketch/api.py:241`), so the
                text runs from "just now" to "22 minutes ago". With "v12 · "
                in front, `w-36` holds the longest of those on one line and
                every prompt then ends on the same x. `tabular-nums` keeps the
                version number from shifting the digits.
              -->
              <span
                class="w-36 shrink-0 whitespace-nowrap text-right text-xs tabular-nums text-ink-gray-5"
                :title="version.creation"
              >
                v{{ version.sequence }} &middot; {{ version.created }}
              </span>
            </div>

            <p v-if="version.summary" class="mt-1 text-p-sm text-ink-gray-6">
              {{ version.summary }}
            </p>

            <div class="mt-2 flex flex-wrap items-center gap-2">
              <!-- The newest row is the live tree, so it has nothing to restore. -->
              <Badge v-if="index === 0" label="Current" size="sm" theme="gray" />
              <Badge
                v-if="version.files_added"
                :label="`${version.files_added} added`"
                size="sm"
                theme="green"
              />
              <Badge
                v-if="version.files_modified"
                :label="`${version.files_modified} changed`"
                size="sm"
                theme="blue"
              />
              <Badge
                v-if="version.files_deleted"
                :label="`${version.files_deleted} deleted`"
                size="sm"
                theme="red"
              />

              <!--
                Both toggles are disclosures, so both carry the same chevron:
                right when closed, down when open. `chevronFor` keeps the two
                literal class names in one place for Tailwind's scanner.
              -->
              <Button
                v-if="version.changes.length"
                :icon-left="chevronFor(openFiles.has(version.name))"
                :label="fileLabel(version.changes.length)"
                size="sm"
                variant="ghost"
                @click="openFiles = toggle(openFiles, version.name)"
              />

              <Button
                v-if="isLong(version)"
                :icon-left="chevronFor(openPrompts.has(version.name))"
                :label="openPrompts.has(version.name) ? 'Show less' : 'Show full prompt'"
                size="sm"
                variant="ghost"
                @click="openPrompts = toggle(openPrompts, version.name)"
              />

              <!--
                At the right end of the row, where the eye ends. The label
                names the version for a screen reader, because every row says
                just "Restore".
              -->
              <Button
                v-if="index > 0 && version.restorable"
                :aria-label="`Restore version ${version.sequence}`"
                class="ml-auto"
                icon-left="lucide-rotate-ccw"
                label="Restore"
                size="sm"
                variant="ghost"
                @click="askRestore(version)"
              />

              <!-- Same spot as Restore, on the one row that can be taken back. -->
              <Button
                v-if="index === 0 && undoTarget"
                :aria-label="`Undo, back to version ${undoTarget.sequence}`"
                class="ml-auto"
                icon-left="lucide-undo-2"
                label="Undo"
                :loading="undoRestore.loading"
                size="sm"
                variant="ghost"
                @click="undo"
              />
            </div>

            <PrototypeChangeList
              v-if="openFiles.has(version.name)"
              class="mt-2"
              :changes="version.changes"
            />
          </li>
        </ul>
      </div>

      <!--
        The Runtime pin, at meta size. It used to lead the card subtitle, which
        made the library version the loudest fact about a Prototype and told a
        new user nothing they could act on (review 5.8); `sketch/api.py:250`
        still returns it, and this is where it lands. It is one build detail
        for the whole Prototype, not a per-version fact, so it sits under the
        list and never becomes a heading. The value is constant while the
        dialog is open, so this row cannot move the panel.
      -->
      <div class="mt-3 space-y-1 border-t border-outline-gray-1 pt-3 text-xs text-ink-gray-5">
        <!-- Only when a row lacks a snapshot, so most dialogs never show it. -->
        <p v-if="hasLegacy">Versions recorded before restore existed can't be restored.</p>
        <p class="truncate">Version {{ prototype.pin }}</p>
      </div>

      <!--
        Nested over this dialog. Both are reka dialogs, which stack: the later
        portal sits on top, Escape closes only the top one, and focus returns
        here when it closes.
      -->
      <PrototypeRestoreDialog
        v-model:open="confirmOpen"
        :slug="prototype.slug"
        :version="restoring"
        @restored="onRestored"
      />
    </template>
  </Dialog>
</template>
