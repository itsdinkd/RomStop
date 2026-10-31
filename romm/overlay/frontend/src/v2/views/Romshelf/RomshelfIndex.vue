<script lang="ts">
import type { RomshelfListResponse } from "@/services/api/romshelf";

// The last list fetched, kept for the session so coming back to this page
// shows it at once while a fresh copy loads.
let lastList: RomshelfListResponse | null = null;
</script>

<script setup lang="ts">
// RomshelfIndex: every hack in the RomStop index, browsable like the
// RomStop site: consoles, search, type, sort, grid or list. Each hack shows
// whether the library has a ROM it can patch (green check) or only another
// version of the game (caution). Filters live in the URL. On phones the
// header actions wrap under the title and the Show switch becomes a select.
// The bookmark on each hack is Save Rom Hack (kept per user on the server);
// the Saved toggle narrows any Show choice to saved hacks, so Saved + Need
// the game lists the games still to add to the library.
import {
  RAlert,
  RBtn,
  RChip,
  REmptyState,
  RIcon,
  RSelect,
  RSkeletonBlock,
  RSliderBtnGroup,
  RTextField,
} from "@v2/lib";
import { useIntersectionObserver } from "@vueuse/core";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { ROUTES } from "@/plugins/routeNames";
import romshelfApi, {
  ROMSHELF_CONSOLE_SLUGS,
  rankScore,
  type RomshelfHackSummary,
  type RomshelfMatchLevel,
} from "@/services/api/romshelf";
import HackThumb from "@/v2/components/Romshelf/HackThumb.vue";
import RollButton from "@/v2/components/Romshelf/RollButton.vue";
import SaveHackButton from "@/v2/components/Romshelf/SaveHackButton.vue";
import { rememberHacks } from "@/v2/components/Romshelf/roll";
import SaveImportDialog from "@/v2/components/Romshelf/SaveImportDialog.vue";
import IndexShell from "@/v2/components/shared/IndexShell.vue";
import PageHeader from "@/v2/components/shared/PageHeader.vue";
import PlatformIcon from "@/v2/components/shared/PlatformIcon.vue";
import { useBreakpoint } from "@/v2/composables/useBreakpoint";
import { useCan } from "@/v2/composables/useCan";

// playable: the library has the ROM a hack needs (exact or likely match).
// owned: the library has the game in any version, including the wrong one.
// missing: the library doesn't have the game at all.
type Show = "all" | "playable" | "owned" | "missing";
type Sort = "rating" | "favs" | "downloads" | "updated" | "title";
type View = "grid" | "list";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const canRefresh = useCan("rom.upload");
const { xs } = useBreakpoint();

const data = ref<RomshelfListResponse | null>(lastList);
const loading = ref(!lastList);
const refreshing = ref(false);
const loadError = ref<string | null>(null);

function q(name: string): string {
  const v = route.query[name];
  return typeof v === "string" ? v : "";
}
function setQuery(patch: Record<string, string | undefined>) {
  const query = { ...route.query, ...patch };
  for (const k of Object.keys(query)) if (!query[k]) delete query[k];
  void router.replace({ query });
}

// "Import saves" opens over the page; `?import=saves` links straight to it.
const importSaves = computed({
  get: () => q("import") === "saves",
  set: (v: boolean) => setQuery({ import: v ? "saves" : undefined }),
});

const search = computed({
  get: () => q("q"),
  set: (v: string) => setQuery({ q: v || undefined }),
});
const consoleId = computed({
  get: () => q("console"),
  set: (v: string) => setQuery({ console: v || undefined }),
});
const hackType = computed({
  get: () => q("type") || null,
  set: (v: string | null) => setQuery({ type: v ?? undefined }),
});
const show = computed<Show>({
  get: () => (q("show") as Show) || "all",
  set: (v) => setQuery({ show: v === "all" ? undefined : v }),
});
const savedOnly = computed({
  get: () => q("saved") === "1",
  set: (v: boolean) => setQuery({ saved: v ? "1" : undefined }),
});
const sort = computed<Sort>({
  get: () => (q("sort") as Sort) || "rating",
  set: (v) => setQuery({ sort: v === "rating" ? undefined : v }),
});
const view = computed<View>({
  get: () => (q("view") as View) || "grid",
  set: (v) => setQuery({ view: v === "grid" ? undefined : v }),
});

// While RomM checks archived ROMs for headers, matches can still improve, so
// the list is fetched again until the check is done.
const HASH_POLL_MS = 5000;
let pollTimer: ReturnType<typeof setTimeout> | undefined;

