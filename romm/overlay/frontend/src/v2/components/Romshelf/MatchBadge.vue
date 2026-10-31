<script setup lang="ts">
// MatchBadge: how well a ROM fits a hack. On a ROM page the question is
// "is this the right ROM" (green / red); in the browse view it is "do I
// own a ROM this hack can patch" (green check / caution).
import { RTag } from "@v2/lib";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { RomshelfMatchLevel } from "@/services/api/romshelf";

const props = withDefaults(
  defineProps<{
    level: RomshelfMatchLevel;
    context?: "rom" | "library";
    size?: "x-small" | "small" | "default";
  }>(),
  { context: "rom", size: "small" },
);

const { t } = useI18n();

const view = computed(() => {
  switch (props.level) {
    case "exact":
      return {
        tone: "success" as const,
        icon: "mdi-check-circle",
        text:
          props.context === "rom"
            ? t("romshelf.match-exact")
            : t("romshelf.library-exact"),
      };
    case "likely":
      return {
        tone: "success" as const,
        icon: "mdi-check-circle-outline",
        text:
          props.context === "rom"
            ? t("romshelf.match-likely")
            : t("romshelf.library-likely"),
      };
    case "wrong_version":
      return props.context === "rom"
        ? {
            tone: "danger" as const,
            icon: "mdi-close-circle",
            text: t("romshelf.match-wrong"),
          }
        : {
            tone: "warning" as const,
            icon: "mdi-alert",
            text: t("romshelf.library-wrong"),
          };
    default:
      return {
        tone: "neutral" as const,
        icon: "mdi-minus-circle-outline",
        text: t("romshelf.library-none"),
      };
  }
});
</script>

<template>
  <RTag
    :tone="view.tone"
    :prepend-icon="view.icon"
    :text="view.text"
    :size="size"
  />
</template>
