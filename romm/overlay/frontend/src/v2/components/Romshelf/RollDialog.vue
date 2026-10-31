<script setup lang="ts">
// RollDialog: "Roll a Rom Hack". A case-opening reel of hack thumbnails
// spins past a marker, slows down and stops on a random hack; a second
// later the hack's page opens (with ?rolled=1, which shows the re-roll
// button there).
import { RDialog } from "@v2/lib";
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { ROUTES } from "@/plugins/routeNames";
import type {
  RomshelfConsole,
  RomshelfHackSummary,
} from "@/services/api/romshelf";
import HackThumb from "@/v2/components/Romshelf/HackThumb.vue";
import { pickRandom, rarity } from "@/v2/components/Romshelf/roll";

const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    /** Hacks the roll may land on (see roll.ts `rollable`). */
    pool: RomshelfHackSummary[];
    consoles?: Record<string, RomshelfConsole> | undefined;
  }>(),
  { consoles: undefined },
);
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();

const { t } = useI18n();
const router = useRouter();

const TILE = 148; // tile width + gap, px; keep in sync with the CSS below
const STRIP = 64;
const WINNER_AT = 54;
const reduceMotion =
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const SPIN_MS = reduceMotion ? 900 : 6500;
const LAND_PAUSE_MS = 1000;

const strip = ref<RomshelfHackSummary[]>([]);
const winner = ref<RomshelfHackSummary | null>(null);
const offset = ref(0);
const spinning = ref(false);
const landed = ref(false);
const viewport = ref<HTMLElement | null>(null);
let timers: ReturnType<typeof setTimeout>[] = [];

function clearTimers() {
  timers.forEach(clearTimeout);
  timers = [];
}

async function roll() {
  clearTimers();
  landed.value = false;
  spinning.value = false;
  offset.value = 0;
  const won = pickRandom(props.pool);
  winner.value = won;
  // Fillers are any eligible hack; repeats are fine on a reel, the winner is fixed.
  strip.value = Array.from({ length: STRIP }, (_, i) =>
    i === WINNER_AT ? won : pickRandom(props.pool),
  );
  await nextTick();
  // Let the strip paint at its start before it starts moving.
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const width = viewport.value?.clientWidth ?? 600;
  // Stop somewhere inside the winning tile, not always dead centre, like a real case.
  const jitter = (Math.random() - 0.5) * (TILE - 40);
  spinning.value = true;
  offset.value = WINNER_AT * TILE + TILE / 2 - width / 2 + jitter;
  timers.push(
    setTimeout(() => {
      spinning.value = false;
      landed.value = true;
      timers.push(setTimeout(() => void open(won), LAND_PAUSE_MS));
    }, SPIN_MS),
  );
}

async function open(h: RomshelfHackSummary) {
  await router.push({
    name: ROUTES.ROMSHELF_HACK,
    params: { hack: h.id },
    query: { rolled: "1" },
  });
  emit("update:modelValue", false);
}

function close() {
  clearTimers();
  emit("update:modelValue", false);
}

watch(
  () => props.modelValue,
  (open) => {
    if (open && props.pool.length) void roll();
    else clearTimers();
  },
  { immediate: true },
);
onBeforeUnmount(clearTimers);

const stripStyle = computed(() => ({
  transform: `translateX(${-offset.value}px)`,
  transition: spinning.value
    ? `transform ${SPIN_MS}ms cubic-bezier(0.08, 0.6, 0.12, 1)`
    : "none",
}));
</script>