async function load(force = false) {
  clearTimeout(pollTimer);
  loadError.value = null;
  try {
    if (force) {
      refreshing.value = true;
      await romshelfApi.refreshIndex();
    }
    data.value = lastList = (await romshelfApi.getHacks()).data;
    rememberHacks(data.value.hacks);
    if (data.value.status.hashing) pollTimer = setTimeout(() => load(), HASH_POLL_MS);
  } catch (err) {
    if (!data.value) loadError.value = (err as Error).message;
  } finally {
    loading.value = false;
    refreshing.value = false;
  }
}
onMounted(() => load());
onBeforeUnmount(() => clearTimeout(pollTimer));

const hacks = computed(() => data.value?.hacks ?? []);
function consoleName(id: string) {
  return data.value?.consoles[id]?.name ?? id.toUpperCase();
}
const title = computed(() =>
  t("romshelf.title-console", {
    console: consoleId.value ? consoleName(consoleId.value) : t("romshelf.all-consoles"),
  }),
);
const consoles = computed(() => {
  const counts = new Map<string, { total: number; owned: number }>();
  for (const h of hacks.value) {
    const c = counts.get(h.console) ?? { total: 0, owned: 0 };
    c.total++;
    if (h.match !== "none") c.owned++;
    counts.set(h.console, c);
  }
  return [...counts.entries()]
    .map(([id, c]) => ({
      id,
      ...c,
      name: consoleName(id),
      slug: ROMSHELF_CONSOLE_SLUGS[id] ?? id,
    }))
    .sort((a, b) => b.owned - a.owned || b.total - a.total);
});
const types = computed(() =>
  [...new Set(hacks.value.map((h) => h.type))].sort().map((v) => ({
    title: v,
    value: v,
  })),
);

const PLAYABLE: RomshelfMatchLevel[] = ["exact", "likely"];
function norm(s: string) {
  return s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, " ");
}
// Saved narrows everything else, including the Show counts.
const pool = computed(() =>
  savedOnly.value ? hacks.value.filter((h) => h.saved) : hacks.value,
);
const savedCount = computed(() => hacks.value.filter((h) => h.saved).length);
const filtered = computed(() => {
  const needle = norm(search.value).trim();
  const list = pool.value.filter((h) => {
    if (consoleId.value && h.console !== consoleId.value) return false;
    if (hackType.value && h.type !== hackType.value) return false;
    if (show.value === "playable" && !PLAYABLE.includes(h.match)) return false;
    if (show.value === "owned" && h.match === "none") return false;
    if (show.value === "missing" && h.match !== "none") return false;
    if (needle) {
      const hay = norm(
        [h.title, h.baseGame, h.authors.join(" "), h.genre ?? "", ...(h.tags ?? [])].join(" "),
      );
      if (!needle.split(" ").every((w) => hay.includes(w))) return false;
    }
    return true;
  });
  const by: Record<Sort, (a: RomshelfHackSummary, b: RomshelfHackSummary) => number> = {
    rating: (a, b) =>
      rankScore(b.rating) - rankScore(a.rating) || (b.favs ?? -1) - (a.favs ?? -1),
    favs: (a, b) =>
      (b.favs ?? -1) - (a.favs ?? -1) || (b.downloads ?? -1) - (a.downloads ?? -1),
    downloads: (a, b) => (b.downloads ?? -1) - (a.downloads ?? -1),
    updated: (a, b) =>
      (b.updated ?? b.released ?? "").localeCompare(a.updated ?? a.released ?? ""),
    title: (a, b) => a.title.localeCompare(b.title),
  };
  return [...list].sort(by[sort.value]);
});

// Thousands of cards at once make the page slow to appear and scroll, so
// they are added a page at a time as the end of the list comes into view.
const PAGE_SIZE = 120;
const shownCount = ref(PAGE_SIZE);
const shown = computed(() => filtered.value.slice(0, shownCount.value));
watch([search, consoleId, hackType, show, savedOnly, sort], () => (shownCount.value = PAGE_SIZE));
const moreSentinel = ref<HTMLElement | null>(null);
useIntersectionObserver(
  moreSentinel,
  ([entry]) => {
    if (entry?.isIntersecting && shownCount.value < filtered.value.length)
      shownCount.value += PAGE_SIZE;
  },
  { rootMargin: "800px" },
);

