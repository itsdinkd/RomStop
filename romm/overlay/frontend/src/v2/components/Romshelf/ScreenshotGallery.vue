<script setup lang="ts">
// ScreenshotGallery: a hack's screenshots as thumbnails; one opens the
// fullscreen carousel.
import { RCarousel } from "@v2/lib";
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";

const props = defineProps<{ screenshots: string[]; title: string }>();

const { t } = useI18n();
const index = ref(0);
const open = ref(false);
// Screenshots that failed to load (hotlinks can break) are left out.
const failed = ref(new Set<string>());
const shown = computed(() =>
  props.screenshots.filter((s) => !failed.value.has(s)),
);

function drop(src: string) {
  failed.value = new Set([...failed.value, src]);
}

function show(i: number) {
  index.value = i;
  open.value = true;
}
</script>

<template>
  <div class="r-v2-rs-gallery">
    <button
      v-for="(src, i) in shown"
      :key="src"
      type="button"
      class="r-v2-rs-gallery__thumb"
      :aria-label="t('romshelf.open-screenshot', { n: i + 1 })"
      @click="show(i)"
    >
      <img
        :src="src"
        :alt="`${title} ${i + 1}`"
        loading="lazy"
        @error="drop(src)"
      />
    </button>
  </div>

  <RCarousel
    v-if="open"
    v-model="index"
    :items="shown"
    fullscreen
    show-thumbnails
    :aria-label="t('romshelf.screenshots')"
    @close="open = false"
  >
    <template #default="{ item, index: i }">
      <img :src="item" :alt="`${title} ${i + 1}`" />
    </template>
    <template #thumbnail="{ item, index: i }">
      <img :src="item" :alt="`${title} ${i + 1}`" />
    </template>
  </RCarousel>
</template>

<style scoped>
.r-v2-rs-gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));
  gap: var(--r-space-2);
}
.r-v2-rs-gallery__thumb {
  padding: 0;
  border: 1px solid var(--r-color-border);
  border-radius: var(--r-radius-md);
  overflow: hidden;
  background: var(--r-color-surface);
  aspect-ratio: 4 / 3;
  cursor: zoom-in;
  transition: transform var(--r-motion-fast) var(--r-motion-ease-out);
}
.r-v2-rs-gallery__thumb:hover,
.r-v2-rs-gallery__thumb:focus-visible {
  transform: scale(1.03);
}
.r-v2-rs-gallery__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  image-rendering: pixelated;
  display: block;
}
</style>