<template>
  <RDialog
    :model-value="modelValue"
    icon="mdi-dice-multiple"
    :width="760"
    :persistent="!landed"
    :fullscreen-on-mobile="false"
    @update:model-value="(v: boolean) => (v ? null : close())"
    @close="close"
  >
    <template #header>
      <span>{{ t("romshelf.roll-title") }}</span>
    </template>

    <template #content>
      <div class="r-v2-rs-roll">
        <div ref="viewport" class="r-v2-rs-roll__viewport" :class="{ 'is-landed': landed }">
          <div class="r-v2-rs-roll__strip" :style="stripStyle">
            <div
              v-for="(h, i) in strip"
              :key="i"
              class="r-v2-rs-roll__tile"
              :class="[
                `r-v2-rs-roll__tile--${rarity(h)}`,
                { 'is-winner': landed && i === WINNER_AT },
              ]"
            >
              <div class="r-v2-rs-roll__art">
                <HackThumb :hack="h" :colors="consoles?.[h.console]?.colors" />
              </div>
              <span class="r-v2-rs-roll__name">{{ h.title }}</span>
            </div>
          </div>
          <div class="r-v2-rs-roll__marker" aria-hidden="true" />
          <div class="r-v2-rs-roll__fade" aria-hidden="true" />
        </div>
        <p class="r-v2-rs-roll__status" aria-live="polite">
          <template v-if="landed && winner">
            {{ t("romshelf.roll-landed", { title: winner.title }) }}
          </template>
          <template v-else>{{ t("romshelf.roll-spinning") }}</template>
        </p>
      </div>
    </template>
  </RDialog>
</template>

<style scoped>
.r-v2-rs-roll {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-3);
}
.r-v2-rs-roll__viewport {
  position: relative;
  overflow: hidden;
  height: 172px;
  border-radius: var(--r-radius-md);
  background:
    radial-gradient(ellipse at center, color-mix(in srgb, white 12%, transparent), transparent 70%),
    color-mix(in srgb, black 45%, transparent);
  border: 1px solid color-mix(in srgb, white 8%, transparent);
}
.r-v2-rs-roll__strip {
  display: flex;
  gap: 8px;
  padding: 12px 0;
  will-change: transform;
}
.r-v2-rs-roll__tile {
  --rarity: royalblue;
  flex: none;
  width: 140px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 6px 8px;
  border-radius: var(--r-radius-sm);
  background: linear-gradient(180deg, color-mix(in srgb, white 6%, transparent), color-mix(in srgb, white 2%, transparent));
  border-bottom: 4px solid var(--rarity);
  box-shadow: inset 0 -24px 30px -24px var(--rarity);
  transition:
    transform 300ms ease,
    box-shadow 300ms ease,
    opacity 300ms ease;
}
.r-v2-rs-roll__tile--blue { --rarity: royalblue; }
.r-v2-rs-roll__tile--purple { --rarity: blueviolet; }
.r-v2-rs-roll__tile--pink { --rarity: mediumorchid; }
.r-v2-rs-roll__tile--red { --rarity: crimson; }
.r-v2-rs-roll__tile--gold { --rarity: goldenrod; }
.r-v2-rs-roll__art {
  height: 96px;
  border-radius: var(--r-radius-sm);
  overflow: hidden;
}
.r-v2-rs-roll__name {
  overflow: hidden;
  color: var(--r-color-fg-heading);
  font-size: var(--r-font-size-sm);
  font-weight: var(--r-font-weight-bold);
  white-space: nowrap;
  text-overflow: ellipsis;
}
.r-v2-rs-roll__viewport.is-landed .r-v2-rs-roll__tile:not(.is-winner) {
  opacity: 0.35;
}
.r-v2-rs-roll__tile.is-winner {
  transform: scale(1.06);
  box-shadow:
    0 0 0 2px var(--rarity),
    0 0 28px var(--rarity),
    inset 0 -24px 30px -24px var(--rarity);
}
.r-v2-rs-roll__marker {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  width: 3px;
  transform: translateX(-50%);
  background: gold;
  box-shadow: 0 0 12px gold;
}
.r-v2-rs-roll__marker::before,
.r-v2-rs-roll__marker::after {
  content: "";
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  border: 9px solid transparent;
}
.r-v2-rs-roll__marker::before {
  top: 0;
  border-top-color: gold;
}
.r-v2-rs-roll__marker::after {
  bottom: 0;
  border-bottom-color: gold;
}
.r-v2-rs-roll__fade {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, black 75%, transparent),
    transparent 22%,
    transparent 78%,
    color-mix(in srgb, black 75%, transparent)
  );
}
.r-v2-rs-roll__status {
  margin: 0;
  min-height: 1.5em;
  text-align: center;
  color: var(--r-color-fg-secondary);
  font-weight: var(--r-font-weight-bold);
}
@media (prefers-reduced-motion: reduce) {
  .r-v2-rs-roll__tile {
    transition: none;
  }
}
</style>