const counts = computed(() => ({
  playable: pool.value.filter((h) => PLAYABLE.includes(h.match)).length,
  owned: pool.value.filter((h) => h.match !== "none").length,
  missing: pool.value.filter((h) => h.match === "none").length,
}));
// "120 of 4473" while any filter is on, so the total is never mistaken for
// the size of the whole index.
const headerCount = computed(() =>
  filtered.value.length === hacks.value.length
    ? filtered.value.length
    : t("romshelf.count-of", { n: filtered.value.length, total: hacks.value.length }),
);

const showItems = computed(() => [
  { id: "all" as Show, label: t("romshelf.show-all") },
  {
    id: "playable" as Show,
    label: t("romshelf.show-playable"),
    badge: counts.value.playable,
  },
  { id: "owned" as Show, label: t("romshelf.show-owned"), badge: counts.value.owned },
  {
    id: "missing" as Show,
    label: t("romshelf.show-missing"),
    badge: counts.value.missing,
  },
]);
const showSelectItems = computed(() =>
  showItems.value.map((i) => ({
    title: i.badge == null ? i.label : `${i.label} (${i.badge})`,
    value: i.id,
  })),
);
const sortItems = computed(() => [
  { title: t("romshelf.sort-rating"), value: "rating" },
  { title: t("romshelf.sort-favs"), value: "favs" },
  { title: t("romshelf.sort-downloads"), value: "downloads" },
  { title: t("romshelf.sort-updated"), value: "updated" },
  { title: t("romshelf.sort-title"), value: "title" },
]);
const viewItems = computed(() => [
  { id: "grid" as View, icon: "mdi-view-grid-outline", ariaLabel: t("romshelf.view-grid") },
  { id: "list" as View, icon: "mdi-view-list-outline", ariaLabel: t("romshelf.view-list") },
]);

function matchIcon(level: RomshelfMatchLevel) {
  if (PLAYABLE.includes(level))
    return { icon: "mdi-check-circle", cls: "ok", label: t("romshelf.library-exact") };
  if (level === "wrong_version")
    return { icon: "mdi-alert", cls: "warn", label: t("romshelf.library-wrong") };
  return null;
}
function colorsOf(id: string) {
  return data.value?.consoles[id]?.colors;
}
function hackLink(h: RomshelfHackSummary) {
  return { name: ROUTES.ROMSHELF_HACK, params: { hack: h.id } };
}
const indexDate = computed(() => {
  const g = data.value?.status.generated;
  return g ? new Date(g).toLocaleDateString() : null;
});
</script>

