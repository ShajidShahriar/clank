/** Class strings shared by the components, so a button looks like a button everywhere. Colors are roles from index.css, which already follow light and dark. */

const BASE = 'press inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-control font-medium disabled:cursor-not-allowed disabled:opacity-40'

/** The one main action of a place: filled with the accent color. */
export const BUTTON_PRIMARY = `${BASE} bg-accent px-3 py-1 text-[13px] text-white hover:brightness-110`

/** Every other button: a quiet fill, no border. */
export const BUTTON = `${BASE} bg-fill px-3 py-1 text-[13px] text-label hover:bg-fill-strong`

/** A button that sits inside a colored banner. */
export const BUTTON_SMALL = `${BASE} bg-fill px-2.5 py-0.5 text-xs text-label hover:bg-fill-strong`

/** An icon-only button. */
export const ICON_BUTTON = 'press inline-flex shrink-0 items-center justify-center rounded-control p-1.5 text-label-2 hover:bg-fill hover:text-label disabled:opacity-40'

/** Text fields. */
export const INPUT = 'w-full rounded-control bg-fill px-2.5 py-1.5 text-[13px] text-label placeholder-label-3 outline-none ring-0 transition-shadow focus:shadow-[0_0_0_3px_var(--accent-soft)] focus:ring-1 focus:ring-accent disabled:opacity-50 selectable'

/** The small section titles of the sidebar and the forms. */
export const SECTION_TITLE = 'px-2.5 pb-1 pt-2 text-xs font-medium text-label-3'
