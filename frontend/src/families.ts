import type { OpeningFamily, RepertoireGraph } from "./api";

// Only the first three clear the colourblind and normal-vision floors when every
// pair can sit side by side. Slots four and five are legible to most people and
// always carry their name in the legend, which is what makes them usable.
export const SLOT_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#9085e9"] as const;
export const SAFE_SLOTS = 3;
export const MAX_PICKS = SLOT_COLORS.length;
export const NEUTRAL = "#6f6f74";

export function defaultPicks(graph: RepertoireGraph): string[] {
  return graph.families
    .filter((f) => f.slot >= 0 && f.slot < SAFE_SLOTS)
    .sort((a, b) => a.slot - b.slot)
    .map((f) => f.key);
}

// Colour follows the family, not its rank: a pick keeps its slot while it is held.
export function slotsFor(picks: string[]): Map<string, number> {
  const slots = new Map<string, number>();
  picks.slice(0, MAX_PICKS).forEach((key, index) => slots.set(key, index));
  return slots;
}

export function familyColor(family: string | null, slots: Map<string, number>): string {
  if (!family) return NEUTRAL;
  const slot = slots.get(family);
  return slot === undefined ? NEUTRAL : SLOT_COLORS[slot];
}

export function ecoRange(family: OpeningFamily): string | null {
  if (!family.eco_low) return null;
  if (!family.eco_high || family.eco_high === family.eco_low) return family.eco_low;
  return `${family.eco_low}-${family.eco_high}`;
}

export function sharpnessLabel(sharpness: number): string {
  if (sharpness < 0.45) return "quiet";
  if (sharpness < 0.6) return "balanced";
  if (sharpness < 0.75) return "sharp";
  return "very sharp";
}