<template>
  <IndexShell :list-mode="false" :list-label="t('romshelf.title')">
    <template #header>
      <PageHeader
        :title="title"
        :count="headerCount"
        class="r-v2-rs-idx__header"
      >
        <div class="r-v2-rs-idx__status">
          <span v-if="data?.status.hashing" class="r-v2-rs-idx__hashing">
            <RIcon icon="mdi-progress-clock" size="14" />
            {{ t("romshelf.hashing", { n: data.status.hashing }) }}
          </span>
          <RollButton v-if="data" :hacks="data.hacks" :consoles="data.consoles" />
          <span v-if="indexDate">{{ t("romshelf.index-date", { date: indexDate }) }}</span>
          <RBtn
            variant="text"
            size="small"
            prepend-icon="mdi-content-save-move"
            @click="importSaves = true"
          >
            {{ t("romshelf.saves-open") }}
          </RBtn>
          <RBtn
            v-if="canRefresh"
            variant="text"
            size="small"
            prepend-icon="mdi-refresh"
            :loading="refreshing"
            @click="load(true)"
          >
            {{ t("romshelf.refresh") }}
          </RBtn>
        </div>
      </PageHeader>
      <p class="r-v2-rs-idx__lede">{{ t("romshelf.lede") }}</p>
      <SaveImportDialog v-model="importSaves" />
      <RAlert
        v-if="data?.status.error"
        type="warning"
        density="compact"
        :text="t('romshelf.index-error', { error: data.status.error })"
      />
    </template>

    <template #toolbar>
      <div class="r-v2-rs-idx__toolbar">
        <RTextField
          v-model="search"
          :placeholder="t('romshelf.search')"
          prepend-inner-icon="mdi-magnify"
          density="compact"
          hide-details
          clearable
          class="r-v2-rs-idx__search"
        />
        <RSelect
          v-if="xs"
          v-model="show"
          :items="showSelectItems"
          density="compact"
          hide-details
          class="r-v2-rs-idx__select r-v2-rs-idx__select--show"
        />
        <RSliderBtnGroup v-else v-model="show" :items="showItems" variant="tab" />
        <RBtn
          size="small"
          variant="outlined"
          :active="savedOnly"
          :prepend-icon="savedOnly ? 'mdi-bookmark' : 'mdi-bookmark-outline'"
          @click="savedOnly = !savedOnly"
        >
          {{ t("romshelf.show-saved", { n: savedCount }) }}
        </RBtn>
        <RSelect
          v-model="hackType"
          :items="types"
          :placeholder="t('romshelf.all-types')"
          density="compact"
          hide-details
          clearable
          class="r-v2-rs-idx__select"
        />
        <RSelect
          v-model="sort"
          :items="sortItems"
          density="compact"
          hide-details
          class="r-v2-rs-idx__select"
        />
        <RSliderBtnGroup v-model="view" :items="viewItems" variant="segmented" />
      </div>
      <div class="r-v2-rs-idx__consoles">
        <RChip
          size="small"
          :variant="consoleId ? 'outlined' : 'flat'"
          :color="consoleId ? undefined : 'primary'"
          @click="consoleId = ''"
        >
          {{ t("romshelf.all-consoles") }}
        </RChip>
        <RChip
          v-for="c in consoles"
          :key="c.id"
          size="small"
          :variant="consoleId === c.id ? 'flat' : 'outlined'"
          :color="consoleId === c.id ? 'primary' : undefined"
          @click="consoleId = consoleId === c.id ? '' : c.id"
        >
          <PlatformIcon :slug="c.slug" :size="16" :show-tooltip="false" />
          <span class="r-v2-rs-idx__chip-label">{{ c.name }}</span>
          <span class="r-v2-rs-idx__chip-count">{{ c.total }}</span>
        </RChip>
      </div>
    </template>

    <div v-if="loading" class="r-v2-rs-idx__grid">
      <RSkeletonBlock v-for="i in 12" :key="i" height="220" />
    </div>
    <RAlert v-else-if="loadError" type="error" :text="loadError" />
    <REmptyState
      v-else-if="savedOnly && !savedCount"
      icon="mdi-bookmark-outline"
      :title="t('romshelf.no-saved')"
      :hint="t('romshelf.no-saved-hint')"
    />
    <REmptyState
      v-else-if="!filtered.length"
      icon="mdi-bookshelf"
      :title="t('romshelf.no-results')"
      :hint="t('romshelf.no-results-hint')"
    />

    <div v-else-if="view === 'grid'" class="r-v2-rs-idx__grid">
      <div v-for="h in shown" :key="h.id" class="r-v2-rs-card-wrap">
      <RouterLink :to="hackLink(h)" class="r-v2-rs-card">
        <div class="r-v2-rs-card__art">
          <HackThumb :hack="h" :colors="colorsOf(h.console)" />
          <span
            v-if="matchIcon(h.match)"
            class="r-v2-rs-card__match"
            :class="`r-v2-rs-card__match--${matchIcon(h.match)!.cls}`"
            :title="matchIcon(h.match)!.label"
          >
            <RIcon :icon="matchIcon(h.match)!.icon" size="18" />
            <span class="r-v2-rs-sr">{{ matchIcon(h.match)!.label }}</span>
          </span>
          <span v-if="h.installs.length" class="r-v2-rs-card__installed">
            <RIcon icon="mdi-bookshelf" size="14" />
            {{ t("romshelf.installed-tag") }}
          </span>
        </div>
        <div class="r-v2-rs-card__body">
          <span class="r-v2-rs-card__title">{{ h.title }}</span>
          <span class="r-v2-rs-card__sub">{{ h.baseGame }}</span>
          <span class="r-v2-rs-card__meta">
            <PlatformIcon
              :slug="ROMSHELF_CONSOLE_SLUGS[h.console] ?? h.console"
              :size="14"
              :show-tooltip="false"
            />
            {{ h.type }}
            <template v-if="h.version"> · {{ h.version }}</template>
            <span v-if="h.rating?.score" class="r-v2-rs-card__rating">
              <RIcon icon="mdi-star" size="12" /> {{ h.rating.score.toFixed(1) }}
            </span>
            <span
              v-else-if="h.favs"
              class="r-v2-rs-card__rating"
              :title="t('romshelf.favs')"
            >
              <RIcon icon="mdi-heart" size="12" /> {{ h.favs }}
            </span>
          </span>
        </div>
      </RouterLink>
      <SaveHackButton
        v-model="h.saved"
        :hack-id="h.id"
        compact
        overlay
        class="r-v2-rs-card__save"
        :class="{ 'r-v2-rs-card__save--on': h.saved }"
      />
      </div>
    </div>

    <ul v-else class="r-v2-rs-idx__list">
      <li v-for="h in shown" :key="h.id" class="r-v2-rs-line-wrap">
        <RouterLink :to="hackLink(h)" class="r-v2-rs-line">
          <HackThumb :hack="h" :colors="colorsOf(h.console)" :size="44" />
          <span class="r-v2-rs-line__text">
            <span class="r-v2-rs-card__title">{{ h.title }}</span>
            <span class="r-v2-rs-card__sub">
              {{ h.baseGame }} · {{ h.authors.join(", ") }}
            </span>
          </span>
          <span class="r-v2-rs-line__cols">
            <PlatformIcon
              :slug="ROMSHELF_CONSOLE_SLUGS[h.console] ?? h.console"
              :size="18"
              :show-tooltip="false"
            />
            <span class="r-v2-rs-line__type">{{ h.type }}</span>
            <span v-if="h.rating?.score" class="r-v2-rs-card__rating">
              <RIcon icon="mdi-star" size="12" /> {{ h.rating.score.toFixed(1) }}
            </span>
            <span
              v-else-if="h.favs"
              class="r-v2-rs-card__rating"
              :title="t('romshelf.favs')"
            >
              <RIcon icon="mdi-heart" size="12" /> {{ h.favs }}
            </span>
            <span
              class="r-v2-rs-card__match r-v2-rs-card__match--inline"
              :class="matchIcon(h.match) ? `r-v2-rs-card__match--${matchIcon(h.match)!.cls}` : ''"
              :title="matchIcon(h.match)?.label"
            >
              <RIcon v-if="matchIcon(h.match)" :icon="matchIcon(h.match)!.icon" size="18" />
            </span>
          </span>
        </RouterLink>
        <SaveHackButton v-model="h.saved" :hack-id="h.id" compact />
      </li>
    </ul>
    <div
      v-if="!loading && shown.length < filtered.length"
      ref="moreSentinel"
      class="r-v2-rs-idx__more"
      aria-hidden="true"
    />
  </IndexShell>
