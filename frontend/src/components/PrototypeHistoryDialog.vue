<script setup lang="ts">
/**
 * The version history of one Prototype, newest first.
 *
 * The agent records a version at the end of each user request. The prompt is
 * what the person recognises, so it leads the row, and the meta line under it
 * holds the only other facts: when, and how many files changed. A row has no
 * version number, no badge and no button. Everything that acts on a row lives
 * in its ... menu, which shows on hover so the list reads as plain text.
 *
 * The newest row is Current. Every other row that kept a snapshot offers
 * "Restore this version", which opens the confirm step
 * (`PrototypeRestoreDialog`). A restore is recorded as a new version on top,
 * so it is never a loss: while that restore is Current, its menu offers "Undo
 * restore", which restores the version just below it.
 *
 * Undo lives in the row menu and not in a toast. A modal reka dialog sets
 * `pointer-events: none` on the body and its overlay catches every click, so a
 * toast action under an open dialog cannot be clicked: the click closes the
 * dialog instead.
 *
 * The list sits in a fixed-height scroll box, so the loading, empty, error and
 * loaded states all have the same size and the dialog never jumps.
 */
import { computed, ref, watch } from 'vue'
import { Button, Dialog, Dropdown, ErrorMessage, LoadingText, toast, useCall } from 'frappe-ui'
import PrototypeChangeList from './PrototypeChangeList.vue'
import PrototypeRestoreDialog from './PrototypeRestoreDialog.vue'
import { method } from '../store'
import type { Prototype, PrototypeVersion, RestoreResult } from '../types'

/** A prompt past this many lines or characters is clamped to two lines. */
const CLAMP_LINES = 2
const CLAMP_CHARS = 140

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
  await history.reload()
  emit('restored')
  toast.success('Version restored')
}

