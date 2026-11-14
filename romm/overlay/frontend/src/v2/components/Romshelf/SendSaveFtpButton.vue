<script setup lang="ts">
// SendSaveFtpButton: "Send with FTP" for one save or save state, on a game's Save data tab.
// The dialog offers the saves (or states) folder from Settings > Send with FTP, shown with the
// console and emulator it was worked out from, or a folder typed in by hand. Typed folders are
// remembered per user and offered again in a dropdown. The file is named the way RetroArch looks
// for it, and a file already on the device is only replaced when the user says so; the server
// keeps the old one as "<name>.bak-<time>". Hidden while Send with FTP is off.
import {
  RAlert,
  RBtn,
  RDialog,
  RIcon,
  RSelect,
  RSpinner,
  RTextField,
} from "@v2/lib";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import romshelfApi, {
  installErrorOf,
  type FtpJob,
  type FtpSavePlan,
} from "@/services/api/romshelf";
import {
  formatBytes,
  pickTarget,
  useFtpStatus,
} from "@/v2/components/Romshelf/ftp";
import { useSnackbar } from "@/v2/composables/useSnackbar";
import type { Asset, AssetType } from "@/v2/utils/assets";

const props = defineProps<{ asset: Asset; type: AssetType }>();

const { t } = useI18n();
const snackbar = useSnackbar();
const ftp = useFtpStatus();

const open = ref(false);
// With several devices, the dialog starts on the default one and can switch.
const targetId = ref<string | null>(null);
const targetItems = computed(() =>
  ftp.value.targets.map((x) => ({ value: x.id, title: x.name })),
);
function onTarget(value: unknown) {
  targetId.value = typeof value === "string" ? value : null;
  void openDialog();
}
const plan = ref<FtpSavePlan | null>(null);
const loading = ref(false);
const checking = ref(false);
const loadError = ref<string | null>(null);
const fieldError = ref<string | null>(null);
const mode = ref<"settings" | "custom">("settings");
const customPath = ref("");
const fileName = ref("");
const recent = ref<string[]>([]);
const menuOpen = ref(false);
const highlighted = ref(-1);
const sending = ref(false);
const job = ref<FtpJob | null>(null);
let checkTimer: ReturnType<typeof setTimeout> | null = null;
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let checkSeq = 0;

const isSave = computed(() => props.type === "save");
const title = computed(() =>
  isSave.value
    ? t("romshelf.ftp-save-title-save")
    : t("romshelf.ftp-save-title-state"),
);
const busy = computed(() => sending.value || job.value?.status === "running");
const progress = computed(() => {
  const j = job.value;
  if (!j || j.status !== "running" || !j.total) return "";
  return t("romshelf.ftp-progress", {
    done: formatBytes(j.done),
    total: formatBytes(j.total),
    percent: Math.floor((100 * j.done) / j.total),
  });
});

// What the device would show for this pick: the settings folder's plan, or the typed folder's.
const settingsPath = ref<string | null>(null);
const settingsExists = ref<boolean | null>(null);
const exists = computed(() =>
  mode.value === "settings" ? settingsExists.value : plan.value?.exists,
);
const detected = computed(() => {
  const p = plan.value;
  if (!p) return "";
  const emulator =
    p.core_dir || p.emulator || t("romshelf.ftp-save-no-emulator");
  return t("romshelf.ftp-save-detected", { console: p.console, emulator });
});
const suggestions = computed(() => {
  const q = customPath.value.trim().toLowerCase();
  return recent.value.filter((p) => !q || p.toLowerCase().includes(q));
});
const canSend = computed(() => {
  if (!plan.value || busy.value || checking.value || !fileName.value.trim())
    return false;
  if (mode.value === "settings") return !!plan.value.settings_dir;
  return !!customPath.value.trim() && !fieldError.value;
});

function errorText(err: unknown) {
  return installErrorOf(err).message;
}