</template>

<style scoped>
.r-v2-rs-idx__header {
  flex-wrap: wrap;
  row-gap: var(--r-space-2);
}
.r-v2-rs-idx__status {
  margin-left: auto;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--r-space-2);
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-sm);
}
.r-v2-rs-idx__hashing {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.r-v2-rs-idx__more {
  height: 1px;
}
.r-v2-rs-idx__lede {
  margin: 0 0 var(--r-space-4);
  color: var(--r-color-fg-secondary);
  max-width: 70ch;
}
.r-v2-rs-idx__toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--r-space-3);
  padding: var(--r-space-2) 0;
}
.r-v2-rs-idx__search {
  flex: 1 1 240px;
  max-width: 420px;
}
.r-v2-rs-idx__select {
  width: 170px;
}
.r-v2-rs-idx__consoles {
  display: flex;
  flex-wrap: wrap;
  gap: var(--r-space-2);
  padding-bottom: var(--r-space-3);
}
.r-v2-rs-idx__chip-label {
  margin-left: var(--r-space-1);
}
.r-v2-rs-idx__chip-count {
  margin-left: var(--r-space-1);
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-xs);
}
.r-v2-rs-idx__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: var(--r-space-4);
  padding-top: var(--r-space-3);
}
.r-v2-rs-card-wrap {
  position: relative;
  display: flex;
  flex-direction: column;
}
.r-v2-rs-card-wrap > .r-v2-rs-card {
  flex: 1;
}
/* The bookmark shows on hover or focus, and always once saved. */
.r-v2-rs-card__save {
  position: absolute;
  top: var(--r-space-2);
  left: var(--r-space-2);
  opacity: 0;
  transition: opacity var(--r-motion-fast) var(--r-motion-ease-out);
}
.r-v2-rs-card-wrap:hover .r-v2-rs-card__save,
.r-v2-rs-card-wrap:focus-within .r-v2-rs-card__save,
.r-v2-rs-card__save--on {
  opacity: 1;
}
/* Touch-sized screens have no hover, so the bookmark always shows. */
html[data-bp~="sm-and-down"] .r-v2-rs-card__save {
  opacity: 1;
}
.r-v2-rs-card {
  display: flex;
  flex-direction: column;
  border-radius: var(--r-radius-card);
  overflow: hidden;
  background: var(--r-color-surface);
  border: 1px solid var(--r-color-border);
  color: inherit;
  text-decoration: none;
  transition:
    transform var(--r-motion-fast) var(--r-motion-ease-out),
    border-color var(--r-motion-fast) var(--r-motion-ease-out);
}
.r-v2-rs-card:hover,
.r-v2-rs-card:focus-visible {
  transform: translateY(-2px);
  border-color: var(--r-color-border-strong);
}
.r-v2-rs-card__art {
  position: relative;
  aspect-ratio: 4 / 3;
  overflow: hidden;
  font-size: 28px;
}
.r-v2-rs-card__art > :first-child {
  position: absolute;
  inset: 0;
}
.r-v2-rs-card__match {
  position: absolute;
  top: var(--r-space-2);
  right: var(--r-space-2);
  display: inline-flex;
  padding: 3px;
  border-radius: var(--r-radius-full);
  background: var(--r-color-overlay-scrim-strong);
}
.r-v2-rs-card__match--inline {
  position: static;
  background: none;
  min-width: 24px;
}
.r-v2-rs-card__match--ok {
  color: var(--r-color-success);
}
.r-v2-rs-card__match--warn {
  color: var(--r-color-warning);
}
.r-v2-rs-card__installed {
  position: absolute;
  left: var(--r-space-2);
  bottom: var(--r-space-2);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: var(--r-radius-pill);
  background: var(--r-color-overlay-scrim-strong);
  color: var(--r-color-overlay-fg);
  font-size: var(--r-font-size-xs);
}
.r-v2-rs-card__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--r-space-3);
  min-width: 0;
}
.r-v2-rs-card__title {
  font-weight: var(--r-font-weight-semibold);
  color: var(--r-color-fg-heading);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.r-v2-rs-card__sub {
  color: var(--r-color-fg-secondary);
  font-size: var(--r-font-size-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.r-v2-rs-card__meta {
  display: flex;
  align-items: center;
  gap: var(--r-space-1);
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-xs);
}
.r-v2-rs-card__rating {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  color: var(--r-color-romm-gold);
}
.r-v2-rs-idx__list {
  list-style: none;
  margin: 0;
  padding: var(--r-space-3) 0 0;
  display: flex;
  flex-direction: column;
  gap: var(--r-space-1);
}
.r-v2-rs-line-wrap {
  display: flex;
  align-items: center;
  gap: var(--r-space-1);
}
.r-v2-rs-line-wrap > .r-v2-rs-line {
  flex: 1;
  min-width: 0;
}
.r-v2-rs-line {
  display: flex;
  align-items: center;
  gap: var(--r-space-3);
  padding: var(--r-space-2) var(--r-space-3);
  border-radius: var(--r-radius-md);
  color: inherit;
  text-decoration: none;
}
.r-v2-rs-line:hover,
.r-v2-rs-line:focus-visible {
  background: var(--r-color-surface-hover);
}
.r-v2-rs-line__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}
.r-v2-rs-line__cols {
  display: flex;
  align-items: center;
  gap: var(--r-space-3);
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-sm);
}
.r-v2-rs-line__type {
  width: 110px;
}
.r-v2-rs-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
/* Phones: the actions sit on their own row under the title, and the toolbar
   controls share the width instead of running off the right edge. */
html[data-bp~="xs"] .r-v2-rs-idx__header {
  align-items: center;
}
html[data-bp~="xs"] .r-v2-rs-idx__header :deep(.page-header__title) {
  font-size: var(--r-font-size-2xl);
}
html[data-bp~="xs"] .r-v2-rs-idx__status {
  margin-left: 0;
  width: 100%;
}
html[data-bp~="xs"] .r-v2-rs-idx__toolbar {
  gap: var(--r-space-2);
}
html[data-bp~="xs"] .r-v2-rs-idx__search {
  flex-basis: 100%;
  max-width: none;
}
html[data-bp~="xs"] .r-v2-rs-idx__select {
  flex: 1 1 140px;
  width: auto;
  min-width: 0;
}
html[data-bp~="xs"] .r-v2-rs-idx__select--show {
  flex-basis: 100%;
}
html[data-bp~="xs"] .r-v2-rs-line__cols {
  gap: var(--r-space-2);
}
html[data-bp~="sm-and-down"] .r-v2-rs-line__type {
  display: none;
}
html[data-bp~="sm-and-down"] .r-v2-rs-idx__grid {
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: var(--r-space-3);
}
</style>
