<script setup lang="ts">
// ChangelogDialog: every version of a hack, newest first, with its changes.
import { RDialog, RTag } from "@v2/lib";
import { useI18n } from "vue-i18n";
import type {
  RomshelfMatchLevel,
  RomshelfVersion,
} from "@/services/api/romshelf";
import MatchBadge from "@/v2/components/Romshelf/MatchBadge.vue";
import RichText from "@/v2/components/Romshelf/RichText.vue";

defineProps<{
  modelValue: boolean;
  title: string;
  versions: RomshelfVersion[];
  current?: string | undefined;
  levels?: Record<string, RomshelfMatchLevel> | undefined;
}>();

const emit = defineEmits<{ (e: "update:modelValue", v: boolean): void }>();

const { t } = useI18n();
</script>

<template>
  <RDialog
    :model-value="modelValue"
    icon="mdi-history"
    width="560"
    scroll-content
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template #header>
      {{ t("romshelf.changelog-title", { title }) }}
    </template>
    <template #content>
      <p v-if="!versions.length" class="r-v2-rs-log__empty">
        {{ t("romshelf.changelog-empty") }}
      </p>
      <ol class="r-v2-rs-log">
        <li v-for="v in versions" :key="v.version" class="r-v2-rs-log__item">
          <div class="r-v2-rs-log__head">
            <span class="r-v2-rs-log__version">{{ v.version }}</span>
            <RTag
              v-if="v.version === current"
              tone="brand"
              size="x-small"
              :text="t('romshelf.current')"
            />
            <span v-if="v.date" class="r-v2-rs-log__date">{{ v.date }}</span>
            <MatchBadge
              v-if="levels && levels[v.version]"
              :level="levels[v.version]!"
              size="x-small"
            />
          </div>
          <p v-if="v.baseRom?.title" class="r-v2-rs-log__base">
            {{ t("romshelf.needs-rom", { rom: v.baseRom.title }) }}
          </p>
          <ul v-if="v.changelog?.length" class="r-v2-rs-log__changes">
            <li v-for="(c, i) in v.changelog" :key="i">
              <RichText :text="c" inline />
            </li>
          </ul>
          <p v-else class="r-v2-rs-log__none">
            {{ t("romshelf.no-changes-listed") }}
          </p>
        </li>
      </ol>
    </template>
  </RDialog>
</template>

<style scoped>
.r-v2-rs-log {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--r-space-4);
}
.r-v2-rs-log__item {
  border-left: 2px solid var(--r-color-border);
  padding-left: var(--r-space-3);
}
.r-v2-rs-log__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--r-space-2);
}
.r-v2-rs-log__version {
  font-weight: var(--r-font-weight-semibold);
  color: var(--r-color-fg-heading);
}
.r-v2-rs-log__date,
.r-v2-rs-log__base,
.r-v2-rs-log__none,
.r-v2-rs-log__empty {
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-sm);
}
.r-v2-rs-log__base,
.r-v2-rs-log__none {
  margin: var(--r-space-1) 0 0;
}
.r-v2-rs-log__changes {
  list-style: disc;
  margin: var(--r-space-2) 0 0;
  padding-left: var(--r-space-5);
  color: var(--r-color-fg-secondary);
  font-size: var(--r-font-size-sm);
  line-height: var(--r-line-height-relaxed);
}
</style>
