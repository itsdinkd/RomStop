<script setup lang="ts">
// InstallPanel: installs a hack onto one library ROM as a new ROM entry.
// The backend fetches the patch itself (RomStop's mirror first, then the source
// site's file URL), so Install is one click. Only when every source refuses a
// server does the user download the file and drop it here.
import {
  RAlert,
  RBtn,
  RCheckbox,
  RDropzone,
  RIcon,
  RSelect,
  RSpinner,
} from "@v2/lib";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { ROUTES } from "@/plugins/routeNames";
import romshelfApi, {
  installErrorOf,
  type RomshelfHack,
  type RomshelfInstall,
  type RomshelfInstallError,
  type RomshelfLibraryFile,
  type RomshelfMatchLevel,
} from "@/services/api/romshelf";
import { useCan } from "@/v2/composables/useCan";
import { useSnackbar } from "@/v2/composables/useSnackbar";
import { browserCoreFor, rememberHackCore } from "@/v2/components/Romshelf/compat";

const props = defineProps<{
  hack: RomshelfHack;
  romId: number;
  files: RomshelfLibraryFile[];
  defaultFileId: number | null;
  level: RomshelfMatchLevel;
  versionLevels: Record<string, RomshelfMatchLevel>;
  installs: RomshelfInstall[];
  needed: string;
  /** The library ROM's platform, to pick a browser core that runs the hack. */
  platformSlug?: string;
}>();

const emit = defineEmits<{ (e: "installed", record: RomshelfInstall): void }>();

const { t } = useI18n();
const snackbar = useSnackbar();
const canInstall = useCan("rom.upload");

const PATCH_ACCEPT =
  ".ips,.ups,.bps,.ppf,.rup,.aps,.bdf,.pmsr,.vcdiff,.xdelta,.zip,.7z,.rar";
const POLL_MS = 2000;
const POLL_LIMIT = 45;

// Older versions RomStop has no patch for still install from a patch the user drops in.
function hasPatch(version: string, latest: boolean) {
  const have = props.hack.patchVersions;
  return have ? have.includes(version) : latest;
}
const versionItems = computed<
  { value: string; title: string; subtitle?: string | undefined }[]
>(() =>
  props.hack.versions.length
    ? props.hack.versions.map((v, i) => ({
        value: v.version,
        title:
          i === 0
            ? t("romshelf.version-latest", { version: v.version })
            : hasPatch(v.version, false)
              ? v.version
              : t("romshelf.version-no-download", { version: v.version }),
        subtitle: v.date,
      }))
    : props.hack.version
      ? [{ value: props.hack.version, title: props.hack.version }]
      : [],
);
const currentVersion = computed(() => versionItems.value[0]?.value ?? null);
const version = ref<string | null>(currentVersion.value);
const fileId = ref<number | null>(
  props.defaultFileId ?? props.files[0]?.id ?? null,
);
const installAnyway = ref(false);
const patchFile = ref<File | null>(null);
const patchMember = ref<string | null>(null);
const installing = ref(false);
const pendingRecord = ref<RomshelfInstall | null>(null);
const error = ref<RomshelfInstallError | null>(null);
let pollTimer: ReturnType<typeof setTimeout> | null = null;

watch(
  () => props.hack.id,
  () => {
    version.value = currentVersion.value;
    error.value = null;
    patchFile.value = null;
    patchMember.value = null;
  },
);

const selectedLevel = computed<RomshelfMatchLevel>(
  () => (version.value && props.versionLevels[version.value]) || props.level,
);
const isOlderVersion = computed(
  () => !!version.value && version.value !== currentVersion.value,
);
const needsOwnPatch = computed(
  () => isOlderVersion.value && !hasPatch(version.value!, false),
);
const installedHere = computed(() =>
  props.installs.find(
    (r) =>
      (r.version ?? null) === (version.value ?? null) &&
      r.base_rom_id === props.romId,
  ),
);
const outdatedInstall = computed(() =>
  props.installs.find(
    (r) => currentVersion.value && r.version !== currentVersion.value,
  ),
);
// dl.vimm.net only serves files to visitors coming from a Vimm page, so link the page instead.
const isDirectFile = computed(
  () =>
    !!props.hack.downloadUrl &&
    !isOlderVersion.value &&
    !/^https?:\/\/dl\.vimm\.net\//i.test(props.hack.downloadUrl),
);
const downloadPage = computed(
  () =>
    (isDirectFile.value ? props.hack.downloadUrl : null) ||
    props.hack.links[0]?.url ||
    props.hack.pageUrl ||
    null,
);
const sourcePage = computed(
  () => props.hack.links[0]?.url || props.hack.downloadUrl || null,
);

