<script setup lang="ts">
import { computed } from "vue";
import type { OutcomeReport } from "../api";
import { FLAW_COLORS } from "../families";

const props = defineProps<{
  report: OutcomeReport;
  pinned: string | null;
}>();

const emit = defineEmits<{ select: [digest: string]; close: [] }>();

const QUALITY_NAMES: Record<string, string> = {
  "??": "Blunders",
  "?": "Mistakes",
  "?!": "Dubious",
  sound: "Sound",
};

const percent = (score: number) => `${(score * 100).toFixed(1)}%`;
const pawns = (cp: number) => (cp / 100).toFixed(2);

const gap = computed(() => Math.round(props.report.score_gap * 1000) / 10);
const flagged = computed(() =>
  props.report.by_quality.filter((q) => q.quality !== "sound"),
);
const flaggedGames = computed(() =>
  flagged.value.reduce((sum, q) => sum + q.games, 0),
);
const spilled = computed(() =>
  Math.round(props.report.worst.reduce((sum, m) => sum + m.points_lost, 0)),
);
const coverage = computed(() => {
  const { moves_measured, moves_unmeasured } = props.report;
  const total = moves_measured + moves_unmeasured;
  return total ? Math.round((moves_measured / total) * 100) : 0;
});

const record = (w: number, d: number, l: number) => `${w}/${d}/${l}`;
</script>

<template>
  <section class="outcomes material">
    <header>
      <span class="eyebrow">What the flaws actually cost</span>
      <button type="button" class="shut" aria-label="Close" @click="emit('close')">
        &times;
      </button>
    </header>

    <div class="verdict">
      <p v-if="gap > 0" class="lede num">{{ gap }} points per 100 games</p>
      <p v-else-if="gap < 0" class="lede num flat">
        {{ -gap }} points the other way
      </p>
      <p v-else-if="flaggedGames > 0" class="lede flat">No measurable gap</p>
      <p v-else class="lede flat">Nothing flagged</p>
      <p class="note">
        <template v-if="flaggedGames > 0">
          Flagged moves score {{ percent(report.flawed_score) }} across
          {{ flaggedGames }} games, against
          {{ percent(report.sound_score) }} on your sound ones.
        </template>
        <template v-else>
          Every measured move came out sound, scoring
          {{ percent(report.sound_score) }}.
        </template>
      </p>
    </div>

    <table v-if="report.by_quality.length" class="tally">
      <thead>
        <tr>
          <th class="glyph"><span class="sr">Quality</span></th>
          <th class="kind">Quality</th>
          <th class="num right">Moves</th>
          <th class="num right">Games</th>
          <th class="num right">Score</th>
          <th class="num right">Loss</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in report.by_quality" :key="row.quality">
          <td class="glyph" :style="{ color: FLAW_COLORS[row.quality] }">
            {{ row.quality === "sound" ? "✓" : row.quality }}
          </td>
          <td class="kind">{{ QUALITY_NAMES[row.quality] ?? row.quality }}</td>
          <td class="num right">{{ row.moves }}</td>
          <td class="num right">{{ row.games }}</td>
          <td class="num right">{{ percent(row.score) }}</td>
          <td class="num right loss">{{ pawns(row.mean_loss_cp) }}</td>
        </tr>
      </tbody>
    </table>

    <div v-if="report.worst.length" class="worst">
      <span class="eyebrow">Costliest habits</span>
      <div class="scroll">
        <table>
          <thead>
            <tr>
              <th class="glyph"><span class="sr">Quality</span></th>
              <th class="played">Played</th>
              <th class="instead">Instead of</th>
              <th class="num right">W/D/L</th>
              <th class="num right">Score</th>
              <th class="num right">Cost</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in report.worst"
              :key="row.child"
              :class="{ here: row.child === pinned }"
              :title="`${row.line} ${row.san} · instead of ${row.best_san}`"
              tabindex="0"
              role="button"
              :aria-label="`${row.san}, ${row.points_lost} points lost across ${row.games} games`"
              @click="emit('select', row.child)"
              @keydown.enter.prevent="emit('select', row.child)"
              @keydown.space.prevent="emit('select', row.child)"
            >
              <td class="glyph" :style="{ color: FLAW_COLORS[row.quality] }">
                {{ row.quality }}
              </td>
              <td class="played">{{ row.san }}</td>
              <td class="instead">{{ row.best_san }}</td>
              <td class="num right dim">
                {{ record(row.wins, row.draws, row.losses) }}
              </td>
              <td class="num right">{{ percent(row.score) }}</td>
              <td class="num right cost">{{ row.points_lost.toFixed(1) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <p class="key">
      A point is a win, half a draw. Cost is what a move gave away against your
      own sound rate; these {{ report.worst.length }} add up to
      {{ spilled }} points. Measured on {{ coverage }}% of the moves you played,
      the rest having no engine cost yet.
    </p>
  </section>
</template>

<style scoped>
.outcomes {
  width: 480px;
  padding: 12px 13px;
  display: grid;
  grid-template-rows: auto auto auto minmax(0, 1fr) auto;
  gap: 9px;
  max-height: 64vh;
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

.verdict {
  display: grid;
  gap: 2px;
}
.lede {
  margin: 0;
  font-size: 19px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--amber);
}
.lede.flat {
  color: var(--text);
}
.note {
  margin: 0;
  font-size: 11px;
  line-height: 1.45;
  color: var(--muted);
}

table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
}
thead th {
  padding: 0 5px 4px;
  font-size: 9.5px;
  font-weight: 500;
  text-align: left;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--faint);
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
th.right {
  text-align: right;
}
.glyph {
  width: 22px;
  font-weight: 600;
}
.kind {
  width: 76px;
  color: var(--muted);
}
.played {
  width: 60px;
}
.instead {
  width: 58px;
  color: var(--muted);
}
.worst .right {
  width: 62px;
}
.dim {
  color: var(--faint);
}
.loss {
  color: var(--amber);
}
.cost {
  color: var(--accent-bright);
}

.tally {
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  padding-top: 4px;
}

.worst {
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 4px;
}
.scroll {
  min-height: 0;
  overflow-y: auto;
}
.worst thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--panel);
  backdrop-filter: blur(12px);
}
.worst tbody tr {
  cursor: pointer;
  transition: background 0.15s var(--ease);
}
.worst tbody tr:hover {
  background: var(--raised);
}
.worst tbody tr.here {
  background: var(--accent-sunk);
}
.worst tbody tr:focus-visible {
  outline: 1px solid var(--accent);
  outline-offset: -1px;
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