async function undo(): Promise<void> {
  const target = undoTarget.value
  if (!target) return
  try {
    await undoRestore.submit({
      slug: props.prototype.slug,
      sequence: target.sequence,
      dry_run: 0,
    })
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
 * A prompt long enough to clamp. Only a request row can be long: a restore or
 * created row is one short line the server wrote.
 */
function isLong(version: PrototypeVersion): boolean {
  if (version.kind !== 'request') return false
  return version.prompt.split('\n').length > CLAMP_LINES || version.prompt.length > CLAMP_CHARS
}

/**
 * The version a restore row points at, by sequence. A restore of a restore
 * follows `restored_from` down to a version the agent actually wrote. Null when
 * the row is gone, or when the chain loops.
 */
function restoreTarget(version: PrototypeVersion): PrototypeVersion | null {
  const bySequence = new Map((history.data ?? []).map((row) => [row.sequence, row]))
  const seen = new Set<number>()
  let target = version.restored_from == null ? undefined : bySequence.get(version.restored_from)
  while (target?.kind === 'restore') {
    if (seen.has(target.sequence) || target.restored_from == null) return null
    seen.add(target.sequence)
    target = bySequence.get(target.restored_from)
  }
  return target ?? null
}

/** The items of a row's ... menu. Empty means the row gets no menu at all. */
function menuOptions(version: PrototypeVersion, index: number) {
  if (index > 0 && version.restorable) {
    return [
      {
        label: 'Restore this version',
        icon: 'lucide-rotate-ccw',
        onClick: () => askRestore(version),
      },
    ]
  }
  if (index === 0 && undoTarget.value) {
    return [{ label: 'Undo restore', icon: 'lucide-undo-2', onClick: undo }]
  }
  return []
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

function versionLabel(count: number): string {
  return `${count} ${count === 1 ? 'version' : 'versions'}`
}

function fileLabel(count: number): string {
  return `${count} ${count === 1 ? 'file' : 'files'}`
}
</script>

<template>
  <Dialog v-model:open="open" size="xl">
    <!-- Same classes as the Dialog's own title, with the count beside it. -->
    <template #title>
      <h3 class="text-2xl-semibold leading-6 text-ink-gray-8">
        History
        <span v-if="history.data?.length" class="text-base text-ink-gray-5">
          {{ versionLabel(history.data.length) }}
        </span>
      </h3>
    </template>

    <template #default>
      <!--
        `-mx-3 px-3` gives the rows' hover surface room to bleed past the text.
        The scroll box clips on x, so without it the surface would end flush
        with the text.
      -->
      <div class="-mx-3 h-96 overflow-y-auto px-3">
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
          The divider is a `before:` hairline, not `border-t`: a top border on
          a rounded row bends up into the corners. Inset by the row padding so
          it spans the text, and hidden above and below a hovered row so the
          rounded surface never meets a line.
        -->
        <ul v-else class="-mx-3">
          <li
            v-for="(version, index) in history.data"
            :key="version.name"
            class="group relative rounded-6 px-3 py-2.5 before:absolute before:inset-x-3 before:top-0 before:border-t before:border-outline-gray-1 first:before:hidden hover:bg-surface-gray-2 hover:before:hidden [&:hover+li]:before:hidden"
          >
            <!--
              Hidden until the row is hovered or focused, and held visible
              while its menu is open (reka stamps `data-state="open"` on the
              trigger). Line 1 reserves `pr-8` so the prompt never runs under it.
            -->
            <div
              v-if="menuOptions(version, index).length"
              class="absolute right-2 top-2 opacity-0 focus-within:opacity-100 group-hover:opacity-100 has-[[data-state=open]]:opacity-100"
            >
              <Dropdown align="end" :options="menuOptions(version, index)">
                <Button
                  aria-label="Version actions"
                  icon="lucide-ellipsis"
                  size="sm"
                  variant="ghost"
                />
              </Dropdown>
            </div>

            <div class="pr-8">
              <!-- A restore row names the version it went back to. -->
              <p
                v-if="version.kind === 'restore'"
                class="flex items-start gap-2 text-p-base text-ink-gray-5"
              >
                <span
                  class="lucide-undo-2 mt-0.5 size-4 shrink-0 text-ink-gray-5"
                  aria-hidden="true"
                />
                <span class="line-clamp-1 min-w-0">
                  <template v-if="restoreTarget(version)">
                    Restored to:
                    <span class="text-ink-gray-7"
                      >&ldquo;{{ restoreTarget(version)?.prompt }}&rdquo;</span
                    >
                  </template>
                  <template v-else
                    >Restored version {{ version.restored_from ?? version.sequence }}</template
                  >
                </span>
              </p>

              <p
                v-else-if="version.kind === 'created'"
                class="flex items-start gap-2 text-p-base text-ink-gray-7"
              >
                <span
                  class="lucide-sparkles mt-0.5 size-4 shrink-0 text-ink-gray-5"
                  aria-hidden="true"
                />
                <span class="min-w-0">{{ version.prompt }}</span>
              </p>

              <!-- A long prompt is its own toggle, so it needs no button. -->
              <button
                v-else-if="isLong(version)"
                type="button"
                class="w-full cursor-pointer whitespace-pre-line text-left text-p-base text-ink-gray-8"
                :class="{ 'line-clamp-2': !openPrompts.has(version.name) }"
                :aria-expanded="openPrompts.has(version.name)"
                @click="openPrompts = toggle(openPrompts, version.name)"
              >
                {{ version.prompt }}
              </button>
              <p v-else class="whitespace-pre-line text-p-base text-ink-gray-8">
                {{ version.prompt }}
              </p>
            </div>

            <p class="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-sm text-ink-gray-5">
              <template v-if="index === 0">
                <span class="font-medium text-ink-gray-7">Current</span>
                <span aria-hidden="true">&middot;</span>
              </template>
              <span :title="version.creation">{{ version.created }}</span>
              <template v-if="version.changes.length">
                <span aria-hidden="true">&middot;</span>
                <button
                  type="button"
                  class="inline-flex items-center gap-0.5 hover:text-ink-gray-7"
                  :aria-expanded="openFiles.has(version.name)"
                  @click="openFiles = toggle(openFiles, version.name)"
                >
                  {{ fileLabel(version.changes.length) }}
                  <span
                    :class="[chevronFor(openFiles.has(version.name)), 'size-3']"
                    aria-hidden="true"
                  />
                </button>
              </template>
            </p>

            <PrototypeChangeList
              v-if="openFiles.has(version.name)"
              class="mt-2"
              :changes="version.changes"
            />
          </li>
        </ul>
      </div>

      <!-- Only when a row lacks a snapshot, so most dialogs never show it. -->
      <p v-if="hasLegacy" class="mt-3 text-xs text-ink-gray-5">
        Versions recorded before restore existed can't be restored.
      </p>

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
