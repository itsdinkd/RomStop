<script setup lang="ts">
// MarioSpecifics: SMW Central's Length, Difficulty and Type ("Mario Type")
// for hacks RomStop took from SMW Central. Hovering, focusing or tapping
// Difficulty shows the whole difficulty scale with what each step means.
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import type { RomshelfHack } from "@/services/api/romshelf";
import { difficultyLevel, difficultyScale } from "@/v2/components/Romshelf/mario";

const props = defineProps<{ hack: RomshelfHack }>();
const { t } = useI18n();

const open = ref(false);
const mario = computed(() => props.hack.mario);
const level = computed(() => difficultyLevel(props.hack));
const scale = computed(() => difficultyScale(props.hack));
const tipId = computed(() => `rs-difficulty-${props.hack.id}`);
</script>

<template>
  <section v-if="mario" class="r-v2-rs-mario">
    <span class="r-v2-rs-mario__title">{{ t("romshelf.mario-specifics") }}</span>
    <div class="r-v2-rs-mario__cells">
      <div class="r-v2-rs-mario__cell">
        <span class="r-v2-rs-mario__label">{{ t("romshelf.mario-length") }}</span>
        <strong>{{ mario.length ?? "—" }}</strong>
      </div>
      <div class="r-v2-rs-mario__cell">
        <button
          type="button"
          class="r-v2-rs-mario__label r-v2-rs-mario__hint"
          :aria-expanded="open"
          :aria-describedby="tipId"
          @mouseenter="open = true"
          @mouseleave="open = false"
          @focus="open = true"
          @blur="open = false"
          @click="open = true"
        >
          {{ t("romshelf.mario-difficulty") }} ⓘ
        </button>
        <span
          v-if="mario.difficulty"
          class="r-v2-rs-mario__badge"
          :style="level ? { background: level.color } : undefined"
          >{{ mario.difficulty }}</span
        >
        <strong v-else>—</strong>
      </div>
      <div class="r-v2-rs-mario__cell">
        <span class="r-v2-rs-mario__label">{{ t("romshelf.mario-type") }}</span>
        <span v-if="mario.types?.length" class="r-v2-rs-mario__types">
          <span v-for="ty in mario.types" :key="ty" class="r-v2-rs-mario__badge r-v2-rs-mario__badge--type">{{ ty }}</span>
        </span>
        <strong v-else>—</strong>
      </div>
    </div>
    <div
      :id="tipId"
      role="tooltip"
      class="r-v2-rs-mario__tip"
      :class="{ 'r-v2-rs-mario__tip--open': open }"
    >
      <div
        v-for="l in scale"
        :key="l.label"
        class="r-v2-rs-mario__step"
        :class="{ 'r-v2-rs-mario__step--current': l.label === level?.label }"
      >
        <span class="r-v2-rs-mario__step-badge">
          <span class="r-v2-rs-mario__badge" :style="{ background: l.color }">{{ l.label }}</span>
        </span>
        <span>{{ l.description }}</span>
      </div>
      <p class="r-v2-rs-mario__credit">
        {{ t(hack.console === "n64" ? "romshelf.mario-scale-sm64" : "romshelf.mario-scale-smw") }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.r-v2-rs-mario {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--r-space-3);
  padding: var(--r-space-3) var(--r-space-4);
  border-radius: var(--r-radius-md);
  border: 1px solid var(--r-color-border);
  background: var(--r-color-surface);
}
.r-v2-rs-mario__title {
  font-weight: var(--r-font-weight-bold);
}
.r-v2-rs-mario__cells {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.r-v2-rs-mario__cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 0 var(--r-space-2);
  text-align: center;
}
.r-v2-rs-mario__cell + .r-v2-rs-mario__cell {
  border-left: 1px solid var(--r-color-border);
}
.r-v2-rs-mario__label {
  font-size: var(--r-font-size-xs);
  font-weight: var(--r-font-weight-bold);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--r-color-fg-muted);
}
/* The dotted underline slowly marches to say "hover me"; it turns solid on hover or focus. */
@keyframes r-v2-rs-mario-march {
  to {
    background-position: 8px 100%;
  }
}
.r-v2-rs-mario__hint {
  position: relative;
  padding: 0 0 2px;
  border: 0;
  cursor: help;
  font-family: inherit;
  background-color: transparent;
  background-image: linear-gradient(90deg, currentColor 50%, transparent 50%);
  background-size: 4px 1px;
  background-repeat: repeat-x;
  background-position: 0 100%;
}
.r-v2-rs-mario__hint::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  background: var(--r-color-brand-primary, currentColor);
  transform: scaleX(0);
  transform-origin: left;
  transition: transform 200ms ease-out;
}
.r-v2-rs-mario__hint:hover,
.r-v2-rs-mario__hint:focus-visible {
  color: var(--r-color-fg);
  outline: none;
}
.r-v2-rs-mario__hint:hover::after,
.r-v2-rs-mario__hint:focus-visible::after {
  transform: scaleX(1);
}
@media (prefers-reduced-motion: no-preference) {
  .r-v2-rs-mario__hint {
    animation: r-v2-rs-mario-march 1.2s linear infinite;
  }
}
.r-v2-rs-mario__types {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 4px;
}
.r-v2-rs-mario__badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: var(--r-font-size-xs);
  font-weight: var(--r-font-weight-bold);
  color: #fff;
  background: var(--r-color-border);
  white-space: nowrap;
}
.r-v2-rs-mario__badge--type {
  background: #4f6a8f;
}
.r-v2-rs-mario__tip {
  position: absolute;
  left: 0;
  right: 0;
  top: calc(100% + 8px);
  z-index: 20;
  max-width: 560px;
  margin: 0 auto;
  padding: var(--r-space-3);
  border-radius: var(--r-radius-md);
  border: 1px solid var(--r-color-panel-border);
  background: var(--r-color-panel);
  box-shadow: 0 12px 32px rgb(0 0 0 / 0.35);
  display: flex;
  flex-direction: column;
  gap: 6px;
  visibility: hidden;
  opacity: 0;
  transform: translateY(-4px);
  transition:
    opacity 150ms,
    transform 150ms,
    visibility 150ms;
}
.r-v2-rs-mario__tip--open {
  visibility: visible;
  opacity: 1;
  transform: none;
}
.r-v2-rs-mario__step {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: var(--r-space-3);
  align-items: center;
  padding: 2px 6px;
  border-radius: var(--r-radius-md);
  font-size: var(--r-font-size-sm);
  line-height: 1.3;
}
.r-v2-rs-mario__step--current {
  background: color-mix(in srgb, var(--r-color-fg) 8%, transparent);
}
.r-v2-rs-mario__step-badge {
  text-align: right;
}
.r-v2-rs-mario__credit {
  margin: 4px 6px 0;
  font-size: var(--r-font-size-xs);
  color: var(--r-color-fg-muted);
}
</style>