async function openDialog() {
  if (!open.value) targetId.value = pickTarget(ftp.value, null)?.id ?? null;
  open.value = true;
  loading.value = true;
  loadError.value = null;
  fieldError.value = null;
  plan.value = null;
  try {
    const { data } = await romshelfApi.planSaveWithFtp({
      type: props.type,
      id: props.asset.id,
      target: targetId.value,
    });
    plan.value = data;
    recent.value = data.recent_paths;
    fileName.value = data.file_name;
    settingsPath.value = data.path;
    settingsExists.value = data.exists;
    mode.value = data.settings_dir ? "settings" : "custom";
    if (!data.settings_dir && !customPath.value)
      customPath.value = data.recent_paths[0] ?? "";
    if (mode.value === "custom" && customPath.value) scheduleCheck(0);
  } catch (err) {
    loadError.value = errorText(err);
  } finally {
    loading.value = false;
  }
}

// Ask the server again whether the file is there, for the folder and name now in the form.
function scheduleCheck(delay = 500) {
  if (checkTimer) clearTimeout(checkTimer);
  checkTimer = setTimeout(() => void check(), delay);
}

async function check() {
  if (!plan.value) return;
  const folder = mode.value === "custom" ? customPath.value.trim() : null;
  if (mode.value === "custom" && !folder) {
    plan.value = { ...plan.value, path: null, exists: null, check_error: null };
    return;
  }
  const seq = ++checkSeq;
  checking.value = true;
  try {
    const { data } = await romshelfApi.planSaveWithFtp({
      type: props.type,
      id: props.asset.id,
      target: targetId.value,
      folder,
      file_name: fileName.value.trim() || null,
    });
    if (seq !== checkSeq) return;
    fieldError.value = null;
    plan.value = data;
    if (mode.value === "settings") {
      settingsPath.value = data.path;
      settingsExists.value = data.exists;
    }
  } catch (err) {
    if (seq === checkSeq) fieldError.value = errorText(err);
  } finally {
    if (seq === checkSeq) checking.value = false;
  }
}

function pickMode(next: "settings" | "custom") {
  if (next === "settings" && !plan.value?.settings_dir) return;
  mode.value = next;
  fieldError.value = null;
  scheduleCheck(0);
}

function onPathInput(value: unknown) {
  customPath.value = typeof value === "string" ? value : "";
  mode.value = "custom";
  menuOpen.value = true;
  highlighted.value = -1;
  scheduleCheck();
}

function onFileNameInput(value: unknown) {
  fileName.value = typeof value === "string" ? value : "";
  scheduleCheck();
}

function choosePath(path: string) {
  customPath.value = path;
  menuOpen.value = false;
  mode.value = "custom";
  scheduleCheck(0);
}

function onPathKeydown(e: KeyboardEvent) {
  const items = suggestions.value;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    menuOpen.value = true;
    highlighted.value = Math.min(highlighted.value + 1, items.length - 1);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    highlighted.value = Math.max(highlighted.value - 1, -1);
  } else if (e.key === "Enter" && menuOpen.value && highlighted.value >= 0) {
    e.preventDefault();
    const path = items[highlighted.value];
    if (path) choosePath(path);
  } else if (e.key === "Escape" && menuOpen.value) {
    e.stopPropagation();
    menuOpen.value = false;
  }
}

function onPathBlur() {
  // Let a click on a suggestion land before the list goes away.
  setTimeout(() => (menuOpen.value = false), 150);
}

async function forget(path: string) {
  try {
    const { data } = await romshelfApi.forgetFtpSavePath(path);
    recent.value = data.paths;
  } catch (err) {
    snackbar.error(errorText(err));
  }
}

