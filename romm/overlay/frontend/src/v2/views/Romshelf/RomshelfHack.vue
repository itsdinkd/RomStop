<script setup lang="ts">
// RomshelfHack: one hack's page in the RomStop section, with the library
// ROMs it fits and an install panel for each. Save Rom Hack bookmarks it
// for later, e.g. when the library doesn't have the game yet.
import {
  RAlert,
  RCollapsible,
  RSkeletonBlock,
  RTag,
} from "@v2/lib";
import { computed, ref, watch } from "vue";
import { Translation, useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { ROUTES } from "@/plugins/routeNames";
import romshelfApi, {
  ROMSHELF_CONSOLE_SLUGS,
  type RomshelfHackResponse,
} from "@/services/api/romshelf";
import HackCounters from "@/v2/components/Romshelf/HackCounters.vue";
import HackDetails from "@/v2/components/Romshelf/HackDetails.vue";
import InstallPanel from "@/v2/components/Romshelf/InstallPanel.vue";
import MatchBadge from "@/v2/components/Romshelf/MatchBadge.vue";
import RollButton from "@/v2/components/Romshelf/RollButton.vue";
import SaveHackButton from "@/v2/components/Romshelf/SaveHackButton.vue";
import BackBtn from "@/v2/components/shared/BackBtn.vue";
import PlatformIcon from "@/v2/components/shared/PlatformIcon.vue";

const { t } = useI18n();
const route = useRoute();

const data = ref<RomshelfHackResponse | null>(null);
const loadError = ref<string | null>(null);

async function load() {
  loadError.value = null;
  try {
    data.value = (await romshelfApi.getHack(String(route.params.hack))).data;
  } catch (err) {
    loadError.value = (err as Error).message;
  }
}
watch(() => route.params.hack, load, { immediate: true });

const hack = computed(() => data.value?.hack ?? null);
// Only pages reached through Roll a Rom Hack offer another roll.
const rolled = computed(() => route.query.rolled === "1");
const fitting = computed(
  () => data.value?.matches.filter((m) => m.level !== "wrong_version") ?? [],
);
const otherVersions = computed(
  () => data.value?.matches.filter((m) => m.level === "wrong_version") ?? [],
);
// The original game in this library: the best match (matches come best
// first), so the base game's name opens its RomM page. Even a wrong-version
// dump is the same game. Null when the library doesn't have it.
const baseRomLink = computed(() => {
  const best = data.value?.matches[0];
  return best ? { name: ROUTES.ROM, params: { rom: best.rom_id } } : null;
});
</script>

<template>
  <section class="r-v2-rs-page">
    <div class="r-v2-rs-page__back">
      <BackBtn :label="t('romshelf.title')" :to="{ name: ROUTES.ROMSHELF }" />
    </div>

    <RAlert v-if="loadError" type="error" :text="loadError" />
    <div v-else-if="!hack" class="r-v2-rs-page__skeleton">
      <RSkeletonBlock height="48" />
      <RSkeletonBlock height="320" />
    </div>

    <template v-else>
      <header class="r-v2-rs-page__head">
        <PlatformIcon
          :slug="ROMSHELF_CONSOLE_SLUGS[hack.console] ?? hack.console"
          :size="36"
        />
        <div>
          <h1 class="r-v2-rs-page__title">{{ hack.title }}</h1>
          <p class="r-v2-rs-page__sub">
            <Translation keypath="romshelf.hack-of" tag="span" scope="global">
              <template #game>
                <RouterLink
                  v-if="baseRomLink"
                  :to="baseRomLink"
                  class="r-v2-rs-page__base-link"
                  :title="t('romshelf.open-base-rom')"
                >
                  {{ hack.baseGame }}
                </RouterLink>
                <template v-else>{{ hack.baseGame }}</template>
              </template>
            </Translation>
            <template v-if="hack.authors.length">
              · {{ hack.authors.join(", ") }}
            </template>
          </p>
        </div>
        <div class="r-v2-rs-page__head-end">
          <HackCounters :hack-id="hack.id" />
          <SaveHackButton v-model="data!.saved" :hack-id="hack.id" />
          <RTag v-if="hack.version" :text="hack.version" />
          <RollButton v-if="rolled" reroll :except-id="hack.id" />
        </div>
      </header>

      <div class="r-v2-rs-page__body">
        <HackDetails :hack="hack" :needed="data!.needed" :rom-link="baseRomLink" />

        <aside class="r-v2-rs-page__roms">
          <h2 class="r-v2-rs-page__h2">{{ t("romshelf.your-roms") }}</h2>
          <p v-if="!data!.matches.length" class="r-v2-rs-page__muted">
            {{ t("romshelf.no-rom-for-hack", { rom: data!.needed }) }}
          </p>
          <RCollapsible
            v-for="m in [...fitting, ...otherVersions]"
            :key="m.rom_id"
            :default-open="m === fitting[0]"
          >
            <template #title>
              <span class="r-v2-rs-page__rom">
                <span class="r-v2-rs-page__rom-name">{{ m.rom!.fs_name }}</span>
              </span>
            </template>
            <template #header-append>
              <MatchBadge :level="m.level" context="rom" />
            </template>
            <div class="r-v2-rs-page__rom-body">
            <HackDetails
              v-if="m.level === 'wrong_version'"
              :hack="hack"
              :needed="data!.needed"
              :match="m"
              :rom="m.rom ?? null"
              :version-levels="m.versions ?? {}"
              compact
              class="r-v2-rs-page__inline-need"
            />
            <InstallPanel
              :hack="hack"
              :rom-id="m.rom_id"
              :files="m.rom!.files"
              :default-file-id="m.file_id"
              :level="m.level"
              :version-levels="m.versions ?? {}"
              :installs="data!.installs"
              :needed="data!.needed"
              :platform-slug="m.rom!.platform_slug"
              @installed="load"
            />
            <RouterLink
              :to="{ name: ROUTES.ROM, params: { rom: m.rom_id }, query: { tab: 'romshelf' } }"
              class="r-v2-rs-page__open"
            >
              {{ t("romshelf.open-rom") }}
            </RouterLink>
            </div>
          </RCollapsible>
        </aside>
      </div>
    </template>
  </section>
</template>

<style scoped>
.r-v2-rs-page {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-4);
  padding: 32px var(--r-row-pad) 60px;
  max-width: var(--r-page-max-w);
}
.r-v2-rs-page__back {
  align-self: flex-start;
}
.r-v2-rs-page__rom-body {
  padding: var(--r-space-2) var(--r-space-4) var(--r-space-4);
}
.r-v2-rs-page__skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-3);
}
.r-v2-rs-page__head {
  display: flex;
  align-items: center;
  gap: var(--r-space-3);
}
.r-v2-rs-page__title {
  margin: 0;
  font-size: var(--r-font-size-3xl);
  font-weight: var(--r-font-weight-bold);
  color: var(--r-color-fg-heading);
}
.r-v2-rs-page__sub,
.r-v2-rs-page__muted {
  margin: 0;
  color: var(--r-color-fg-secondary);
}
.r-v2-rs-page__base-link {
  color: var(--r-color-fg-heading);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.r-v2-rs-page__base-link:hover {
  color: var(--r-color-brand-primary, var(--r-color-fg-heading));
}
.r-v2-rs-page__head-end {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: var(--r-space-3);
  margin-left: auto;
}
.r-v2-rs-page__body {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(300px, 1fr);
  gap: var(--r-space-6);
  align-items: start;
}
.r-v2-rs-page__roms {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-3);
  position: sticky;
  top: calc(var(--r-nav-h) + var(--r-space-4));
}
.r-v2-rs-page__h2 {
  margin: 0;
  font-size: var(--r-font-size-lg);
  color: var(--r-color-fg-heading);
}
.r-v2-rs-page__rom-name {
  overflow-wrap: anywhere;
  text-align: left;
}
.r-v2-rs-page__inline-need {
  margin-bottom: var(--r-space-3);
}
.r-v2-rs-page__open {
  display: inline-block;
  margin-top: var(--r-space-3);
  color: var(--r-color-brand-primary, var(--r-color-fg-heading));
  font-size: var(--r-font-size-sm);
}
html[data-bp~="sm-and-down"] .r-v2-rs-page {
  padding: 16px 14px 24px;
}
html[data-bp~="sm-and-down"] .r-v2-rs-page__head {
  flex-wrap: wrap;
}
html[data-bp~="sm-and-down"] .r-v2-rs-page__body {
  grid-template-columns: 1fr;
}
html[data-bp~="sm-and-down"] .r-v2-rs-page__roms {
  position: static;
}
</style>
