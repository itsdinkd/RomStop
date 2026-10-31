<script setup lang="ts">
// HackDetails: everything RomStop knows about one hack, as on the RomStop
// site: the ROM it needs (checked against `rom` when given), checksums,
// version and changelog, description, screenshots and credits. The install
// controls come in through the default slot.
import { RBtn, RIcon, RTag } from "@v2/lib";
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import type { RouteLocationRaw } from "vue-router";
import type {
  RomshelfHack,
  RomshelfLibraryRom,
  RomshelfMatch,
  RomshelfMatchLevel,
} from "@/services/api/romshelf";
import ChangelogDialog from "@/v2/components/Romshelf/ChangelogDialog.vue";
import MarioSpecifics from "@/v2/components/Romshelf/MarioSpecifics.vue";
import RichText from "@/v2/components/Romshelf/RichText.vue";
import ScreenshotGallery from "@/v2/components/Romshelf/ScreenshotGallery.vue";
import { emulatorName } from "@/v2/components/Romshelf/compat";

const props = withDefaults(
  defineProps<{
    hack: RomshelfHack;
    needed: string;
    match?: RomshelfMatch | null;
    rom?: RomshelfLibraryRom | null;
    versionLevels?: Record<string, RomshelfMatchLevel>;
    /** Only the needed ROM and checksums. */
    compact?: boolean;
    /** The original game's page in this library, when it is there. */
    romLink?: RouteLocationRaw | null;
  }>(),
  { match: null, rom: null, versionLevels: () => ({}), compact: false, romLink: null },
);

defineSlots<{ default?: () => unknown }>();

const { t } = useI18n();
const changelogOpen = ref(false);

const compat = computed(() => props.hack.compatibility ?? null);
const compatRows = computed(() => {
  const c = compat.value;
  if (!c) return [];
  const row = (label: string, ids: string[] | undefined, tone: "success" | "warning" | "danger") =>
    ids?.length ? [{ label, tone, names: ids.map((id) => emulatorName(props.hack, id)) }] : [];
  return [
    ...row(t(c.only ? "romshelf.compat-only" : "romshelf.compat-works"), c.works, "success"),
    ...row(t("romshelf.compat-issues"), c.issues, "warning"),
    ...row(t("romshelf.compat-broken"), c.broken, "danger"),
  ];
});

const base = computed(() => props.hack.baseRom ?? {});
const currentVersion = computed(
  () => props.hack.versions[0]?.version ?? props.hack.version,
);
const currentDate = computed(
  () => props.hack.versions[0]?.date ?? props.hack.updated,
);

const file = computed(() => {
  if (!props.rom) return null;
  return (
    props.rom.files.find((f) => f.id === props.match?.file_id) ??
    props.rom.files[0] ??
    null
  );
});

const needState = computed(() => {
  const level = props.match?.level;
  if (!props.rom || !level || level === "none") return "neutral";
  return level === "wrong_version" ? "wrong" : "right";
});

const needTitle = computed(() => {
  switch (props.match?.level) {
    case "exact":
      return t("romshelf.need-exact");
    case "likely":
      return t("romshelf.need-likely");
    case "wrong_version":
      return t("romshelf.need-wrong", { rom: props.needed });
    default:
      return t("romshelf.need-plain", { rom: props.needed });
  }
});

const needDetail = computed(() => {
  const m = props.match;
  if (!m || !props.rom) return null;
  if (m.level === "exact")
    return m.strip_header
      ? t("romshelf.reason-checksum-header")
      : t("romshelf.reason-checksum");
  if (m.level === "likely")
    return m.reason === "file name"
      ? t("romshelf.reason-file-name")
      : t("romshelf.reason-title");
  if (m.level === "wrong_version") {
    const key =
      m.reason === "region"
        ? "romshelf.reason-region"
        : m.reason === "revision"
          ? "romshelf.reason-revision"
          : m.reason === "header"
            ? "romshelf.reason-missing-header"
            : "romshelf.reason-checksum-differs";
    return t(key, { yours: props.rom.fs_name, rom: props.needed });
  }
  return null;
});

type HashRow = {
  label: string;
  wanted: string;
  yours: string | null;
  same: boolean;
};
const hashRows = computed<HashRow[]>(() => {
  const rows: HashRow[] = [];
  const f = file.value;
  // The checksum the patch expects, then the No-Intro one without a header:
  // a file matching either is the right dump.
  const wants = (...xs: (string | undefined)[]) =>
    xs.flatMap((x) => (x ? [x.toLowerCase()] : []));
  const pairs: [string, string[], (string | null)[]][] = [
    [
      "CRC32",
      wants(base.value.crc32, base.value.headerlessCrc32),
      [f?.crc32 ?? null, f?.headerless_crc32 ?? null],
    ],
    [
      "SHA-1",
      wants(base.value.sha1, base.value.headerlessSha1),
      [f?.sha1 ?? null, f?.headerless_sha1 ?? null],
    ],
  ];
  for (const [label, wanted, yours] of pairs) {
    const w = wanted.find((x) => yours.includes(x)) ?? wanted[0];
    if (!w) continue;
    const same = yours.includes(w);
    rows.push({
      label,
      wanted: w,
      yours: same ? w : (yours[0] ?? null),
      same,
    });
  }
  return rows;
});

