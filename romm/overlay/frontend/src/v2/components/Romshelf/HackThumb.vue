<script setup lang="ts">
// HackThumb: a hack's first screenshot, or its console's colours and short
// name when it has none (the same fallback the RomStop site uses).
import { computed, ref, watch } from "vue";

const props = withDefaults(
  defineProps<{
    hack: { title: string; console: string; screenshots?: string[]; cover?: string | null };
    colors?: [string, string] | undefined;
    label?: string | undefined;
    size?: number | undefined;
  }>(),
  { colors: undefined, label: undefined, size: undefined },
);

const broken = ref(false);
const src = computed(() =>
  broken.value ? undefined : (props.hack.cover ?? props.hack.screenshots?.[0]),
);
watch(
  () => props.hack,
  () => (broken.value = false),
);
const style = computed(() => {
  return {
    ...(props.colors
      ? {
          background: `linear-gradient(135deg, ${props.colors[0]}, ${props.colors[1]})`,
        }
      : {}),
    ...(props.size
      ? { width: `${props.size}px`, height: `${props.size}px` }
      : {}),
  };
});
</script>

<template>
  <span class="r-v2-rs-thumb" :class="{ 'r-v2-rs-thumb--fill': !size }" :style="style">
    <img v-if="src" :src="src" alt="" loading="lazy" @error="broken = true" />
    <span v-else class="r-v2-rs-thumb__label">{{
      label ?? hack.console.toUpperCase()
    }}</span>
  </span>
</template>

<style scoped>
.r-v2-rs-thumb {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  overflow: hidden;
  border-radius: var(--r-radius-sm);
  background: var(--r-color-cover-placeholder);
}
.r-v2-rs-thumb--fill {
  width: 100%;
  height: 100%;
}
.r-v2-rs-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  image-rendering: pixelated;
}
.r-v2-rs-thumb__label {
  color: var(--r-color-overlay-fg);
  font-family: var(--r-font-family-display);
  font-weight: var(--r-font-weight-bold);
  font-size: 0.7em;
  letter-spacing: 0.04em;
  text-shadow: 0 1px 2px var(--r-color-title-shadow);
}
</style>
