<script setup lang="ts">
// SaveHackButton: "Save Rom Hack", a bookmark kept on the RomM server for the
// signed-in user. Mostly for hacks whose game isn't in the library yet, so
// they can be found again with the Saved filter once the ROM is added.
// `compact` is the icon-only version for list rows; `overlay` puts it on a
// dark scrim for use over cover art.
import { RBtn } from "@v2/lib";
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import romshelfApi from "@/services/api/romshelf";
import { useSnackbar } from "@/v2/composables/useSnackbar";

const props = defineProps<{
  hackId: string;
  compact?: boolean;
  overlay?: boolean;
}>();
const saved = defineModel<boolean>({ required: true });
const { t } = useI18n();
const snackbar = useSnackbar();
const busy = ref(false);

const icon = computed(() =>
  saved.value ? "mdi-bookmark" : "mdi-bookmark-outline",
);
const label = computed(() =>
  saved.value ? t("romshelf.unsave-hack") : t("romshelf.save-hack"),
);

async function toggle() {
  if (busy.value) return;
  const next = !saved.value;
  busy.value = true;
  saved.value = next;
  try {
    await romshelfApi.setSaved(props.hackId, next);
  } catch {
    saved.value = !next;
    snackbar.error(t("romshelf.save-failed"), { timeout: 6000 });
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <RBtn
    v-if="compact"
    :icon="icon"
    size="small"
    variant="text"
    :color="saved && !overlay ? 'primary' : undefined"
    :tooltip="label"
    :aria-label="label"
    :aria-pressed="saved"
    :class="{ 'r-v2-rs-save--overlay': overlay, 'r-v2-rs-save--on': saved }"
    @click.prevent.stop="toggle"
  />
  <RBtn
    v-else
    :prepend-icon="icon"
    size="small"
    variant="outlined"
    :active="saved"
    @click="toggle"
  >
    {{ saved ? t("romshelf.saved-hack") : t("romshelf.save-hack") }}
  </RBtn>
</template>

<style scoped>
.r-v2-rs-save--overlay {
  background: var(--r-color-overlay-scrim-strong);
  color: var(--r-color-overlay-fg);
}
.r-v2-rs-save--overlay.r-v2-rs-save--on {
  color: var(--r-color-brand-primary);
}
</style>