const ratingText = computed(() => {
  const r = props.hack.rating;
  if (!r || (!r.score && !r.count)) return null;
  return t("romshelf.rating-value", {
    score: r.score.toFixed(1),
    count: r.count,
  });
});
</script>

<template>
  <div class="r-v2-rs-hack">
    <section
      class="r-v2-rs-hack__need"
      :class="`r-v2-rs-hack__need--${needState}`"
    >
      <RIcon
        :icon="
          needState === 'right'
            ? 'mdi-check-circle'
            : needState === 'wrong'
              ? 'mdi-close-circle'
              : 'mdi-chip'
        "
        size="22"
        class="r-v2-rs-hack__need-icon"
      />
      <div class="r-v2-rs-hack__need-text">
        <span class="r-v2-rs-hack__label">{{ t("romshelf.rom-needed") }}</span>
        <strong class="r-v2-rs-hack__need-title">{{ needTitle }}</strong>
        <span v-if="needState !== 'wrong'" class="r-v2-rs-hack__need-rom">
          <RouterLink
            v-if="romLink"
            :to="romLink"
            class="r-v2-rs-hack__need-link"
            :title="t('romshelf.open-base-rom')"
          >
            {{ needed }}
          </RouterLink>
          <template v-else>{{ needed }}</template>
          <template v-if="base.revision"> · {{ base.revision }}</template>
          <template v-if="base.header"> · {{ base.header }}</template>
        </span>
        <span v-if="needDetail" class="r-v2-rs-hack__need-detail">
          {{ needDetail }}
        </span>
        <span v-if="base.notes" class="r-v2-rs-hack__need-detail">
          {{ base.notes }}
        </span>
      </div>
    </section>

    <section v-if="compat" class="r-v2-rs-hack__compat">
      <span class="r-v2-rs-hack__label">{{ t("romshelf.compat") }}</span>
      <div v-for="row in compatRows" :key="row.label" class="r-v2-rs-hack__hash">
        <span class="r-v2-rs-hack__muted">{{ row.label }}</span>
        <RTag v-for="n in row.names" :key="n" :text="n" size="small" :tone="row.tone" />
      </div>
      <p v-if="compat.hardware" class="r-v2-rs-hack__muted">
        {{ t(`romshelf.compat-hardware-${compat.hardware}`) }}
      </p>
      <details v-if="compat.notes?.length" class="r-v2-rs-hack__muted">
        <summary>{{ t("romshelf.compat-notes") }}</summary>
        <p v-for="n in compat.notes" :key="n"><em>“{{ n }}”</em></p>
      </details>
    </section>

    <section v-if="hashRows.length" class="r-v2-rs-hack__hashes">
      <span class="r-v2-rs-hack__label">{{ t("romshelf.checksum") }}</span>
      <div v-for="row in hashRows" :key="row.label" class="r-v2-rs-hack__hash">
        <RTag :label="row.label" :text="row.wanted" mono size="small" />
        <RTag
          v-if="rom && row.yours"
          :label="t('romshelf.yours')"
          :text="row.yours"
          mono
          size="small"
          :tone="row.same ? 'success' : 'danger'"
        />
      </div>
    </section>
    <p v-else class="r-v2-rs-hack__muted">{{ t("romshelf.no-checksum") }}</p>

    <template v-if="!compact">
    <section class="r-v2-rs-hack__version">
      <span class="r-v2-rs-hack__label">{{ t("romshelf.version") }}</span>
      <RBtn
        variant="outlined"
        size="small"
        prepend-icon="mdi-history"
        @click="changelogOpen = true"
      >
        {{
          currentVersion
            ? t("romshelf.version-button", {
                version: currentVersion,
                n: hack.versions.length,
              })
            : t("romshelf.changelog")
        }}
      </RBtn>
      <span v-if="currentDate" class="r-v2-rs-hack__muted">
        {{ t("romshelf.updated-on", { date: currentDate }) }}
      </span>
    </section>

    <section class="r-v2-rs-hack__desc">
      <RichText :text="hack.description || hack.summary" />
      <ul v-if="hack.features?.length" class="r-v2-rs-hack__features">
        <li v-for="f in hack.features" :key="f">{{ f }}</li>
      </ul>
    </section>

    <ScreenshotGallery
      v-if="hack.screenshots.length"
      :screenshots="hack.screenshots"
      :title="hack.title"
    />

    <MarioSpecifics :hack="hack" />

    <dl class="r-v2-rs-hack__facts">
      <div>
        <dt>{{ t("romshelf.authors") }}</dt>
        <dd>{{ hack.authors.join(", ") || "—" }}</dd>
      </div>
      <div>
        <dt>{{ t("romshelf.type") }}</dt>
        <dd>
          {{ hack.type }}<template v-if="hack.genre && !hack.mario?.types"> · {{ hack.genre }}</template>
        </dd>
      </div>
      <div v-if="hack.released">
        <dt>{{ t("romshelf.released") }}</dt>
        <dd>{{ hack.released }}</dd>
      </div>
      <div v-if="ratingText">
        <dt>{{ t("romshelf.rating") }}</dt>
        <dd>
          {{ ratingText }}
          <span
            v-for="r in hack.ratings"
            :key="r.source"
            class="r-v2-rs-hack__muted"
          >
            · {{ r.source }} {{ r.display ?? r.score.toFixed(1) }}
          </span>
        </dd>
      </div>
      <div v-if="hack.favs != null">
        <dt>{{ t("romshelf.favs") }}</dt>
        <dd>
          <a
            v-if="hack.favsUrl"
            :href="hack.favsUrl"
            target="_blank"
            rel="noopener noreferrer"
          >
            {{ t("romshelf.favs-value", { n: hack.favs.toLocaleString() }) }}
          </a>
          <template v-else>
            {{ t("romshelf.favs-value", { n: hack.favs.toLocaleString() }) }}
          </template>
        </dd>
      </div>
      <div v-if="hack.language">
        <dt>{{ t("romshelf.language") }}</dt>
        <dd>{{ hack.language }}</dd>
      </div>
      <div v-if="hack.patchFormats?.length || hack.patchFormat">
        <dt>{{ t("romshelf.patch-format") }}</dt>
        <dd>{{ hack.patchFormats?.join(", ") || hack.patchFormat }}</dd>
      </div>
      <div v-if="hack.links.length || hack.pageUrl">
        <dt>{{ t("romshelf.links") }}</dt>
        <dd class="r-v2-rs-hack__links">
          <a
            v-for="l in hack.links"
            :key="l.url"
            :href="l.url"
            target="_blank"
            rel="noopener"
            >{{ l.label }}</a
          >
          <a v-if="hack.pageUrl" :href="hack.pageUrl" target="_blank" rel="noopener">
            RomStop
          </a>
        </dd>
      </div>
    </dl>
    </template>

    <slot />

    <ChangelogDialog
      v-model="changelogOpen"
      :title="hack.title"
      :versions="hack.versions"
      :current="currentVersion"
      :levels="rom ? versionLevels : undefined"
    />
  </div>
