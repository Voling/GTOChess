import type { OpeningFamily, RepertoireGraph } from "./api";

export const SLOT_COLORS = ["#3987e5", "#d95926", "#199e70"] as const;
export const NEUTRAL = "#6f6f74";
export const HIGHLIGHT = SLOT_COLORS[0];

export function familyColor(family: string | null, slots: Map<string, number>): string {
  if (!family) return NEUTRAL;
  const slot = slots.get(family);
  return slot === undefined ? NEUTRAL : SLOT_COLORS[slot];
}

export function slotIndex(graph: RepertoireGraph): Map<string, number> {
  const slots = new Map<string, number>();
  for (const family of graph.families) {
    if (family.slot >= 0 && family.slot < SLOT_COLORS.length) slots.set(family.key, family.slot);
  }
  return slots;
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