// Wii Riivolution and Vita rePatch mods install like a patch: the backend lays the mod over the game
// (a Dolphin launcher next to the disc, or a patched .vpk). Other mod kinds only get install steps.
const modInstall = computed(
  () =>
    props.hack.install?.method === "riivolution" ||
    props.hack.install?.method === "vita-repatch",
);
const dropAccept = computed(() =>
  modInstall.value ? ".zip,.7z,.rar" : PATCH_ACCEPT,
);

// Other mod packages: the user downloads the file and installs it on the console.
const installFile = computed(() => props.hack.installUrl || props.hack.downloadUrl || null);

const blockedByMatch = computed(
  () => selectedLevel.value === "wrong_version" && !installAnyway.value,
);
const canSubmit = computed(
  () =>
    canInstall.value &&
    !installing.value &&
    !pendingRecord.value &&
    !blockedByMatch.value &&
    !!fileId.value &&
    (error.value?.code !== "choose_patch" || !!patchMember.value),
);

const installLabel = computed(() => {
  if (error.value?.code === "already_installed" || installedHere.value)
    return t("romshelf.replace-install");
  if (outdatedInstall.value && !installedHere.value && !isOlderVersion.value)
    return t("romshelf.update-to", { version: currentVersion.value });
  return t("romshelf.install");
});

async function submit(replace = false) {
  if (!fileId.value) return;
  installing.value = true;
  const previousCode = error.value?.code;
  error.value = null;
  try {
    const { data } = await romshelfApi.install({
      hackId: props.hack.id,
      romId: props.romId,
      fileId: fileId.value,
      version: version.value,
      patchFile: patchFile.value,
      patchMember: patchMember.value,
      replace: replace || previousCode === "already_installed",
    });
    if (!data.checksum_validated) {
      snackbar.warning(t("romshelf.checksum-warning"), { timeout: 6000 });
    }
    pendingRecord.value = data;
    poll(0);
  } catch (err) {
    error.value = installErrorOf(err);
  } finally {
    installing.value = false;
  }
}

function poll(attempt: number) {
  pollTimer = setTimeout(() => void checkInstall(attempt), POLL_MS);
}

async function checkInstall(attempt: number) {
  const record = pendingRecord.value;
  if (!record) return;
  try {
    const { data } = await romshelfApi.getInstall(record.id);
    if (data.status === "done" && data.rom_id) {
      if (props.platformSlug) rememberHackCore(data.rom_id, props.platformSlug, props.hack);
      pendingRecord.value = null;
      patchFile.value = null;
      snackbar.success(
        t("romshelf.installed-toast", { title: props.hack.title }),
        {
          icon: "mdi-check-bold",
          timeout: 5000,
          persist: { link: `/rom/${data.rom_id}` },
        },
      );
      emit("installed", data);
      return;
    }
  } catch {
    /* the next poll retries */
  }
  if (attempt < POLL_LIMIT) {
    poll(attempt + 1);
    return;
  }
  emit("installed", record);
  pendingRecord.value = null;
  snackbar.info(t("romshelf.install-waiting-scan"), { timeout: 8000 });
}

// Installs made before RomStop knew about emulators get their core too.
watch(
  () => installedHere.value?.rom_id,
  (romId) => {
    if (romId && props.platformSlug) rememberHackCore(romId, props.platformSlug, props.hack);
  },
  { immediate: true },
);
const browserCore = computed(() =>
  props.platformSlug ? browserCoreFor(props.hack, props.platformSlug) : null,
);

onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer);
});

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function onFiles(files: File[]) {
  patchFile.value = files[0] ?? null;
  if (patchFile.value) {
    patchMember.value = null;
    void submit();
  }
}
</script>