async function send() {
  if (!canSend.value || !plan.value) return;
  sending.value = true;
  const replace = exists.value === true;
  const name = fileName.value.trim();
  try {
    const { data } = await romshelfApi.sendSaveWithFtp({
      type: props.type,
      id: props.asset.id,
      target: targetId.value,
      folder: mode.value === "custom" ? customPath.value.trim() : null,
      file_name: name !== plan.value.file_name ? name : null,
      replace,
    });
    if (mode.value === "custom") {
      const folder = data.dest;
      recent.value = [folder, ...recent.value.filter((p) => p !== folder)];
    }
    open.value = false;
    job.value = data;
    follow(data, name);
  } catch (err) {
    const e = installErrorOf(err);
    if (e.code === "exists") {
      // Someone put it there since the check: show the warning and let them confirm.
      if (mode.value === "settings") settingsExists.value = true;
      else plan.value = { ...plan.value, exists: true };
    } else {
      snackbar.error(e.message, { timeout: 8000 });
    }
  } finally {
    sending.value = false;
  }
}

function follow(j: FtpJob, name: string) {
  if (j.status === "running") {
    pollTimer = setTimeout(() => void poll(j.id, name), 1000);
    return;
  }
  if (j.status === "done") {
    snackbar.success(
      t("romshelf.ftp-save-sent", { name, target: j.target, dest: j.dest }),
      { icon: "mdi-check-bold", timeout: 6000 },
    );
  } else {
    snackbar.error(j.message || t("romshelf.ftp-failed"), { timeout: 10000 });
  }
}

async function poll(id: string, name: string) {
  try {
    const { data } = await romshelfApi.getFtpJob(id);
    job.value = data;
    follow(data, name);
  } catch {
    pollTimer = setTimeout(() => void poll(id, name), 3000);
  }
}

watch(open, (isOpen) => {
  if (!isOpen) menuOpen.value = false;
});

onBeforeUnmount(() => {
  if (checkTimer) clearTimeout(checkTimer);
  if (pollTimer) clearTimeout(pollTimer);
});
</script>

