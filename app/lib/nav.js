// Page switching. Each navigation renders into a page element of its own, so a view that
// finishes loading after the user has moved on writes into a page that is no longer shown,
// and its buttons can't be pressed.
//
// 13 Oct 2026, measured: after a click the screen was empty for 0.8 to 2.0 seconds, until the new page's reads were
// in; a page opened at the place the one before it had been scrolled to; and the page that was left went on asking
// the node. So a new page says it is loading until its view writes into it, it opens at its top (or where the
// visitor left it, when they come back to it), and the page that is left is told so.

/** What a page shows until its view has written into it: the words the site opens with. */
export const LOADING = '<p class="muted">Loading…</p>';

/**
 * Where a page opens. The place it was left at is kept with its entry in the browser's history (`{ y }`): an entry
 * the visitor comes back to has it, and a page they go on to has none and opens at its top.
 */
export function scrollTarget(state) {
  const y = Number(state?.y);
  return Number.isFinite(y) && y > 0 ? Math.round(y) : 0;
}

/** `mount()` puts a fresh page on screen and returns it. `leave()` is told each time a page is left. */
export function createNavigator(mount, { leave = () => {} } = {}) {
  let current = 0;
  return async function navigate(handler, onError = () => {}) {
    const id = ++current;
    leave();
    const page = mount();
    try {
      await handler(page);
    } catch (err) {
      if (id === current) onError(page, err);
    }
    return id === current;
  };
}

/** Block ranges for an event query, newest first: at most `max` windows of `size` blocks,
 *  from `latest` back to `floor`. `complete` is false when older blocks were left out. */
/** Every page but the terms asks the entry question until it has been answered. */
export function needsGate(hash, passed) {
  return !passed && hash !== "#/terms";
}
