<script setup lang="ts">
// SaveImportDialog: import saves, save states and their screenshots from the
// user's own RetroArch / ES-DE / emulator folders. The browser reads the
// folders; the server matches each file to a game and emulator; the user
// reviews; uploads go through RomM's own /api/saves and /api/states. Nothing
// already in RomM is overwritten (a taken name is kept as a new version or
// skipped, the user's choice) and nothing on the device is changed.
import {
  RAlert,
  RBtn,
  RCheckbox,
  RDialog,
  RIcon,
  RProgressLinear,
  RSelect,
  RSwitch,
  RTag,
} from "@v2/lib";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import romshelfApi, {
  installErrorOf,
  type SaveImportGame,
  type SaveImportOptions,
  type SaveImportOverride,
  type SaveImportPlanInput,
  type SaveImportRow,
} from "@/services/api/romshelf";
import {
  type PickedFile,
  type ScanResult,
  deviceRoms,
  md5Of,
  pickedFiles,
  saveStem,
  scanFolders,
} from "@/v2/components/Romshelf/saveScan";

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ (e: "update:modelValue", v: boolean): void }>();
const { t } = useI18n();

type Phase = "setup" | "scanning" | "review" | "importing" | "done";
type Choice = "keep" | "skip";
interface Result {
  ok: boolean;
  message: string;
}

const options = ref<SaveImportOptions | null>(null);
const error = ref<string | null>(null);
const phase = ref<Phase>("setup");
const progress = ref<{ done: number; total: number; label: string } | null>(
  null,
);

const platformId = ref<number | null>(null);
const detect = ref(true);
const systemEmulator = ref<string | null>(null);
const folders = ref<{ root: string; files: PickedFile[] }[]>([]);
const folderInput = ref<HTMLInputElement | null>(null);

const scan = ref<ScanResult | null>(null);
const md5s = new Map<string, string>();
let romList: { path: string; crc32?: string | undefined }[] = [];
const rows = ref<SaveImportRow[]>([]);
const overrides = ref<Record<string, SaveImportOverride>>({});
const choices = ref<Record<string, Choice>>({});
const excluded = ref<Record<string, boolean>>({});
const results = ref<Record<string, Result>>({});
const planning = ref(false);

watch(
  () => props.modelValue,
  async (open) => {
    if (!open || options.value) return;
    try {
      options.value = (await romshelfApi.getSaveImportOptions()).data;
    } catch (err) {
      error.value = installErrorOf(err).message;
    }
  },
  { immediate: true },
);

const platformItems = computed(() => [
  { title: t("romshelf.saves-all-systems"), value: null },
  ...(options.value?.platforms ?? []).map((p) => ({
    title: p.name,
    value: p.id,
  })),
]);
const emulatorItems = computed(() => {
  const names = new Set(options.value?.emulators ?? []);
  for (const r of rows.value) if (r.emulator) names.add(r.emulator);
  return [
    { title: t("romshelf.saves-no-emulator"), value: "" },
    ...[...names].sort().map((e) => ({ title: e, value: e })),
  ];
});

function addFolder(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = pickedFiles(input.files);
  input.value = "";
  if (!files.length) return;
  const root = files[0]!.path.split("/")[0] ?? files[0]!.path;
  folders.value = [
    ...folders.value.filter((f) => f.root !== root),
    { root, files },
  ];
}

function removeFolder(root: string) {
  folders.value = folders.value.filter((f) => f.root !== root);
}

async function planInput(hashStems?: Set<string>): Promise<SaveImportPlanInput> {
  const s = scan.value!;
  if (hashStems) {
    const stems = new Set([...s.saves.keys()].map(saveStem));
    romList = await deviceRoms(s.others, stems, hashStems);
  }
  return {
    files: [...s.saves].map(([path, file]) => ({
      path,
      size: file.size,
      md5: md5s.get(path) ?? "",
      modified: file.lastModified / 1000,
    })),
    device_roms: romList,
    playlists: s.playlists,
    gamelists: s.gamelists,
    es_systems: s.esSystems,
    platform_id: platformId.value,
    detect_emulator: detect.value,
    platform_emulators:
      platformId.value !== null && systemEmulator.value
        ? { [platformId.value]: systemEmulator.value }
        : {},
    overrides: overrides.value,
  };
}

