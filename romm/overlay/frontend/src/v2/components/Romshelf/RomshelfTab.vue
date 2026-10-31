<script setup lang="ts">
// RomshelfTab: the RomStop section of a game's page. Lists every hack made
// for this game, one collapsed row each, with whether this ROM is the one
// the hack needs.
import {
  RAlert,
  RBtn,
  REmptyState,
  RChip,
  RIcon,
  RSkeletonBlock,
  RTag,
  RTextField,
} from "@v2/lib";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import type { DetailedRomSchema } from "@/__generated__";
import { ROUTES } from "@/plugins/routeNames";
import romshelfApi, {
  rankScore,
  type RomshelfMatchLevel,
  type RomshelfRomHack,
  type RomshelfRomResponse,
} from "@/services/api/romshelf";
import HackRow from "@/v2/components/Romshelf/HackRow.vue";

const props = defineProps<{ rom: DetailedRomSchema }>();
const emit = defineEmits<{ (e: "count", n: number): void }>();

const { t } = useI18n();
const data = ref<RomshelfRomResponse | null>(null);
const loading = ref(false);
const loadError = ref<string | null>(null);

async function load() {
  loading.value = true;
  loadError.value = null;
  try {
    const res = await romshelfApi.getRomHacks(props.rom.id);
    data.value = res.data;
    emit("count", res.data.hacks.length);
  } catch (err) {
    loadError.value = (err as Error).message;
  } finally {
    loading.value = false;
  }
}

watch(() => props.rom.id, load, { immediate: true });

// Popular games have hundreds of hacks: best fit and top rated first, a
// small category and sort bar, a search box, and the list shown a page at a
// time.
const PAGE = 25;
const LEVEL_RANK: Record<RomshelfMatchLevel, number> = {
  exact: 0,
  likely: 1,
  wrong_version: 2,
  none: 3,
};
type Sort = "fit" | "title" | "rating" | "downloads" | "favs";
const search = ref("");
const hackType = ref<string | null>(null);
const sort = ref<Sort>("fit");
const limit = ref(PAGE);
watch([search, hackType, sort, () => props.rom.id], () => (limit.value = PAGE));
watch(
  () => props.rom.id,
  () => (hackType.value = null),
);

function norm(s: string) {
  return s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, " ");
}
type Compare = (a: RomshelfRomHack, b: RomshelfRomHack) => number;
const byFavs: Compare = (a, b) => (b.hack.favs ?? -1) - (a.hack.favs ?? -1);
const byRating: Compare = (a, b) =>
  rankScore(b.hack.rating) - rankScore(a.hack.rating) || byFavs(a, b);
const SORTS: Record<Sort, Compare> = {
  fit: (a, b) =>
    Number(b.installs.length > 0) - Number(a.installs.length > 0) ||
    LEVEL_RANK[a.match.level] - LEVEL_RANK[b.match.level] ||
    byRating(a, b),
  title: (a, b) => a.hack.title.localeCompare(b.hack.title),
  rating: byRating,
  downloads: (a, b) =>
    (b.hack.downloads ?? -1) - (a.hack.downloads ?? -1) || byRating(a, b),
  favs: (a, b) => byFavs(a, b) || SORTS.downloads(a, b),
};
const sorted = computed(() =>
  [...(data.value?.hacks ?? [])].sort(SORTS[sort.value]),
);
const filtered = computed(() => {
  const words = norm(search.value).trim().split(" ").filter(Boolean);
  return sorted.value.filter((e) => {
    if (hackType.value && e.hack.type !== hackType.value) return false;
    if (!words.length) return true;
    const hay = norm(
      [
        e.hack.title,
        e.hack.summary ?? "",
        e.hack.authors.join(" "),
        e.hack.type,
        ...(e.hack.tags ?? []),
      ].join(" "),
    );
    return words.every((w) => hay.includes(w));
  });
});

// Only types this game's hacks use, with how many of each.
const typeItems = computed(() => {
  const counts = new Map<string, number>();
  for (const e of data.value?.hacks ?? [])
    counts.set(e.hack.type, (counts.get(e.hack.type) ?? 0) + 1);
  return [...counts]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([type, n]) => ({ title: `${type} (${n})`, value: type }));
});
const hasDownloads = computed(
  () => data.value?.hacks.some((e) => e.hack.downloads != null) ?? false,
);
const hasFavs = computed(
  () => data.value?.hacks.some((e) => e.hack.favs != null) ?? false,
);
const sortItems = computed(() => [
  { title: t("romshelf.sort-short-fit"), value: "fit" as Sort },
  { title: t("romshelf.sort-short-title"), value: "title" as Sort },
  { title: t("romshelf.sort-short-rating"), value: "rating" as Sort },
  ...(hasDownloads.value
    ? [{ title: t("romshelf.sort-short-downloads"), value: "downloads" as Sort }]
    : []),
  ...(hasFavs.value
    ? [{ title: t("romshelf.sort-short-favs"), value: "favs" as Sort }]
    : []),
]);
const visible = computed(() => filtered.value.slice(0, limit.value));

const fits = computed(
  () =>
    data.value?.hacks.filter((h) => h.match.level !== "wrong_version")
      .length ?? 0,
);
const installedHack = computed(() => data.value?.installed_hack ?? null);
const installedVersion = computed(
  () => data.value?.installed_from?.version ?? null,
);
const hackOutdated = computed(
  () =>
    !!installedHack.value?.version &&
    !!installedVersion.value &&
    installedHack.value.version !== installedVersion.value,
);
</script>

