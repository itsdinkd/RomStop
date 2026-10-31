<script setup lang="ts">
// Renders a RomStop description or changelog entry. The index stores them as
// Markdown (converted from the sources' BBCode); this builds elements from the
// parsed tree, so no HTML from the index is ever injected. Long descriptions
// start collapsed behind a "More" toggle.
import { computed, h, ref, type VNode } from "vue";
import {
  blockLength,
  parseInline,
  parseMarkdown,
  type Block,
  type Inline,
} from "@/v2/components/Romshelf/markdown";

const props = withDefaults(
  defineProps<{
    text: string;
    /** One line of inline Markdown, e.g. a changelog entry. */
    inline?: boolean;
    /** Collapse past roughly this many characters; 0 never collapses. */
    collapseAt?: number;
  }>(),
  { inline: false, collapseAt: 1400 },
);

const blocks = computed(() => (props.inline ? [] : parseMarkdown(props.text)));
const long = computed(
  () =>
    !props.inline &&
    props.collapseAt > 0 &&
    blockLength(blocks.value) > props.collapseAt * 1.3,
);
const open = ref(false);

function inlines(nodes: Inline[]): (VNode | string)[] {
  return nodes.map((n) => {
    switch (n.t) {
      case "text":
        return n.v;
      case "br":
        return h("br");
      case "code":
        return h("code", n.v);
      case "strong":
        return h("strong", inlines(n.c));
      case "em":
        return h("em", inlines(n.c));
      case "del":
        return h("del", inlines(n.c));
      case "link":
        return h(
          "a",
          { href: n.href, target: "_blank", rel: "noreferrer nofollow" },
          inlines(n.c),
        );
    }
  });
}

function block(b: Block): VNode {
  switch (b.t) {
    case "p":
      return h("p", inlines(b.c));
    case "h":
      return h("h4", inlines(b.c));
    case "ul":
    case "ol":
      return h(
        b.t,
        b.items.map((it) => h("li", inlines(it))),
      );
    case "quote":
      return h("blockquote", inlines(b.c));
    case "pre":
      return h("pre", b.v);
    case "hr":
      return h("hr");
  }
}

const Body = () =>
  props.inline
    ? h("span", inlines(parseInline(props.text)))
    : h(
        "div",
        {
          class: [
            "r-v2-rs-md__body",
            { "r-v2-rs-md__body--collapsed": long.value && !open.value },
          ],
        },
        blocks.value.map(block),
      );
</script>

<template>
  <Body v-if="inline" />
  <div v-else class="r-v2-rs-md">
    <Body />
    <button
      v-if="long"
      type="button"
      class="r-v2-rs-md__more"
      :aria-expanded="open"
      @click="open = !open"
    >
      {{ open ? "Less ▴" : "More ▾" }}
    </button>
  </div>
</template>

<style scoped>
.r-v2-rs-md__body {
  display: flex;
  flex-direction: column;
  gap: var(--r-space-3);
  color: var(--r-color-fg);
  line-height: var(--r-line-height-relaxed);
  overflow-wrap: anywhere;
}
.r-v2-rs-md__body--collapsed {
  max-height: 22rem;
  overflow: hidden;
  mask-image: linear-gradient(to bottom, black 75%, transparent);
}
.r-v2-rs-md__body :deep(p),
.r-v2-rs-md__body :deep(ul),
.r-v2-rs-md__body :deep(ol),
.r-v2-rs-md__body :deep(blockquote),
.r-v2-rs-md__body :deep(pre),
.r-v2-rs-md__body :deep(h4) {
  margin: 0;
}
.r-v2-rs-md__body :deep(h4) {
  font-weight: 700;
  color: var(--r-color-fg-heading);
}
.r-v2-rs-md__body :deep(ul),
.r-v2-rs-md__body :deep(ol) {
  padding-left: var(--r-space-5);
}
.r-v2-rs-md__body :deep(ul) {
  list-style: disc;
}
.r-v2-rs-md__body :deep(ol) {
  list-style: decimal;
}
.r-v2-rs-md__body :deep(blockquote) {
  border-left: 2px solid var(--r-color-border);
  padding-left: var(--r-space-3);
  color: var(--r-color-fg-secondary);
}
.r-v2-rs-md__body :deep(pre) {
  overflow-x: auto;
  white-space: pre;
  font-size: 0.85em;
}
:deep(a) {
  color: var(--r-color-accent, inherit);
  text-decoration: underline;
}
.r-v2-rs-md__more {
  margin-top: var(--r-space-2);
  background: none;
  border: 0;
  padding: 0;
  font-weight: 600;
  color: var(--r-color-accent, var(--r-color-fg));
  cursor: pointer;
}
</style>
