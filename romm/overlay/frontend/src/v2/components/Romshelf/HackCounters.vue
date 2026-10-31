<script setup lang="ts">
// HackCounters: how often this RomM installed (downloads) and played a hack,
// each an icon with a tooltip. Hidden if the counts can't be loaded.
import { RIcon, RTooltip } from "@v2/lib";
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import romshelfApi, { type RomshelfCounts } from "@/services/api/romshelf";

const props = defineProps<{ hackId: string }>();
const { t } = useI18n();
const counts = ref<RomshelfCounts | null>(null);

watch(
  () => props.hackId,
  async (id) => {
    counts.value = null;
    try {
      const got = (await romshelfApi.getCounts(id)).data.counts;
      if (id === props.hackId) counts.value = got;
    } catch {
      counts.value = null;
    }
  },
  { immediate: true },
);

const fmt = (n: number) =>
  new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n);
</script>

<template>
  <div v-if="counts" class="r-v2-rs-counters">
    <RTooltip :text="t('romshelf.count-downloads')" :hint="t('romshelf.count-in-romm')">
      <template #activator="{ props: tip }">
        <span v-bind="tip" class="r-v2-rs-counters__item" tabindex="0">
          <RIcon icon="mdi-download" size="16" class="r-v2-rs-counters__dl" />
          <span class="r-v2-rs-sr">{{ t("romshelf.count-downloads") }}:</span>
          {{ fmt(counts.downloads) }}
        </span>
      </template>
    </RTooltip>
    <RTooltip :text="t('romshelf.count-plays')" :hint="t('romshelf.count-in-romm')">
      <template #activator="{ props: tip }">
        <span v-bind="tip" class="r-v2-rs-counters__item" tabindex="0">
          <RIcon icon="mdi-play" size="16" class="r-v2-rs-counters__play" />
          <span class="r-v2-rs-sr">{{ t("romshelf.count-plays") }}:</span>
          {{ fmt(counts.plays) }}
        </span>
      </template>
    </RTooltip>
  </div>
</template>

<style scoped>
.r-v2-rs-counters {
  display: inline-flex;
  gap: var(--r-space-2);
}
.r-v2-rs-counters__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: var(--r-radius-sm);
  border: 1px solid color-mix(in srgb, white 10%, transparent);
  font-variant-numeric: tabular-nums;
  font-size: var(--r-font-size-sm);
  color: var(--r-color-fg-heading);
}
.r-v2-rs-counters__dl {
  color: var(--r-color-brand-primary);
}
.r-v2-rs-counters__play {
  color: hotpink;
}
.r-v2-rs-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