<template>
  <template v-if="ftp.enabled">
    <RBtn
      icon="mdi-upload-network-outline"
      variant="text"
      size="small"
      :loading="busy"
      :tooltip="
        busy
          ? progress || t('romshelf.ftp-sending', { target: ftp.name || 'FTP' })
          : ftp.name
            ? t('romshelf.ftp-send-to', { target: ftp.name })
            : t('romshelf.ftp-send')
      "
      :aria-label="t('romshelf.ftp-save-send-named', { name: asset.file_name })"
      @click="openDialog"
    />
    <RDialog
      v-model="open"
      icon="mdi-upload-network-outline"
      :width="560"
      cancelable
    >
      <template #header>
        <span>{{ title }}</span>
      </template>
      <template #content>
        <RSelect
          v-if="ftp.targets.length > 1"
          :model-value="targetId ?? ''"
          :items="targetItems"
          item-title="title"
          item-value="value"
          :label="t('romshelf.ftp-save-device')"
          prefix-label="stacked"
          density="compact"
          hide-details
          class="r-v2-rs-save-ftp__device"
          @update:model-value="onTarget"
        />
        <div v-if="loading" class="r-v2-rs-save-ftp__loading">
          <RSpinner :size="20" />
        </div>
        <RAlert
          v-else-if="loadError"
          type="error"
          density="compact"
          :text="loadError"
        />
        <div v-else-if="plan" class="r-v2-rs-save-ftp">
          <p class="r-v2-rs-save-ftp__file">
            <RIcon
              :icon="isSave ? 'mdi-content-save-outline' : 'mdi-history'"
              size="16"
            />
            <span>{{ asset.file_name }}</span>
            <span class="r-v2-rs-save-ftp__muted">→ {{ plan.target }}</span>
          </p>

          <div
            class="r-v2-rs-save-ftp__choices"
            role="radiogroup"
            :aria-label="t('romshelf.ftp-save-where')"
          >
            <button
              type="button"
              role="radio"
              class="r-v2-rs-save-ftp__choice"
              :class="{ 'r-v2-rs-save-ftp__choice--on': mode === 'settings' }"
              :aria-checked="mode === 'settings'"
              :disabled="!plan.settings_dir"
              @click="pickMode('settings')"
            >
              <RIcon
                :icon="
                  mode === 'settings'
                    ? 'mdi-radiobox-marked'
                    : 'mdi-radiobox-blank'
                "
                size="18"
              />
              <span class="r-v2-rs-save-ftp__choice-text">
                <strong>{{ t("romshelf.ftp-save-use-settings") }}</strong>
                <em v-if="settingsPath" class="r-v2-rs-save-ftp__path">{{
                  settingsPath
                }}</em>
                <span v-else class="r-v2-rs-save-ftp__muted">{{
                  isSave
                    ? t("romshelf.ftp-save-no-folder-save")
                    : t("romshelf.ftp-save-no-folder-state")
                }}</span>
                <span class="r-v2-rs-save-ftp__muted">{{ detected }}</span>
              </span>
            </button>

            <div
              class="r-v2-rs-save-ftp__choice"
              :class="{ 'r-v2-rs-save-ftp__choice--on': mode === 'custom' }"
            >
              <button
                type="button"
                role="radio"
                class="r-v2-rs-save-ftp__choice-head"
                :aria-checked="mode === 'custom'"
                @click="pickMode('custom')"
              >
                <RIcon
                  :icon="
                    mode === 'custom'
                      ? 'mdi-radiobox-marked'
                      : 'mdi-radiobox-blank'
                  "
                  size="18"
                />
                <strong>{{ t("romshelf.ftp-save-use-custom") }}</strong>
              </button>
              <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- keys from the field inside bubble here -->
              <div class="r-v2-rs-save-ftp__combo" @keydown="onPathKeydown">
                <RTextField
                  :model-value="customPath"
                  :label="t('romshelf.ftp-save-folder')"
                  placeholder="/data/homebrew/PPSA99169/saves"
                  prefix-label="stacked"
                  density="compact"
                  :error-messages="
                    mode === 'custom' && fieldError ? fieldError : undefined
                  "
                  :hide-details="!(mode === 'custom' && fieldError)"
                  autocomplete="off"
                  @update:model-value="onPathInput"
                  @focus="
                    menuOpen = true;
                    mode = 'custom';
                  "
                  @blur="onPathBlur"
                />
                <ul
                  v-if="menuOpen"
                  class="r-v2-rs-save-ftp__menu"
                  role="listbox"
                  :aria-label="t('romshelf.ftp-save-recent')"
                >
                  <li class="r-v2-rs-save-ftp__menu-hint" role="presentation">
                    <RIcon icon="mdi-keyboard-outline" size="14" />
                    {{ t("romshelf.ftp-save-type-to-add") }}
                  </li>
                  <li
                    v-for="(p, i) in suggestions"
                    :key="p"
                    role="option"
                    tabindex="-1"
                    class="r-v2-rs-save-ftp__menu-item"
                    :class="{
                      'r-v2-rs-save-ftp__menu-item--on': i === highlighted,
                    }"
                    :aria-selected="i === highlighted"
                    @mousedown.prevent="choosePath(p)"
                  >
                    <RIcon icon="mdi-history" size="14" />
                    <span class="r-v2-rs-save-ftp__menu-path">{{ p }}</span>
                    <RBtn
                      icon="mdi-close"
                      variant="text"
                      size="x-small"
                      :aria-label="t('romshelf.ftp-save-forget', { path: p })"
                      :tooltip="t('romshelf.ftp-save-forget', { path: p })"
                      @mousedown.stop.prevent="forget(p)"
                    />
                  </li>
                </ul>
              </div>
            </div>
          </div>

          <RTextField
            :model-value="fileName"
            :label="t('romshelf.ftp-save-file-name')"
            :hint="t('romshelf.ftp-save-file-name-hint')"
            prefix-label="stacked"
            density="compact"
            @update:model-value="onFileNameInput"
          />

          <p v-if="checking" class="r-v2-rs-save-ftp__muted">
            <RSpinner :size="12" /> {{ t("romshelf.ftp-save-checking") }}
          </p>
          <RAlert
            v-else-if="exists"
            type="warning"
            density="compact"
            :text="t('romshelf.ftp-save-exists', { name: fileName.trim() })"
          />
          <RAlert
            v-else-if="plan.check_error"
            type="info"
            density="compact"
            :text="
              t('romshelf.ftp-save-check-failed', { error: plan.check_error })
            "
          />
        </div>
      </template>
      <template #footer>
        <RBtn
          :color="exists ? 'warning' : 'primary'"
          prepend-icon="mdi-upload-network-outline"
          :loading="sending"
          :disabled="!canSend"
          @click="send"
        >
          {{
            exists
              ? t("romshelf.ftp-save-replace")
              : t("romshelf.ftp-save-send")
          }}
        </RBtn>
      </template>
    </RDialog>
  </template>
