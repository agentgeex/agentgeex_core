import * as React from "react";

/**
 * components/ui/portal-container.tsx — Radix overlay mount point (2026-11-13)
 *
 * Why this is needed:
 * Feng 2026-11-13 08:07: 'after going fullscreen this dropdown no longer works, no dropdown works at all,
 * other controls are fine.'
 *
 * Root cause: Radix's `Portal` mounts dropdown content into `document.body` by default.
 * But **native browser fullscreen** (Fullscreen API) renders only 'the fullscreen element and its subtree' —
 * body sits outside the fullscreen element, so even though the dropdown is rendered and its DOM exists,
 * everything outside the element is clipped → the symptom is 'clicks do nothing'.
 *
 * Solution: give overlays an injectable mount point. The fullscreen container pushes its own DOM node in,
 * and the overlay renders **inside the fullscreen element**, visible and clickable.
 *
 * Default null = keep the old behavior (mount to body), so non-fullscreen setups are entirely unchanged.
 */
const PortalContainerContext = React.createContext<HTMLElement | null>(null);

/** Where the overlay should mount: returning null means 'mount to body by default'. */
export function usePortalContainer(): HTMLElement | null {
  return React.useContext(PortalContainerContext);
}

export function PortalContainerProvider({
  container,
  children,
}: {
  container: HTMLElement | null;
  children: React.ReactNode;
}) {
  return (
    <PortalContainerContext.Provider value={container}>{children}</PortalContainerContext.Provider>
  );
}