async function plan(hashStems?: Set<string>): Promise<SaveImportRow[]> {
  planning.value = true;
  try {
    const { data } = await romshelfApi.planSaveImport(
      await planInput(hashStems),
    );
    rows.value = data.rows;
    return data.rows;
  } finally {
    planning.value = false;
  }
}

async function startScan() {
  error.value = null;
  phase.value = "scanning";
  try {
    progress.value = { done: 0, total: 0, label: t("romshelf.saves-reading") };
    const s = await scanFolders(folders.value.flatMap((f) => f.files));
    scan.value = s;
    const entries = [...s.saves];
    progress.value = { done: 0, total: entries.length, label: t("romshelf.saves-hashing") };
    for (const [path, file] of entries) {
      if (!md5s.has(path)) md5s.set(path, await md5Of(file));
      progress.value!.done++;
    }
    progress.value = { done: 0, total: 0, label: t("romshelf.saves-matching") };
    const first = await plan(new Set());
    // Saves that matched no game by name: checksum the device ROMs named like them.
    const missing = new Set(
      first
        .filter((r) => r.status === "unmatched" || r.status === "ambiguous")
        .map((r) => saveStem(r.path)),
    );
    if (missing.size) await plan(missing);
    phase.value = "review";
  } catch (err) {
    error.value = installErrorOf(err).message;
    phase.value = "setup";
  } finally {
    progress.value = null;
  }
}

async function replanWith(path: string, patch: SaveImportOverride) {
  overrides.value = {
    ...overrides.value,
    [path]: { ...overrides.value[path], ...patch },
  };
  try {
    await plan();
  } catch (err) {
    error.value = installErrorOf(err).message;
  }
}

const renameRows = computed(() =>
  rows.value.filter(
    (r) => r.renamed_name && (r.status === "new" || r.status === "conflict"),
  ),
);
async function renameAll(rename: boolean) {
  const next = { ...overrides.value };
  for (const r of renameRows.value) next[r.path] = { ...next[r.path], rename };
  overrides.value = next;
  await plan();
}

function gameItems(row: SaveImportRow) {
  const games: SaveImportGame[] = row.candidates.length > 1
    ? row.candidates
    : (options.value?.games ?? []).filter(
        (g) => platformId.value === null || g.platform_id === platformId.value,
      );
  return games.map((g) => ({
    title: `${g.fs_name_no_ext} · ${g.platform_name}`,
    value: g.id,
  }));
}

function setEmulator(row: SaveImportRow, emulator: string) {
  void replanWith(row.path, emulator
    ? { emulator, skip_emulator: false }
    : { emulator: "", skip_emulator: true });
}

function uploadName(row: SaveImportRow): string | null {
  if (row.status === "new") return excluded.value[row.path] ? null : row.target_name;
  if (row.status === "conflict" && choices.value[row.path] === "keep") {
    return row.keep_both_name;
  }
  return null;
}

const toImport = computed(() => rows.value.filter((r) => uploadName(r)));
const counts = computed(() => {
  const c: Record<string, number> = {};
  for (const r of rows.value) c[r.status] = (c[r.status] ?? 0) + 1;
  return c;
});
const undecided = computed(
  () => rows.value.filter((r) => r.status === "conflict" && !choices.value[r.path]).length,
);

async function runImport() {
  error.value = null;
  const wanted = new Map(
    toImport.value.map((r) => [r.path, uploadName(r)] as const),
  );
  phase.value = "importing";
  results.value = {};
  try {
    // Plan again right before uploading, so a save that reached RomM meanwhile
    // (Cloud Sync, another tab) is never written over.
    const fresh = await plan();
    const queue = fresh.filter((r) => wanted.has(r.path));
    progress.value = { done: 0, total: queue.length, label: t("romshelf.saves-uploading") };
    for (const row of queue) {
      const name = uploadName(row);
      if (!name || name !== wanted.get(row.path) || !row.rom) {
        results.value[row.path] = { ok: false, message: t("romshelf.saves-changed") };
      } else {
        try {
          const shot = row.screenshot_path
            ? scan.value!.saves.get(row.screenshot_path)
            : undefined;
          await romshelfApi.uploadSaveFile({
            kind: row.kind === "state" ? "state" : "save",
            romId: row.rom.id,
            emulator: row.emulator,
            file: scan.value!.saves.get(row.path)!,
            fileName: name,
            screenshot: shot,
            screenshotName: shot ? `${name}.png` : undefined,
          });
          results.value[row.path] = { ok: true, message: name };
        } catch (err) {
          results.value[row.path] = { ok: false, message: installErrorOf(err).message };
        }
      }
      progress.value!.done++;
    }
    phase.value = "done";
  } catch (err) {
    error.value = installErrorOf(err).message;
    phase.value = "review";
  } finally {
    progress.value = null;
  }
}

