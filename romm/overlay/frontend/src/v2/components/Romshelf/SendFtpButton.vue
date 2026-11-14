<script setup lang="ts">
// SendFtpButton: "Send with FTP" for one library game (usually an installed hack).
// RomM uploads the file to the FTP target an admin set up under Settings > Send with FTP,
// say a PS5's FTP payload next to RetroArch, into <base path>/<console folder>/[Hacks/].
// Hidden while Send with FTP is off.
import { RBtn, RSpinner } from "@v2/lib";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import romshelfApi, {
  installErrorOf,
  type FtpJob,
} from "@/services/api/romshelf";
import { useCan } from "@/v2/composables/useCan";
import { useSnackbar } from "@/v2/composables/useSnackbar";
import { formatBytes, useFtpStatus } from "@/v2/components/Romshelf/ftp";

const props = withDefaults(
  defineProps<{
    romId: number;
    title?: string;
    size?: "small" | "default";
    /** A send RomM's server started by itself (after an install); shown while it is still going. */
    jobId?: string | null;
  }>(),
  { title: "", size: "default", jobId: null },
);

const { t } = useI18n();
const snackbar = useSnackbar();
const canSend = useCan("rom.upload");
const ftp = useFtpStatus();
const job = ref<FtpJob | null>(null);
const starting = ref(false);
let timer: ReturnType<typeof setTimeout> | null = null;

const visible = computed(() => ftp.value.enabled && canSend.value);
const busy = computed(
  () =>
    starting.value ||
    job.value?.status === "running" ||
    job.value?.status === "queued",
);
const progress = computed(() => {
  const j = job.value;
  if (!j || j.status !== "running" || !j.total) return "";
  return t("romshelf.ftp-progress", {
    done: formatBytes(j.done),
    total: formatBytes(j.total),
    percent: Math.floor((100 * j.done) / j.total),
  });
});

async function send() {
  if (busy.value || !visible.value) return;
  starting.value = true;
  try {
    const { data } = await romshelfApi.sendWithFtp(props.romId);
    job.value = data;
    follow(data);
  } catch (err) {
    snackbar.error(installErrorOf(err).message, { timeout: 8000 });
  } finally {
    starting.value = false;
  }
}

function follow(j: FtpJob) {
  if (j.status === "running" || j.status === "queued") {
    timer = setTimeout(() => void poll(j.id), 1000);
    return;
  }
  const name = props.title || t("romshelf.ftp-the-game");
  if (j.status === "done") {
    snackbar.success(
      j.sent === 0 && j.skipped > 0
        ? t("romshelf.ftp-already-there", {
            title: name,
            target: j.target,
            dest: j.dest,
          })
        : t("romshelf.ftp-sent", {
            title: name,
            target: j.target,
            dest: j.dest,
          }),
      { icon: "mdi-check-bold", timeout: 6000 },
    );
  } else {
    snackbar.error(j.message || t("romshelf.ftp-failed"), { timeout: 10000 });
  }
}

async function poll(id: string) {
  try {
    const { data } = await romshelfApi.getFtpJob(id);
    job.value = data;
    follow(data);
  } catch {
    timer = setTimeout(() => void poll(id), 3000);
  }
}

// Follow a send the server started. With `quietIfOver`, a send that already ended says nothing
// (the page was opened later); otherwise its result is shown like a click's.
async function track(id: string, quietIfOver = false) {
  if (!/^[0-9a-f]{32}$/.test(id) || job.value?.id === id) return;
  try {
    const { data } = await romshelfApi.getFtpJob(id);
    if (quietIfOver && data.status !== "running" && data.status !== "queued")
      return;
    job.value = data;
    follow(data);
  } catch {
    /* an unknown or expired job: nothing to show */
  }
}

watch(
  () => props.jobId,
  (id) => {
    if (id) void track(id, true);
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer);
});

defineExpose({ send, track, visible });
</script>

<template>
  <span v-if="visible" class="r-v2-rs-ftp">
    <RBtn
      variant="outlined"
      :size="size"
      prepend-icon="mdi-upload-network-outline"
      :loading="starting"
      :disabled="busy"
      :title="
        ftp.name ? t('romshelf.ftp-send-to', { target: ftp.name }) : undefined
      "
      @click="send"
    >
      {{ t("romshelf.ftp-send") }}
    </RBtn>
    <span
      v-if="job?.status === 'running' || job?.status === 'queued'"
      class="r-v2-rs-ftp__progress"
    >
      <RSpinner :size="14" />
      {{
        job.status === "queued"
          ? t("romshelf.ftp-queued", { target: job.target })
          : progress || t("romshelf.ftp-sending", { target: job.target })
      }}
    </span>
  </span>
</template>

<style scoped>
.r-v2-rs-ftp {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--r-space-2);
}
.r-v2-rs-ftp__progress {
  display: inline-flex;
  align-items: center;
  gap: var(--r-space-2);
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-sm);
}
</style>