</template>

<style scoped>
.r-v2-rs-save-ftp {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-3);
}
.r-v2-rs-save-ftp__device {
  margin-bottom: var(--r-space-3);
}
.r-v2-rs-save-ftp__loading {
  display: grid;
  place-items: center;
  min-height: 120px;
}
.r-v2-rs-save-ftp__file {
  margin: 0;
  display: flex;
  align-items: center;
  gap: var(--r-space-2);
  font-size: var(--r-font-size-sm);
  min-width: 0;
  overflow-wrap: anywhere;
}
.r-v2-rs-save-ftp__muted {
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-xs);
}
.r-v2-rs-save-ftp__choices {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-2);
}
.r-v2-rs-save-ftp__choice {
  display: flex;
  gap: var(--r-space-2);
  align-items: flex-start;
  width: 100%;
  padding: var(--r-space-3);
  border: 1px solid var(--r-color-border);
  border-radius: var(--r-radius-lg);
  background: transparent;
  color: inherit;
  text-align: left;
  font: inherit;
}
button.r-v2-rs-save-ftp__choice {
  cursor: pointer;
}
div.r-v2-rs-save-ftp__choice {
  flex-direction: column;
}
.r-v2-rs-save-ftp__choice:disabled {
  opacity: 0.6;
  cursor: default;
}
.r-v2-rs-save-ftp__choice--on {
  border-color: var(--r-color-brand-primary);
}
.r-v2-rs-save-ftp__choice-head {
  display: flex;
  align-items: center;
  gap: var(--r-space-2);
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.r-v2-rs-save-ftp__choice-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.r-v2-rs-save-ftp__path {
  font-size: var(--r-font-size-sm);
  color: var(--r-color-fg-secondary);
  overflow-wrap: anywhere;
}
.r-v2-rs-save-ftp__combo {
  position: relative;
  width: 100%;
}
.r-v2-rs-save-ftp__menu {
  position: absolute;
  z-index: 2;
  left: 0;
  right: 0;
  top: 100%;
  margin: 4px 0 0;
  padding: var(--r-space-1) 0;
  list-style: none;
  max-height: 220px;
  overflow-y: auto;
  border: 1px solid var(--r-color-border);
  border-radius: var(--r-radius-lg);
  background: var(--r-color-bg-elevated);
  box-shadow: var(--r-elev-2);
}
.r-v2-rs-save-ftp__menu-hint {
  display: flex;
  align-items: center;
  gap: var(--r-space-2);
  padding: var(--r-space-1) var(--r-space-3);
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-xs);
  font-style: italic;
}
.r-v2-rs-save-ftp__menu-item {
  display: flex;
  align-items: center;
  gap: var(--r-space-2);
  padding: 2px var(--r-space-2) 2px var(--r-space-3);
  font-size: var(--r-font-size-sm);
  cursor: pointer;
}
.r-v2-rs-save-ftp__menu-item:hover,
.r-v2-rs-save-ftp__menu-item--on {
  background: var(--r-color-surface-hover);
}
.r-v2-rs-save-ftp__menu-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
