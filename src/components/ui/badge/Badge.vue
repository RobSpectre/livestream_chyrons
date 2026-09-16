<script setup lang="ts">
import type { HTMLAttributes } from "vue"
import { cn } from "@/lib/utils"
import { badgeVariants, type BadgeVariants } from "./variants"

const props = defineProps<{
  class?: HTMLAttributes["class"]
  variant?: BadgeVariants["variant"]
  /** When set the badge is tinted with the agent accent instead of the theme variant. */
  accent?: string
}>()
</script>

<template>
  <span
    :class="cn(badgeVariants({ variant: props.variant }), props.class)"
    :style="
      props.accent
        ? {
            borderColor: `${props.accent}80`,
            color: props.accent,
            backgroundColor: `${props.accent}1a`,
          }
        : undefined
    "
  >
    <slot />
  </span>
</template>