<template>
  <div
    v-if="hack.install && !modInstall"
    class="r-v2-rs-install r-v2-rs-install--guide"
  >
    <RAlert
      type="info"
      density="compact"
      :text="t('romshelf.install-only', { kind: hack.install.label })"
    />
    <p class="r-v2-rs-install__needs">{{ hack.install.needs }}</p>
    <ol class="r-v2-rs-install__steps">
      <li v-for="step in hack.install.steps" :key="step">{{ step }}</li>
    </ol>
    <p v-if="hack.install.emulator" class="r-v2-rs-install__needs">
      {{ hack.install.emulator }}
    </p>
    <div class="r-v2-rs-install__guide-links">
      <RBtn
        v-if="installFile"
        :href="installFile"
        target="_blank"
        rel="noreferrer"
        variant="flat"
        prepend-icon="mdi-download"
      >
        {{ t("romshelf.download-mod") }}
      </RBtn>
      <RBtn
        v-if="hack.install.guideUrl || sourcePage"
        :href="hack.install.guideUrl || sourcePage!"
        target="_blank"
        rel="noreferrer"
        variant="text"
        append-icon="mdi-open-in-new"
      >
        {{ t("romshelf.install-guide") }}
      </RBtn>
    </div>
  </div>
  <div v-else class="r-v2-rs-install">
    <RAlert
      v-if="modInstall"
      type="info"
      density="compact"
      :text="
        hack.install?.method === 'riivolution'
          ? t('romshelf.mod-install-wii')
          : t('romshelf.mod-install-vita')
      "
    />
    <RAlert
      v-if="browserCore && !browserCore.ok"
      type="warning"
      density="compact"
      :text="t('romshelf.compat-no-browser-core')"
    />
    <div v-if="installedHere?.rom_id" class="r-v2-rs-install__done">
      <RAlert type="success" density="compact">
        {{ t("romshelf.installed-as", { name: installedHere.fs_name }) }}
        <template #append>
          <RBtn
            size="small"
            variant="text"
            :to="{ name: ROUTES.ROM, params: { rom: installedHere.rom_id } }"
            append-icon="mdi-arrow-right"
          >
            {{ t("romshelf.open-rom") }}
          </RBtn>
        </template>
      </RAlert>
    </div>
    <RAlert
      v-else-if="installedHere && installedHere.status === 'pending'"
      type="info"
      density="compact"
      :text="t('romshelf.install-waiting-scan')"
    />
    <RAlert
      v-if="outdatedInstall && !installedHere"
      type="info"
      density="compact"
      icon="mdi-update"
      :text="
        t('romshelf.update-available', {
          installed: outdatedInstall.version ?? '?',
          version: currentVersion,
        })
      "
    />

    <div v-if="files.length > 1" class="r-v2-rs-install__controls">
      <RSelect
        v-model="fileId"
        :items="files"
        item-title="file_name"
        item-value="id"
        :label="t('romshelf.file-to-patch')"
        variant="outlined"
        density="comfortable"
        hide-details
        class="r-v2-rs-install__select"
      />
    </div>

    <RCheckbox
      v-if="selectedLevel === 'wrong_version'"
      v-model="installAnyway"
      :label="t('romshelf.install-anyway', { rom: needed })"
      hide-details
    />

    <RAlert
      v-if="needsOwnPatch && !patchFile"
      type="info"
      density="compact"
      :text="t('romshelf.older-version-hint')"
    />

    <RAlert
      v-if="error && error.code !== 'browser_download'"
      :type="error.code === 'choose_patch' ? 'info' : 'error'"
      density="compact"
      :text="error.message"
    />

    <RSelect
      v-if="error?.code === 'choose_patch' && error.candidates"
      v-model="patchMember"
      :items="error.candidates"
      :label="t('romshelf.choose-patch')"
      variant="outlined"
      density="comfortable"
      hide-details
    />

    <div
      v-if="error?.code === 'browser_download' || needsOwnPatch"
      class="r-v2-rs-install__manual"
    >
      <p v-if="error?.code === 'browser_download'" class="r-v2-rs-install__msg">
        <RIcon icon="mdi-download-lock-outline" class="r-v2-rs-install__icon" />
        {{ error.message }}
      </p>
      <RBtn
        v-if="downloadPage"
        :href="downloadPage"
        :target="isDirectFile ? undefined : '_blank'"
        :download="isDirectFile ? '' : undefined"
        rel="noopener"
        variant="outlined"
        size="small"
        :prepend-icon="isDirectFile ? 'mdi-download' : 'mdi-open-in-new'"
      >
        {{
          isDirectFile
            ? t("romshelf.download-patch-file")
            : t("romshelf.open-download-page")
        }}
      </RBtn>
      <RDropzone
        compact
        :accept="dropAccept"
        :title="t('romshelf.drop-patch')"
        :hint="t('romshelf.drop-patch-hint')"
        :active-title="t('romshelf.drop-patch-active')"
        :input-label="t('romshelf.drop-patch')"
        :disabled="installing || !!pendingRecord"
        @files="onFiles"
      />
    </div>

    <div class="r-v2-rs-install__actions">
      <RBtn
        :variant="installedHere ? 'outlined' : 'flat'"
        :color="installedHere ? undefined : 'primary'"
        :prepend-icon="installedHere ? 'mdi-refresh' : 'mdi-download'"
        :disabled="!canSubmit"
        :loading="installing"
        @click="submit(!!installedHere)"
      >
        {{ installLabel }}
      </RBtn>
      <RSelect
        v-if="versionItems.length > 1"
        v-model="version"
        :items="versionItems"
        item-title="title"
        item-value="value"
        :aria-label="t('romshelf.version')"
        prepend-inner-icon="mdi-history"
        variant="outlined"
        density="compact"
        hide-details
        class="r-v2-rs-install__version"
      >
        <template #item="{ props: itemProps, item }">
          <li v-bind="itemProps">
            <div class="r-select__item-stack">
              <div class="r-select__item-title">{{ item.title }}</div>
              <div v-if="item.raw.subtitle" class="r-select__item-subtitle">
                {{ item.raw.subtitle }}
              </div>
            </div>
          </li>
        </template>
      </RSelect>
      <span v-if="pendingRecord" class="r-v2-rs-install__pending">
        <RSpinner :size="14" />
        {{ t("romshelf.adding-to-library") }}
      </span>
      <span v-else-if="!canInstall" class="r-v2-rs-install__hint">
        {{ t("romshelf.no-permission") }}
      </span>
      <a
        v-if="sourcePage"
        :href="sourcePage"
        target="_blank"
        rel="noopener"
        class="r-v2-rs-install__source"
      >
        {{ t("romshelf.patch-source", { site: hostOf(sourcePage) }) }}
      </a>
    </div>
  </div>
