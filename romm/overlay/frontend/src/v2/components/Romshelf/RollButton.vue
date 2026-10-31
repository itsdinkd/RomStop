<script setup lang="ts">
// RollButton: "Roll a Rom Hack" (index page) or "Re-roll" (a hack page the
// roll opened). Picks among hacks that aren't installed and have a ROM in
// the library to patch; when there are none it says so in a tooltip
// instead of opening the reel.
import { RBtn, RTooltip } from "@v2/lib";
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import romshelfApi, {
  type RomshelfConsole,
  type RomshelfHackSummary,
} from "@/services/api/romshelf";
import RollDialog from "@/v2/components/Romshelf/RollDialog.vue";
import { rememberedHacks, rollable } from "@/v2/components/Romshelf/roll";

const props = withDefaults(
  defineProps<{
    reroll?: boolean;
    /** The list already on screen; fetched when not given. */
    hacks?: RomshelfHackSummary[] | null | undefined;
    consoles?: Record<string, RomshelfConsole> | undefined;
    /** Never land on this hack again (the one a re-roll leaves). */
    exceptId?: string | undefined;
  }>(),
  { reroll: false, hacks: undefined, consoles: undefined, exceptId: undefined },
);

const { t } = useI18n();
const loading = ref(false);
const open = ref(false);
const pool = ref<RomshelfHackSummary[]>([]);
const consoleInfo = ref<Record<string, RomshelfConsole> | undefined>(props.consoles);
const noneTip = ref(false);
let tipTimer: ReturnType<typeof setTimeout> | undefined;

async function start() {
  loading.value = true;
  try {
    let list = props.hacks ?? rememberedHacks();
    if (!list) {
      const data = (await romshelfApi.getHacks()).data;
      list = data.hacks;
      consoleInfo.value ??= data.consoles;
    }
    pool.value = rollable(list, props.exceptId);
  } catch {
    pool.value = [];
  } finally {
    loading.value = false;
  }
  if (!pool.value.length) {
    noneTip.value = true;
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => (noneTip.value = false), 3500);
    return;
  }
  open.value = true;
}
</script>

<template>
  <RTooltip :model-value="noneTip" :text="t('romshelf.roll-none')" location="bottom">
    <template #activator="{ props: tip }">
      <span v-bind="tip" class="r-v2-rs-rollbtn" :class="{ 'r-v2-rs-rollbtn--re': reroll }">
        <RBtn
          :variant="reroll ? 'flat' : 'translucent'"
          color="primary"
          :size="reroll ? 'default' : 'small'"
          prepend-icon="mdi-dice-multiple"
          :loading="loading"
          @click="start"
        >
          {{ reroll ? t("romshelf.reroll") : t("romshelf.roll") }}
        </RBtn>
      </span>
    </template>
  </RTooltip>
  <RollDialog
    v-if="pool.length"
    v-model="open"
    :pool="pool"
    :consoles="consoleInfo ?? consoles"
  />
</template>

<style scoped>
.r-v2-rs-rollbtn {
  display: inline-flex;
  border-radius: var(--r-radius-md);
}
/* The re-roll button is the next step after a roll: make it easy to spot. */
.r-v2-rs-rollbtn--re {
  box-shadow: 0 0 18px color-mix(in srgb, var(--r-color-brand-primary) 55%, transparent);
  animation: r-v2-rs-rollbtn-pulse 2.4s ease-in-out infinite;
}
@keyframes r-v2-rs-rollbtn-pulse {
  50% {
    box-shadow: 0 0 4px color-mix(in srgb, var(--r-color-brand-primary) 30%, transparent);
  }
}
@media (prefers-reduced-motion: reduce) {
  .r-v2-rs-rollbtn--re {
    animation: none;
  }
}
</style>