<template>
  <div class="r-v2-rs-tab">
    <RAlert
      v-if="installedHack"
      :type="hackOutdated ? 'info' : 'success'"
      density="compact"
      :icon="hackOutdated ? 'mdi-update' : 'mdi-bookshelf'"
    >
      {{
        t("romshelf.this-is-hack", {
          title: installedHack.title,
          version: installedVersion ?? "",
          game: installedHack.baseGame,
        })
      }}
      <template v-if="hackOutdated">
        {{ t("romshelf.this-is-outdated", { version: installedHack.version }) }}
      </template>
      <template #append>
        <RBtn
          size="small"
          variant="text"
          :to="{ name: ROUTES.ROMSHELF_HACK, params: { hack: installedHack.id } }"
        >
          {{ t("romshelf.view-hack") }}
        </RBtn>
      </template>
    </RAlert>

    <div v-if="loading && !data" class="r-v2-rs-tab__skeleton">
      <RSkeletonBlock v-for="i in 3" :key="i" height="56" />
    </div>

    <RAlert v-else-if="loadError" type="error" :text="loadError" />

    <REmptyState
      v-else-if="data && !data.supported"
      icon="mdi-bookshelf"
      :title="t('romshelf.unsupported-title')"
      :hint="t('romshelf.unsupported-hint')"
    />

    <section v-else-if="data && data.hacks.length" class="r-v2-rs-tab__section">
      <header class="r-v2-rs-tab__heading">
        <RIcon icon="mdi-bookshelf" size="18" />
        {{ t("romshelf.section-title") }}
        <RTag size="small" :text="String(data.hacks.length)" />
        <RTag
          v-if="fits"
          size="small"
          tone="success"
          prepend-icon="mdi-check"
          :text="t('romshelf.fits-count', { n: fits })"
        />
      </header>
      <div v-if="data.hacks.length > 1" class="r-v2-rs-tab__filters">
        <div v-if="typeItems.length > 1" class="r-v2-rs-tab__filter-row">
          <span class="r-v2-rs-tab__filter-label">{{ t("romshelf.category") }}</span>
          <RChip
            size="small"
            :variant="hackType ? 'outlined' : 'flat'"
            :color="hackType ? undefined : 'primary'"
            @click="hackType = null"
          >
            {{ t("romshelf.show-all") }} ({{ data.hacks.length }})
          </RChip>
          <RChip
            v-for="item in typeItems"
            :key="item.value"
            size="small"
            :variant="hackType === item.value ? 'flat' : 'outlined'"
            :color="hackType === item.value ? 'primary' : undefined"
            @click="hackType = hackType === item.value ? null : item.value"
          >
            {{ item.title }}
          </RChip>
        </div>
        <div class="r-v2-rs-tab__filter-row">
          <span class="r-v2-rs-tab__filter-label">{{ t("romshelf.sort") }}</span>
          <RChip
            v-for="item in sortItems"
            :key="item.value"
            size="small"
            :variant="sort === item.value ? 'flat' : 'outlined'"
            :color="sort === item.value ? 'primary' : undefined"
            :prepend-icon="sort === item.value ? 'mdi-sort-descending' : undefined"
            @click="sort = item.value"
          >
            {{ item.title }}
          </RChip>
        </div>
      </div>
      <RTextField
        v-if="data.hacks.length > 3"
        v-model="search"
        :placeholder="t('romshelf.search-these')"
        prepend-inner-icon="mdi-magnify"
        density="compact"
        hide-details
        clearable
      />
      <div class="r-v2-rs-tab__list">
        <HackRow
          v-for="entry in visible"
          :key="entry.hack.id"
          :entry="entry"
          :rom="data.rom!"
          @installed="load"
        />
        <p v-if="!filtered.length" class="r-v2-rs-tab__none">
          {{ t("romshelf.no-results") }}
        </p>
        <RBtn
          v-if="filtered.length > visible.length"
          variant="outlined"
          size="small"
          class="r-v2-rs-tab__more"
          @click="limit += PAGE"
        >
          {{ t("romshelf.show-more", { n: filtered.length - visible.length }) }}
        </RBtn>
      </div>
    </section>

    <REmptyState
      v-else-if="data && !installedHack"
      icon="mdi-bookshelf"
      :title="t('romshelf.none-title')"
      :hint="
        data.status.error
          ? t('romshelf.index-error', { error: data.status.error })
          : t('romshelf.none-hint', { n: data.status.hack_count })
      "
    />

    <div class="r-v2-rs-tab__foot">
      <RBtn
        variant="text"
        size="small"
        prepend-icon="mdi-bookshelf"
        :to="{ name: ROUTES.ROMSHELF }"
      >
        {{ t("romshelf.browse-all") }}
      </RBtn>
    </div>
  </div>
</template>

<style scoped>
.r-v2-rs-tab {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-3);
}
.r-v2-rs-tab__skeleton,
.r-v2-rs-tab__list {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-2);
}
.r-v2-rs-tab__section {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-3);
}
.r-v2-rs-tab__heading {
  display: flex;
  align-items: center;
  gap: var(--r-space-2);
  font-weight: var(--r-font-weight-semibold);
}
.r-v2-rs-tab__filters {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-2);
}
.r-v2-rs-tab__filter-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--r-space-1);
}
.r-v2-rs-tab__filter-label {
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-xs);
  font-weight: var(--r-font-weight-semibold);
  letter-spacing: 0.04em;
  margin-right: var(--r-space-1);
  text-transform: uppercase;
}
.r-v2-rs-tab__none {
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-sm);
  margin: 0;
}
.r-v2-rs-tab__more {
  align-self: center;
}
.r-v2-rs-tab__foot {
  display: flex;
  justify-content: flex-end;
}
</style>
