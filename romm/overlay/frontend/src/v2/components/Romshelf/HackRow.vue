<script setup lang="ts">
// HackRow: one hack for the ROM on screen, collapsed to a single line until
// opened.
import { RCollapsible, RTag } from "@v2/lib";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type {
  RomshelfInstall,
  RomshelfLibraryRom,
  RomshelfRomHack,
} from "@/services/api/romshelf";
import HackDetails from "@/v2/components/Romshelf/HackDetails.vue";
import HackThumb from "@/v2/components/Romshelf/HackThumb.vue";
import InstallPanel from "@/v2/components/Romshelf/InstallPanel.vue";
import MatchBadge from "@/v2/components/Romshelf/MatchBadge.vue";
import { hasCompatWarning } from "@/v2/components/Romshelf/compat";

const props = defineProps<{
  entry: RomshelfRomHack;
  rom: RomshelfLibraryRom;
}>();

const emit = defineEmits<{ (e: "installed", record: RomshelfInstall): void }>();

const { t } = useI18n();

const hack = computed(() => props.entry.hack);
const current = computed(
  () => hack.value.versions[0]?.version ?? hack.value.version,
);
const installState = computed(() => {
  const mine = props.entry.installs;
  if (!mine.length) return null;
  if (current.value && !mine.some((r) => r.version === current.value))
    return "update";
  return "installed";
});
</script>

<template>
  <RCollapsible class="r-v2-rs-row">
    <template #header-prepend>
      <HackThumb :hack="hack" :size="40" />
    </template>
    <template #title>
      <span class="r-v2-rs-row__title">
        <span class="r-v2-rs-row__name">{{ hack.title }}</span>
        <span class="r-v2-rs-row__meta">
          {{ hack.type }} · {{ hack.authors.join(", ") }}
          <template v-if="hack.downloads">
            · {{ t("romshelf.downloads", { n: hack.downloads.toLocaleString() }) }}
          </template>
          <template v-if="hack.favs">
            · {{ t("romshelf.favs-count", { n: hack.favs.toLocaleString() }) }}
          </template>
        </span>
        <span v-if="hack.summary" class="r-v2-rs-row__summary">
          {{ hack.summary }}
        </span>
      </span>
    </template>
    <template #header-append>
      <span class="r-v2-rs-row__tags">
        <RTag
          v-if="installState === 'update'"
          tone="info"
          prepend-icon="mdi-update"
          size="small"
          :text="t('romshelf.update-tag')"
        />
        <RTag
          v-else-if="installState === 'installed'"
          tone="brand"
          prepend-icon="mdi-check"
          size="small"
          :text="t('romshelf.installed-tag')"
        />
        <RTag
          v-if="hasCompatWarning(hack)"
          tone="warning"
          prepend-icon="mdi-alert"
          size="small"
          :text="t('romshelf.compat-tag')"
          :title="hack.compatibility?.summary"
        />
        <RTag v-if="current" size="small" :text="current" />
        <MatchBadge :level="entry.match.level" context="rom" />
      </span>
    </template>

    <div class="r-v2-rs-row__body">
      <HackDetails
        :hack="hack"
        :needed="entry.needed"
        :match="entry.match"
        :rom="rom"
        :version-levels="entry.versions"
      >
        <InstallPanel
          :hack="hack"
          :rom-id="rom.id"
          :files="rom.files"
          :default-file-id="entry.match.file_id"
          :level="entry.match.level"
          :version-levels="entry.versions"
          :installs="entry.installs"
          :needed="entry.needed"
          :platform-slug="rom.platform_slug"
          @installed="emit('installed', $event)"
        />
      </HackDetails>
    </div>
  </RCollapsible>
</template>

<style scoped>
.r-v2-rs-row__title {
  display: flex;
  flex-direction: column;
  min-width: 0;
  text-align: left;
}
.r-v2-rs-row__name {
  font-weight: var(--r-font-weight-semibold);
  color: var(--r-color-fg-heading);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.r-v2-rs-row__meta {
  font-size: var(--r-font-size-xs);
  color: var(--r-color-fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.r-v2-rs-row__summary {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  overflow: hidden;
  margin-top: 2px;
  font-size: var(--r-font-size-sm);
  color: var(--r-color-fg-secondary);
  white-space: normal;
}
.r-v2-rs-row__tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--r-space-2);
}
.r-v2-rs-row__body {
  padding: var(--r-space-2) var(--r-space-4) var(--r-space-4);
}
html[data-bp~="sm-and-down"] .r-v2-rs-row__meta {
  display: none;
}
</style>