const imported = computed(
  () => Object.values(results.value).filter((r) => r.ok).length,
);

function reset() {
  phase.value = "setup";
  rows.value = [];
  overrides.value = {};
  choices.value = {};
  excluded.value = {};
  results.value = {};
  scan.value = null;
}

function close() {
  emit("update:modelValue", false);
  if (phase.value === "done") reset();
}

const STATUS_TONE: Record<string, "success" | "neutral" | "warning" | "danger" | "info"> = {
  new: "success",
  in_romm: "neutral",
  conflict: "warning",
  ambiguous: "warning",
  unmatched: "danger",
  unsupported: "neutral",
};

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
</script>

<template>
  <RDialog
    :model-value="modelValue"
    icon="mdi-content-save-move"
    width="1100"
    scroll-content
    full-height-on-mobile
    :persistent="phase === 'importing' || phase === 'scanning'"
    @update:model-value="close"
  >
    <template #header>{{ t("romshelf.saves-title") }}</template>
    <template #content>
      <div class="r-v2-rs-saves">
        <RAlert v-if="error" type="error" density="compact" :text="error" />
        <p v-if="options" class="r-v2-rs-saves__lede">
          {{ t("romshelf.saves-lede", { user: options.user }) }}
        </p>

        <!-- Setup: systems, folders, emulator detection -->
        <template v-if="phase === 'setup'">
          <div class="r-v2-rs-saves__setup">
            <RSelect
              v-model="platformId"
              :items="platformItems"
              :label="t('romshelf.saves-system')"
              prefix-label="stacked"
              searchable
              hide-details
            />
            <div class="r-v2-rs-saves__detect">
              <RSwitch v-model="detect" :label="t('romshelf.saves-detect')" />
              <p class="r-v2-rs-saves__hint">{{ t("romshelf.saves-detect-hint") }}</p>
            </div>
            <RSelect
              v-if="!detect && platformId !== null"
              v-model="systemEmulator"
              :items="emulatorItems"
              :label="t('romshelf.saves-system-emulator')"
              prefix-label="stacked"
              searchable
              hide-details
            />
          </div>

          <h3 class="r-v2-rs-saves__h">{{ t("romshelf.saves-folders") }}</h3>
          <p class="r-v2-rs-saves__hint">{{ t("romshelf.saves-folders-hint") }}</p>
          <ul v-if="folders.length" class="r-v2-rs-saves__folders">
            <li v-for="f in folders" :key="f.root">
              <RIcon icon="mdi-folder" size="16" />
              <span>{{ f.root }}</span>
              <span class="r-v2-rs-saves__muted">
                {{ t("romshelf.saves-files", { n: f.files.length }) }}
              </span>
              <RBtn
                variant="text"
                size="x-small"
                icon="mdi-close"
                :aria-label="t('romshelf.saves-remove-folder')"
                @click="removeFolder(f.root)"
              />
            </li>
          </ul>
          <input
            ref="folderInput"
            type="file"
            class="r-v2-rs-saves__input"
            :aria-label="t('romshelf.saves-add-folder')"
            webkitdirectory
            multiple
            @change="addFolder"
          />
          <div class="r-v2-rs-saves__actions">
            <RBtn variant="outlined" prepend-icon="mdi-folder-plus" @click="folderInput?.click()">
              {{ t("romshelf.saves-add-folder") }}
            </RBtn>
            <RBtn
              color="primary"
              prepend-icon="mdi-magnify"
              :disabled="!folders.length || !options"
              @click="startScan"
            >
              {{ t("romshelf.saves-scan") }}
            </RBtn>
          </div>
        </template>

        <!-- Progress -->
        <div v-if="progress" class="r-v2-rs-saves__progress">
          <span>{{ progress.label }}</span>
          <span v-if="progress.total">{{ progress.done }} / {{ progress.total }}</span>
          <RProgressLinear
            :model-value="progress.total ? (100 * progress.done) / progress.total : 0"
            :indeterminate="!progress.total"
          />
        </div>

        <!-- Review and results -->
        <template v-if="phase === 'review' || phase === 'importing' || phase === 'done'">
          <div class="r-v2-rs-saves__summary">
            <RTag
              v-for="(n, status) in counts"
              :key="status"
              :tone="STATUS_TONE[status] ?? 'neutral'"
              size="small"
              :text="t(`romshelf.saves-status-${status}`, { n })"
            />
          </div>

          <RAlert
            v-if="phase === 'done'"
            type="success"
            density="compact"
            :text="t('romshelf.saves-done', { n: imported })"
          />

          <div v-if="phase === 'review' && renameRows.length" class="r-v2-rs-saves__ask">
            <p>{{ t("romshelf.saves-rename-ask", { n: renameRows.length }) }}</p>
            <RBtn size="small" variant="outlined" :loading="planning" @click="renameAll(true)">
              {{ t("romshelf.saves-rename-all") }}
            </RBtn>
            <RBtn size="small" variant="text" :loading="planning" @click="renameAll(false)">
              {{ t("romshelf.saves-rename-none") }}
            </RBtn>
          </div>
          <RAlert
            v-if="phase === 'review' && undecided"
            type="warning"
            density="compact"
            :text="t('romshelf.saves-conflicts', { n: undecided })"
          />

          <div class="r-v2-rs-saves__table-wrap">
            <table class="r-v2-rs-saves__table">
              <thead>
                <tr>
                  <th>{{ t("romshelf.saves-col-file") }}</th>
                  <th>{{ t("romshelf.saves-col-game") }}</th>
                  <th>{{ t("romshelf.saves-col-emulator") }}</th>
                  <th>{{ t("romshelf.saves-col-action") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in rows" :key="row.path" :class="`is-${row.status}`">
                  <td>
                    <div class="r-v2-rs-saves__file">
                      <RTag size="x-small" :text="t(`romshelf.saves-kind-${row.kind}`)" />
                      <span :title="row.path">{{ row.name }}</span>
                    </div>
                    <div class="r-v2-rs-saves__muted" :title="row.path">
                      {{ row.path }} · {{ sizeLabel(row.size) }}
                      <template v-if="row.screenshot_path"> · {{ t("romshelf.saves-with-shot") }}</template>
                    </div>
                    <RCheckbox
                      v-if="row.renamed_name && phase === 'review'"
                      :model-value="row.rename"
                      size="sm"
                      hide-details
                      :label="t('romshelf.saves-rename-to', { name: row.renamed_name })"
                      @update:model-value="replanWith(row.path, { rename: !!$event })"
                    />
                    <div v-else-if="row.rename" class="r-v2-rs-saves__muted">
                      → {{ row.target_name }}
                    </div>
                  </td>
                  <td>
                    <template v-if="row.status === 'unsupported'">—</template>
                    <RSelect
                      v-else-if="phase === 'review'"
                      :model-value="row.rom?.id ?? null"
                      :items="gameItems(row)"
                      :placeholder="t('romshelf.saves-pick-game')"
                      density="compact"
                      searchable
                      hide-details
                      @update:model-value="replanWith(row.path, { rom_id: $event as number })"
                    />
                    <span v-else>{{ row.rom?.fs_name_no_ext ?? "—" }}</span>
                    <div v-if="row.rom" class="r-v2-rs-saves__muted">
                      {{ row.rom.platform_name }}
                      <template v-if="row.match && row.match !== 'exact'">
                        · {{ t(`romshelf.saves-match-${row.match}`) }}
                      </template>
                    </div>
                  </td>
                  <td>
                    <template v-if="!row.rom">—</template>
                    <RSelect
                      v-else-if="phase === 'review'"
                      :model-value="row.emulator ?? ''"
                      :items="emulatorItems"
                      density="compact"
                      searchable
                      hide-details
                      @update:model-value="setEmulator(row, ($event as string) ?? '')"
                    />
                    <span v-else>{{ row.emulator ?? "—" }}</span>
                    <div v-if="row.emulator_source" class="r-v2-rs-saves__muted">
                      {{ row.emulator_source }}
                    </div>
                  </td>
                  <td>
                    <template v-if="results[row.path]">
                      <RTag
                        size="small"
                        :tone="results[row.path]!.ok ? 'success' : 'danger'"
                        :text="results[row.path]!.ok ? t('romshelf.saves-imported') : t('romshelf.saves-failed')"
                      />
                      <div class="r-v2-rs-saves__muted">{{ results[row.path]!.message }}</div>
                    </template>
                    <template v-else>
                      <RTag
                        size="small"
                        :tone="STATUS_TONE[row.status] ?? 'neutral'"
                        :text="t(`romshelf.saves-state-${row.status}`)"
                      />
                      <RCheckbox
                        v-if="row.status === 'new' && phase === 'review'"
                        :model-value="!excluded[row.path]"
                        size="sm"
                        hide-details
                        :label="t('romshelf.saves-include')"
                        @update:model-value="excluded[row.path] = !$event"
                      />
                      <div v-if="row.status === 'conflict' && phase === 'review'" class="r-v2-rs-saves__choice">
                        <RBtn
                          size="x-small"
                          :variant="choices[row.path] === 'keep' ? 'flat' : 'outlined'"
                          @click="choices[row.path] = 'keep'"
                        >
                          {{ t("romshelf.saves-keep-both") }}
                        </RBtn>
                        <RBtn
                          size="x-small"
                          :variant="choices[row.path] === 'skip' ? 'flat' : 'outlined'"
                          @click="choices[row.path] = 'skip'"
                        >
                          {{ t("romshelf.saves-skip") }}
                        </RBtn>
                      </div>
                      <div v-if="row.reason" class="r-v2-rs-saves__muted">{{ row.reason }}</div>
                      <div
                        v-if="row.status === 'conflict' && choices[row.path] === 'keep'"
                        class="r-v2-rs-saves__muted"
                      >
                        → {{ row.keep_both_name }}
                      </div>
                    </template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="r-v2-rs-saves__actions">
            <RBtn v-if="phase !== 'importing'" variant="text" @click="reset">
              {{ t("romshelf.saves-start-over") }}
            </RBtn>
            <RBtn
              v-if="phase === 'review'"
              color="primary"
              prepend-icon="mdi-upload"
              :disabled="!toImport.length || planning"
              @click="runImport"
            >
              {{ t("romshelf.saves-import", { n: toImport.length }) }}
            </RBtn>
          </div>
        </template>
      </div>
    </template>
  </RDialog>
</template>

<style scoped>
.r-v2-rs-saves {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.r-v2-rs-saves__lede,
.r-v2-rs-saves__hint {
  margin: 0;
  color: var(--r-color-fg-secondary);
  font-size: 0.875rem;
}
.r-v2-rs-saves__setup {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
  align-items: start;
}
.r-v2-rs-saves__h {
  margin: 8px 0 0;
  font-size: 1rem;
}
.r-v2-rs-saves__folders {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.r-v2-rs-saves__folders li {
  display: flex;
  align-items: center;
  gap: 8px;
}
.r-v2-rs-saves__input {
  display: none;
}
.r-v2-rs-saves__actions,
.r-v2-rs-saves__ask,
.r-v2-rs-saves__summary,
.r-v2-rs-saves__choice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.r-v2-rs-saves__actions {
  justify-content: flex-end;
}
.r-v2-rs-saves__ask p {
  margin: 0;
  flex: 1 1 280px;
}
.r-v2-rs-saves__progress {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 8px;
}
.r-v2-rs-saves__progress > :last-child {
  grid-column: 1 / -1;
}
.r-v2-rs-saves__table-wrap {
  overflow-x: auto;
}
.r-v2-rs-saves__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}
.r-v2-rs-saves__table th {
  text-align: left;
  font-weight: 600;
  padding: 6px 8px;
  border-bottom: 1px solid var(--r-color-border);
}
.r-v2-rs-saves__table td {
  padding: 8px;
  vertical-align: top;
  border-bottom: 1px solid var(--r-color-border);
  min-width: 160px;
}
.r-v2-rs-saves__file {
  display: flex;
  align-items: center;
  gap: 6px;
  word-break: break-all;
}
.r-v2-rs-saves__muted {
  color: var(--r-color-fg-secondary);
  font-size: 0.75rem;
  word-break: break-all;
}
</style>
