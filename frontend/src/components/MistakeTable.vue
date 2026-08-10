<script setup lang="ts">
import { computed, ref } from "vue";
import type { MoveAnnotation, OpeningName } from "../api";
import { familyColor, FLAW_COLORS } from "../families";

export interface Mistake {
  mark: MoveAnnotation;
  move: number | null;
  line: string;
  opening: OpeningName | null;
  family: string | null;
  familyName: string | null;
}

const props = defineProps<{
  mistakes: Mistake[];
  measured: number;
  pinned: string | null;
  slots: Map<string, number>;
}>();

const emit = defineEmits<{ select: [digest: string]; close: [] }>();

// Worst single move against worst habit. A move giving up 0.9 across 175 games
// costs more than one giving up 1.5 across 47, and only the second gets a glyph.
type Order = "loss" | "cost";
const order = ref<Order>("cost");

const totalCost = (m: Mistake) => m.mark.loss_cp * m.mark.games;

const rows = computed(() =>
  [...props.mistakes].sort((a, b) =>
    order.value === "loss"
      ? b.mark.loss_cp - a.mark.loss_cp || b.mark.games - a.mark.games
      : totalCost(b) - totalCost(a),
  ),
);

const spilled = computed(() =>
  Math.round(rows.value.reduce((sum, m) => sum + totalCost(m), 0) / 100),
);

const pawns = (cp: number) => (cp / 100).toFixed(2);

// Named opening first, the coloured family second, and only "unnamed" when the
// line runs off the book entirely.
const where = (m: Mistake) => m.opening?.name ?? m.familyName ?? "Unnamed line";

// Lichess names run to "French Defense: Horwitz Attack, Papa-Ticulat Gambit",
// which no column holds. The half before the colon is the answer to which
// opening, so it is the half that survives the ellipsis.
const split = (m: Mistake): [string, string | null] => {
  const full = where(m);
  const at = full.indexOf(":");
  return at < 0 ? [full, null] : [full.slice(0, at), full.slice(at + 1).trim()];
};

const detail = (m: Mistake) =>
  [where(m), m.opening?.eco, m.line].filter(Boolean).join(" · ");
</script>

<template>
  <section class="mistakes material">
    <header>
      <span class="eyebrow">
        {{ mistakes.length }} flagged of {{ measured }} measured
      </span>
      <button type="button" class="shut" aria-label="Close" @click="emit('close')">
        &times;
      </button>
    </header>

    <div class="sort" role="group" aria-label="Order">
      <button
        type="button"
        :class="{ on: order === 'cost' }"
        :aria-pressed="order === 'cost'"
        @click="order = 'cost'"
      >
        Costliest
      </button>
      <button
        type="button"
        :class="{ on: order === 'loss' }"
        :aria-pressed="order === 'loss'"
        @click="order = 'loss'"
      >
        Worst move
      </button>
    </div>

    <div class="scroll">
      <table>
        <thead>
          <tr>
            <th class="glyph"><span class="sr">Quality</span></th>
            <th class="played">Played</th>
            <th class="swatch-cell"><span class="sr">Family</span></th>
            <th class="opening">Opening</th>
            <th class="instead">Instead of</th>
            <th class="num right">Loss</th>
            <th class="num right">Games</th>
            <th class="num right">Cost</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.mark.child"
            :class="{ here: row.mark.child === pinned }"
            :title="detail(row)"
            tabindex="0"
            role="button"
            :aria-label="`${row.mark.san}, ${detail(row)}`"
            @click="emit('select', row.mark.child)"
            @keydown.enter.prevent="emit('select', row.mark.child)"
            @keydown.space.prevent="emit('select', row.mark.child)"
          >
            <td class="glyph" :style="{ color: FLAW_COLORS[row.mark.quality] }">
              {{ row.mark.quality }}
            </td>
            <td class="played">
              <span v-if="row.move" class="ply num">{{ row.move }}.</span>
              {{ row.mark.san }}
            </td>
            <td class="swatch-cell">
              <span
                class="swatch"
                :class="{ hollow: !row.family }"
                :style="{ background: familyColor(row.family, slots) }"
              />
            </td>
            <td class="opening">
              {{ split(row)[0] }}
              <span v-if="split(row)[1]" class="variation">
                &middot; {{ split(row)[1] }}
              </span>
            </td>
            <td class="instead">{{ row.mark.best_san }}</td>
            <td class="num right loss">{{ pawns(row.mark.loss_cp) }}</td>
            <td class="num right">{{ row.mark.games }}</td>
            <td class="num right cost">{{ Math.round(totalCost(row) / 100) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <p class="key">
      Click a move to walk the line to it. Cost is the loss across every game you
      played it, {{ spilled }} pawns in total.
    </p>
  </section>
</template>

<style scoped>
.mistakes {
  width: 600px;
  padding: 12px 13px;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  gap: 8px;
  max-height: 46vh;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.shut {
  font-size: 15px;
  line-height: 1;
  color: var(--faint);
}
.shut:hover {
  color: var(--text);
}

.sort {
  display: flex;
  gap: 2px;
  padding: 2px;
  background: var(--sunken);
  border-radius: var(--r-control);
}
.sort button {
  flex: 1;
  padding: 3px 0;
  border-radius: 5px;
  font-size: 10.5px;
  color: var(--muted);
  transition: background 0.15s var(--ease), color 0.15s var(--ease);
}
.sort button.on {
  background: var(--raised);
  color: var(--text);
}

.scroll {
  min-height: 0;
  overflow-y: auto;
}
table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
}
thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 0 5px 4px;
  background: var(--panel);
  backdrop-filter: blur(12px);
  font-size: 9.5px;
  font-weight: 500;
  text-align: left;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--faint);
}
tbody tr {
  cursor: pointer;
  transition: background 0.15s var(--ease);
}
tbody tr:hover {
  background: var(--raised);
}
tbody tr.here {
  background: var(--accent-sunk);
}
tbody tr:focus-visible {
  outline: 1px solid var(--accent);
  outline-offset: -1px;
}
td {
  padding: 3px 5px;
  font-size: 11.5px;
  color: var(--text);
  white-space: nowrap;
}
.right {
  text-align: right;
}
.glyph {
  width: 24px;
  font-weight: 600;
}
.played {
  width: 66px;
}
.played .ply {
  font-size: 10px;
  color: var(--faint);
}
.swatch-cell {
  width: 15px;
  padding-right: 0;
}
.swatch {
  display: block;
  width: 8px;
  height: 8px;
  border-radius: 3px;
}
.swatch.hollow {
  background: transparent !important;
  border: 1px solid var(--faint);
}
/* Ellipsised rather than wrapped: the full name is on the row's title, and a
   wrapping cell would make every row a different height. */
.opening {
  overflow: hidden;
  text-overflow: ellipsis;
}
.variation {
  color: var(--faint);
}
.instead {
  width: 60px;
  color: var(--muted);
}
.right {
  width: 44px;
}
.loss {
  color: var(--amber);
}
.cost {
  color: var(--accent-bright);
}
.sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}

.key {
  margin: 2px 0 0;
  padding-top: 8px;
  border-top: 1px solid var(--line);
  font-size: 10.5px;
  line-height: 1.45;
  color: var(--faint);
}
</style>
