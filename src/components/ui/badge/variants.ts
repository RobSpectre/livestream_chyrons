import type { VariantProps } from "class-variance-authority"
import { cva } from "class-variance-authority"

export const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2.5 py-0.5 font-semibold transition-colors focus:outline-none",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground shadow",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        destructive: "border-transparent bg-destructive text-destructive-foreground shadow",
        outline: "text-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
)

export type BadgeVariants = VariantProps<typeof badgeVariants>
