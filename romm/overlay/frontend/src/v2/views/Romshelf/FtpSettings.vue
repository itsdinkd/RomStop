<script setup lang="ts">
// Settings > Send with FTP (admins only): the FTP server installed hacks are sent to with "Send with FTP",
// say a jailbroken PS5's FTP payload next to RetroArch. Presets only fill in the form.
// The password is saved on the RomM server (encrypted) and never comes back to the browser.
import {
  RAlert,
  RBtn,
  RChip,
  RIcon,
  RSelect,
  RSpinner,
  RTextField,
} from "@v2/lib";
import { storeToRefs } from "pinia";
import { computed, onMounted, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import { onBeforeRouteLeave } from "vue-router";
import romshelfApi, {
  installErrorOf,
  type FtpPreset,
  type FtpSettings,
  type FtpSettingsInput,
  type FtpTestResult,
} from "@/services/api/romshelf";
import storePlatforms from "@/stores/platforms";
import SettingsSaveBar from "@/v2/components/Settings/SettingsSaveBar.vue";
import SettingsSection from "@/v2/components/Settings/SettingsSection.vue";
import SettingsToggleRow from "@/v2/components/Settings/SettingsToggleRow.vue";
import { useFtpStatus } from "@/v2/components/Romshelf/ftp";
import { useCan } from "@/v2/composables/useCan";
import { useConfirm } from "@/v2/composables/useConfirm";
import { useSnackbar } from "@/v2/composables/useSnackbar";
import { useUnloadGuard } from "@/v2/composables/useUnloadGuard";

defineOptions({ inheritAttrs: false });

const { t } = useI18n();
const snackbar = useSnackbar();
const confirm = useConfirm();
const isAdmin = useCan("app.admin");
const { allPlatforms } = storeToRefs(storePlatforms());

type Form = Omit<FtpSettings, "password_set">;

const loading = ref(true);
const loadError = ref<string | null>(null);
const saving = ref(false);
const testing = ref(false);
const presets = ref<FtpPreset[]>([]);
const saved = ref<FtpSettings | null>(null);
const form = reactive<Form>({
  enabled: false,
  name: "",
  preset: null,
  host: "",
  port: 21,
  username: "",
  tls: false,
  passive: true,
  base_path: "/",
  folders: {},
  hacks: "match",
  hacks_folder: "Hacks",
  existing: "replace",
  timeout: 30,
});
const password = ref("");
const clearPassword = ref(false);
const testResult = ref<FtpTestResult | null>(null);
const testError = ref<string | null>(null);
const platformFilter = ref("");

function fill(s: FtpSettings) {
  const { password_set: _set, ...rest } = s;
  Object.assign(form, { ...rest, folders: { ...rest.folders } });
  password.value = "";
  clearPassword.value = false;
}

const snapshot = ref("");
const current = () =>
  JSON.stringify({
    ...form,
    password: password.value,
    clear: clearPassword.value,
  });
const dirty = computed(() => !!saved.value && current() !== snapshot.value);

async function load() {
  loading.value = true;
  loadError.value = null;
  try {
    const { data } = await romshelfApi.getFtpSettings();
    presets.value = data.presets;
    saved.value = data.settings;
    fill(data.settings);
    snapshot.value = current();
  } catch (err) {
    loadError.value = installErrorOf(err).message;
  } finally {
    loading.value = false;
  }
}
onMounted(() => {
  if (isAdmin.value) void load();
  else loading.value = false;
});

const presetItems = computed(() => [
  { value: "", title: t("romshelf.ftp-preset-custom") },
  ...presets.value.map((p) => ({ value: p.id, title: p.label })),
]);
const presetValue = computed({
  get: () => form.preset ?? "",
  set: (id: string) => applyPreset(id),
});
const activePreset = computed(
  () => presets.value.find((p) => p.id === form.preset) ?? null,
);

function applyPreset(id: string) {
  const preset = presets.value.find((p) => p.id === id);
  form.preset = preset ? preset.id : null;
  if (!preset) return;
  // The preset fills the form; the host and anything else stay editable.
  const { folders, ...settings } = preset.settings;
  Object.assign(form, settings);
  if (folders) form.folders = { ...folders };
}

const hacksItems = computed(() => [
  { value: "match", title: t("romshelf.ftp-hacks-match") },
  { value: "always", title: t("romshelf.ftp-hacks-always") },
  { value: "never", title: t("romshelf.ftp-hacks-never") },
]);
const existingItems = computed(() => [
  { value: "replace", title: t("romshelf.ftp-existing-replace") },
  { value: "skip", title: t("romshelf.ftp-existing-skip") },
]);

function payload(): FtpSettingsInput {
  const out: FtpSettingsInput = { ...form, folders: { ...form.folders } };
  if (password.value) out.password = password.value;
  if (clearPassword.value) out.clear_password = true;
  return out;
}

async function onSave() {
  saving.value = true;
  try {
    const { data } = await romshelfApi.saveFtpSettings(payload());
    saved.value = data.settings;
    fill(data.settings);
    snapshot.value = current();
    useFtpStatus(true);
    snackbar.success(t("romshelf.ftp-saved"));
  } catch (err) {
    snackbar.error(installErrorOf(err).message, { timeout: 8000 });
  } finally {
    saving.value = false;
  }
}

function onDiscard() {
  if (saved.value) fill(saved.value);
  snapshot.value = current();
}

async function onTest() {
  testing.value = true;
  testResult.value = null;
  testError.value = null;
  try {
    const { data } = await romshelfApi.testFtp(payload());
    testResult.value = data;
  } catch (err) {
    testError.value = installErrorOf(err).message;
  } finally {
    testing.value = false;
  }
}

// Platforms in the library, each with the device folder its games go to.
const platformRows = computed(() => {
  const q = platformFilter.value.trim().toLowerCase();
  return [...allPlatforms.value]
    .filter((p) => p.fs_slug)
    .filter(
      (p) =>
        !q ||
        p.display_name.toLowerCase().includes(q) ||
        p.fs_slug.toLowerCase().includes(q),
    )
    .sort((a, b) => a.display_name.localeCompare(b.display_name));
});
const foundFolders = computed(() => new Set(testResult.value?.folders ?? []));
// A wrong base path is easy to miss: the first send just creates new folders next to the real ones.
const noPlatformFolders = computed(
  () =>
    !!testResult.value &&
    allPlatforms.value.length > 0 &&
    !allPlatforms.value.some(
      (p) => p.fs_slug && foundFolders.value.has(deviceFolder(p.fs_slug)),
    ),
);

function folderFor(slug: string) {
  return form.folders[slug] ?? "";
}
function setFolder(slug: string, value: unknown) {
  const text = typeof value === "string" ? value.trim() : "";
  const next = { ...form.folders };
  if (text) next[slug] = text;
  else delete next[slug];
  form.folders = next;
}
function deviceFolder(slug: string) {
  return (form.folders[slug] || slug).split("/")[0] ?? "";
}
function examplePath(slug: string, hack: boolean) {
  const base = form.base_path.replace(/\/+$/, "");
  const folder = form.folders[slug] || slug;
  const inHacks = form.hacks === "always" || (form.hacks === "match" && hack);
  return `${base}/${folder}${inHacks ? `/${form.hacks_folder || "Hacks"}` : ""}/`;
}

onBeforeRouteLeave(async () => {
  if (!dirty.value) return true;
  return confirm({
    title: t("romshelf.ftp-leave-title"),
    body: t("romshelf.ftp-leave-body"),
    confirmText: t("romshelf.ftp-leave-confirm"),
    tone: "warning",
  });
});
useUnloadGuard(() => dirty.value);
</script>

<template>
  <div v-if="loading" class="r-v2-rs-ftp-settings__loading">
    <RSpinner />
  </div>
  <div v-else-if="!isAdmin" class="r-v2-section-stack">
    <RAlert type="info" :text="t('romshelf.ftp-admin-only')" />
  </div>
  <div v-else-if="loadError" class="r-v2-section-stack">
    <RAlert type="error" :text="loadError">
      <template #actions>
        <RBtn variant="text" @click="load">{{ t("common.try-again") }}</RBtn>
      </template>
    </RAlert>
  </div>
  <div v-else class="r-v2-section-stack r-v2-rs-ftp-settings">
    <SettingsSection
      :title="t('romshelf.ftp-title')"
      icon="mdi-upload-network-outline"
    >
      <p class="r-v2-rs-ftp-settings__desc">{{ t("romshelf.ftp-desc") }}</p>
      <SettingsToggleRow
        v-model="form.enabled"
        :title="t('romshelf.ftp-enabled')"
        :description="t('romshelf.ftp-enabled-desc')"
      />
      <div class="r-v2-rs-ftp-settings__grid">
        <RSelect
          v-model="presetValue"
          :items="presetItems"
          item-title="title"
          item-value="value"
          :label="t('romshelf.ftp-preset')"
          prefix-label="stacked"
          hide-details
        />
        <RTextField
          v-model="form.name"
          :label="t('romshelf.ftp-name')"
          :placeholder="activePreset?.label || form.host || 'FTP'"
          prefix-label="stacked"
          hide-details
        />
      </div>
      <p v-if="activePreset" class="r-v2-rs-ftp-settings__note">
        <RIcon icon="mdi-information-outline" size="13" />
        {{ activePreset.description }}
      </p>
    </SettingsSection>

    <SettingsSection
      :title="t('romshelf.ftp-server')"
      icon="mdi-server-network"
    >
      <div class="r-v2-rs-ftp-settings__grid">
        <RTextField
          v-model="form.host"
          :label="t('romshelf.ftp-host')"
          placeholder="192.168.1.50"
          autocomplete="off"
          prefix-label="stacked"
          hide-details
        />
        <RTextField
          :model-value="form.port"
          type="number"
          min="1"
          max="65535"
          :label="t('romshelf.ftp-port')"
          prefix-label="stacked"
          hide-details
          @update:model-value="form.port = Number($event) || 21"
        />
        <RTextField
          v-model="form.username"
          :label="t('romshelf.ftp-username')"
          :placeholder="t('romshelf.ftp-anonymous')"
          autocomplete="off"
          prefix-label="stacked"
          hide-details
        />
        <RTextField
          v-model="password"
          type="password"
          :label="t('romshelf.ftp-password')"
          :placeholder="
            saved?.password_set && !clearPassword
              ? t('romshelf.ftp-password-saved')
              : t('romshelf.ftp-password-none')
          "
          autocomplete="new-password"
          prefix-label="stacked"
          hide-details
        />
      </div>
      <div v-if="saved?.password_set" class="r-v2-rs-ftp-settings__row">
        <RBtn
          size="small"
          variant="text"
          prepend-icon="mdi-key-remove"
          :disabled="clearPassword"
          @click="
            clearPassword = true;
            password = '';
          "
        >
          {{
            clearPassword
              ? t("romshelf.ftp-password-will-clear")
              : t("romshelf.ftp-password-clear")
          }}
        </RBtn>
      </div>
      <SettingsToggleRow
        v-model="form.passive"
        :title="t('romshelf.ftp-passive')"
        :description="t('romshelf.ftp-passive-desc')"
      />
      <SettingsToggleRow
        v-model="form.tls"
        :title="t('romshelf.ftp-tls')"
        :description="t('romshelf.ftp-tls-desc')"
      />
      <div class="r-v2-rs-ftp-settings__row">
        <RBtn
          variant="outlined"
          prepend-icon="mdi-lan-connect"
          :loading="testing"
          :disabled="!form.host"
          @click="onTest"
        >
          {{ t("romshelf.ftp-test") }}
        </RBtn>
        <span v-if="testResult" class="r-v2-rs-ftp-settings__ok">
          <RIcon icon="mdi-check-circle-outline" size="16" />
          {{
            t("romshelf.ftp-test-ok", {
              path: form.base_path,
              n: testResult.folders.length,
            })
          }}
        </span>
      </div>
      <RAlert
        v-if="testError"
        type="error"
        density="compact"
        :text="testError"
      />
      <RAlert
        v-if="noPlatformFolders"
        type="warning"
        density="compact"
        :text="t('romshelf.ftp-test-no-platforms', { path: form.base_path })"
      />
      <div
        v-if="testResult?.folders.length"
        class="r-v2-rs-ftp-settings__chips"
      >
        <RChip v-for="f in testResult.folders" :key="f" size="small">{{
          f
        }}</RChip>
      </div>
    </SettingsSection>

    <SettingsSection
      :title="t('romshelf.ftp-folders')"
      icon="mdi-folder-arrow-up-outline"
    >
      <div class="r-v2-rs-ftp-settings__grid">
        <RTextField
          v-model="form.base_path"
          :label="t('romshelf.ftp-base-path')"
          placeholder="/content"
          prefix-label="stacked"
          hide-details
        />
        <RSelect
          v-model="form.existing"
          :items="existingItems"
          item-title="title"
          item-value="value"
          :label="t('romshelf.ftp-existing')"
          prefix-label="stacked"
          hide-details
        />
        <RSelect
          v-model="form.hacks"
          :items="hacksItems"
          item-title="title"
          item-value="value"
          :label="t('romshelf.ftp-hacks')"
          prefix-label="stacked"
          hide-details
        />
        <RTextField
          v-model="form.hacks_folder"
          :label="t('romshelf.ftp-hacks-folder')"
          placeholder="Hacks"
          :disabled="form.hacks === 'never'"
          prefix-label="stacked"
          hide-details
        />
      </div>
      <p class="r-v2-rs-ftp-settings__note">
        <RIcon icon="mdi-information-outline" size="13" />
        {{ t("romshelf.ftp-folders-desc") }}
      </p>
      <RTextField
        v-model="platformFilter"
        :placeholder="t('romshelf.ftp-filter-platforms')"
        prepend-inner-icon="mdi-magnify"
        density="compact"
        clearable
        hide-details
        class="r-v2-rs-ftp-settings__filter"
      />
      <div class="r-v2-rs-ftp-settings__platforms">
        <div
          v-for="p in platformRows"
          :key="p.fs_slug"
          class="r-v2-rs-ftp-settings__platform"
        >
          <div class="r-v2-rs-ftp-settings__platform-name">
            <span>{{ p.display_name }}</span>
            <code>{{ examplePath(p.fs_slug, true) }}</code>
          </div>
          <RTextField
            :model-value="folderFor(p.fs_slug)"
            :placeholder="p.fs_slug"
            :aria-label="
              t('romshelf.ftp-folder-for', { platform: p.display_name })
            "
            density="compact"
            hide-details
            @update:model-value="setFolder(p.fs_slug, $event)"
          />
          <RIcon
            v-if="testResult"
            :icon="
              foundFolders.has(deviceFolder(p.fs_slug))
                ? 'mdi-check'
                : 'mdi-folder-plus-outline'
            "
            :title="
              foundFolders.has(deviceFolder(p.fs_slug))
                ? t('romshelf.ftp-folder-found')
                : t('romshelf.ftp-folder-new')
            "
            size="16"
          />
        </div>
      </div>
    </SettingsSection>

    <SettingsSaveBar
      :visible="dirty"
      :label="t('romshelf.ftp-unsaved')"
      :saving="saving"
      @save="onSave"
      @discard="onDiscard"
    />
  </div>
</template>

<style scoped>
.r-v2-rs-ftp-settings__loading {
  display: grid;
  place-items: center;
  min-height: 240px;
}
.r-v2-rs-ftp-settings {
  padding-bottom: 96px;
}
.r-v2-rs-ftp-settings__desc {
  margin: 0;
  color: var(--r-color-fg-secondary);
  font-size: var(--r-font-size-sm);
}
.r-v2-rs-ftp-settings__note {
  margin: 0;
  display: flex;
  gap: var(--r-space-1);
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-xs);
}
.r-v2-rs-ftp-settings__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--r-space-3);
}
.r-v2-rs-ftp-settings__row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--r-space-3);
}
.r-v2-rs-ftp-settings__ok {
  display: inline-flex;
  align-items: center;
  gap: var(--r-space-1);
  color: var(--r-color-success);
  font-size: var(--r-font-size-sm);
}
.r-v2-rs-ftp-settings__chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--r-space-1);
}
.r-v2-rs-ftp-settings__filter {
  max-width: 320px;
}
.r-v2-rs-ftp-settings__platforms {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-2);
}
.r-v2-rs-ftp-settings__platform {
  display: grid;
  grid-template-columns: minmax(0, 280px) minmax(120px, 240px) 20px;
  justify-content: start;
  align-items: center;
  gap: var(--r-space-3);
}
.r-v2-rs-ftp-settings__platform-name {
  display: flex;
  flex-direction: column;
  min-width: 0;
  font-size: var(--r-font-size-sm);
}
.r-v2-rs-ftp-settings__platform-name code {
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
html[data-bp~="xs"] .r-v2-rs-ftp-settings__platform {
  grid-template-columns: minmax(0, 1fr) 20px;
}
html[data-bp~="xs"] .r-v2-rs-ftp-settings__platform-name {
  grid-column: 1 / -1;
}
</style>