</template>

<style scoped>
.r-v2-rs-hack {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-4);
}
.r-v2-rs-hack__label {
  font-size: var(--r-font-size-xs);
  font-weight: var(--r-font-weight-bold);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--r-color-fg-muted);
}
.r-v2-rs-hack__muted {
  color: var(--r-color-fg-muted);
  font-size: var(--r-font-size-sm);
  margin: 0;
}
.r-v2-rs-hack__need {
  display: flex;
  gap: var(--r-space-3);
  align-items: flex-start;
  padding: var(--r-space-3) var(--r-space-4);
  border-radius: var(--r-radius-md);
  border: 1px solid var(--r-color-border);
  background: var(--r-color-surface);
}
.r-v2-rs-hack__need--right {
  border-color: color-mix(in srgb, var(--r-color-success) 55%, transparent);
  background: color-mix(in srgb, var(--r-color-success) 12%, transparent);
}
.r-v2-rs-hack__need--right .r-v2-rs-hack__need-icon {
  color: var(--r-color-success);
}
.r-v2-rs-hack__need--wrong {
  border-color: color-mix(in srgb, var(--r-color-danger) 55%, transparent);
  background: color-mix(in srgb, var(--r-color-danger) 12%, transparent);
}
.r-v2-rs-hack__need--wrong .r-v2-rs-hack__need-icon {
  color: var(--r-color-danger);
}
.r-v2-rs-hack__need-link {
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.r-v2-rs-hack__need-link:hover {
  color: var(--r-color-brand-primary, var(--r-color-fg-heading));
}
.r-v2-rs-hack__need-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.r-v2-rs-hack__need-title {
  color: var(--r-color-fg-heading);
}
.r-v2-rs-hack__need-rom,
.r-v2-rs-hack__need-detail {
  color: var(--r-color-fg-secondary);
  font-size: var(--r-font-size-sm);
  overflow-wrap: anywhere;
}
.r-v2-rs-hack__compat,
.r-v2-rs-hack__hashes {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-2);
}
.r-v2-rs-hack__hash {
  display: flex;
  flex-wrap: wrap;
  gap: var(--r-space-2);
}
.r-v2-rs-hack__version {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--r-space-3);
}
.r-v2-rs-hack__features {
  list-style: disc;
  margin: var(--r-space-2) 0 0;
  padding-left: var(--r-space-5);
  color: var(--r-color-fg-secondary);
}
.r-v2-rs-hack__facts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: var(--r-space-3) var(--r-space-5);
  margin: 0;
}
.r-v2-rs-hack__facts dt {
  font-size: var(--r-font-size-xs);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--r-color-fg-muted);
}
.r-v2-rs-hack__facts dd {
  margin: 2px 0 0;
  color: var(--r-color-fg);
}
.r-v2-rs-hack__links {
  display: flex;
  flex-wrap: wrap;
  gap: var(--r-space-3);
}
.r-v2-rs-hack__links a {
  color: var(--r-color-brand-primary);
}
</style>
