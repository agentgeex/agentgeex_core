import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  // base: every button gets a border uniformly (Feng's acceptance feedback 2026-09-28: every button must have a border)
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-primary/20 bg-primary text-primary-foreground shadow hover:bg-primary/90",
        /* [2026-10-28 15:05 Feng] Buttons are colored by "purpose" — one color per type, recognizable at a glance.
           Use **solid fills** uniformly (outline/transparent is invisible on white pages; reworked twice already).
           Palette only uses existing tokens, no new colors:
             default(blue primary)  primary action: create / save / confirm / submit / send message
             run    (green success) execute: run / rerun / retry
             view   (cyan info)     view: details / open / flow graph
             edit   (orange warning) edit: edit / modify / configure
             back   (purple chart-1) navigation: back / return
             destructive(red)       danger: delete / archive
             secondary(grey)        cancel / close / reset
        */
        run: "border-action-run/40 bg-action-run text-action-run-foreground shadow-sm hover:bg-action-run/90",
        view: "border-action-view/40 bg-action-view text-action-view-foreground shadow-sm hover:bg-action-view/90",
        edit: "border-warning/50 bg-warning text-warning-foreground shadow-sm hover:bg-warning/90",
        back: "border-chart-1/60 bg-chart-1 text-white shadow-sm hover:bg-chart-1/90",
        destructive: "border-destructive/30 bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90",
        outline: "border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground",
        secondary: "border-input bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80",
        ghost: "border-input bg-transparent hover:bg-accent hover:text-accent-foreground",
        link: "border-input bg-transparent text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-md px-3 text-xs",
        lg: "h-10 rounded-md px-8",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
