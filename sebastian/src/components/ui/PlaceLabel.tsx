import { PLACES } from "@/lib/symphony/places"
import type { Place } from "@/lib/symphony/types"
import { Icon } from "./Icon"
import styles from "./ui.module.css"

/** A place always shows with its icon and full name. Colored once per screen, grey in lists. */
export function PlaceLabel({ place, quiet, iconOnly }: { place: Place | null; quiet?: boolean; iconOnly?: boolean }) {
  if (!place) return <span className={styles.placeQuiet}>Unknown place</span>
  const p = PLACES[place]
  if (iconOnly) {
    return <Icon name={p.icon} size={16} className={styles.placeQuiet} aria-label={`Ran ${p.short}`} />
  }
  return (
    <span className={quiet ? `${styles.place} ${styles.placeQuiet}` : styles.place} style={quiet ? undefined : { color: p.ink }}>
      <Icon name={p.icon} size={16} />
      {p.name}
    </span>
  )
}