</template>

<style scoped>
.r-v2-rs-install__needs {
  font-size: var(--r-font-size-sm);
  color: var(--r-color-fg-muted);
}
.r-v2-rs-install__steps {
  padding-left: 1.25rem;
  font-size: var(--r-font-size-sm);
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.r-v2-rs-install__guide-links {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.r-v2-rs-install {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-3);
}
.r-v2-rs-install__controls {
  display: flex;
  flex-wrap: wrap;
  gap: var(--r-space-3);
}
.r-v2-rs-install__select {
  flex: 1 1 220px;
  max-width: 360px;
}
.r-v2-rs-install__version {
  flex: 0 1 220px;
  min-width: 160px;
}
.r-v2-rs-install__manual {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--r-space-2);
}
.r-v2-rs-install__manual :deep(.r-dropzone) {
  align-self: stretch;
}
.r-v2-rs-install__msg {
  margin: 0;
  color: var(--r-color-fg-secondary);
  font-size: var(--r-font-size-sm);
}
.r-v2-rs-install__icon {
  margin-right: var(--r-space-1);
  color: var(--r-color-warning);
}
.r-v2-rs-install__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--r-space-3);
}
.r-v2-rs-install__pending,
.r-v2-rs-install__hint {
  display: inline-flex;
  align-items: center;
  gap: var(--r-space-2);
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-sm);
}
.r-v2-rs-install__source {
  margin-left: auto;
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-xs);
}
</style>
