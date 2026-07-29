<script setup lang="ts">
import { computed } from "vue";
import type { Explanation, OpeningFamily, OpeningName } from "../api";
import { familyColor, NEUTRAL } from "../families";
import type { PlacedEdge, PlacedNode } from "../layout";
import ExplanationPanel from "./ExplanationPanel.vue";
import MiniBoard from "./MiniBoard.vue";

const props = defineProps<{
  placed: PlacedNode;
  continuations: PlacedEdge[];
  pinned: boolean;
  previewing: boolean;
  family: OpeningFamily | null;
  opening: OpeningName | null;
  slots: Map<string, number>;
  explanation: Explanation | null;
  explaining: boolean;
  explanationError: string | null;
}>();

const emit = defineEmits<{ go: [digest: string]; close: []; analyse: [] }>();

const line = computed(() => {
  const path = props.placed.node.san_path;
  if (path.length === 0) return "Starting position";
  const parts: string[] = [];
  path.forEach((san, i) => {
    if (i % 2 === 0) parts.push(`${i / 2 + 1}.`);
    parts.push(san);
  });
  return parts.join(" ");
});

const toMove = computed(() => (props.placed.node.epd.split(" ")[1] === "b" ? "Black" : "White"));

const share = (edge: PlacedEdge) =>
  Math.round((edge.edge.games / Math.max(props.placed.node.games, 1)) * 100);
</script>

<template>
  <aside class="inspector material">
    <header>
      <span class="eyebrow">Position</span>
      <button v-if="pinned" type="button" aria-label="Clear selection" @click="emit('close')">
        &times;
      </button>
    </header>

    <MiniBoard :epd="placed.node.epd" :size="240" />

    <p v-if="family" class="family">
      <span
        class="swatch"
        :style="{ background: familyColor(family.key, slots) }"
        :class="{ hollow: familyColor(family.key, slots) === NEUTRAL }"
      />
      <span class="named">{{ opening ? opening.name : family.name }}</span>
      <span v-if="opening" class="num eco">{{ opening.eco }}</span>
    </p>

    <p class="num line">{{ line }}</p>

    <dl>
      <div>
        <dt>Games</dt>
        <dd class="num">{{ placed.node.games }}</dd>
      </div>
      <div>
        <dt>You score</dt>
        <dd class="num">{{ Math.round(placed.node.score * 100) }}%</dd>
      </div>
      <div>
        <dt>To move</dt>
        <dd>{{ toMove }}</dd>
      </div>
    </dl>

    <section v-if="continuations.length">
      <span class="eyebrow">Continuations</span>
      <ul>
        <li v-for="edge in continuations" :key="edge.key">
          <button type="button" @click="emit('go', edge.edge.child)">
            <span class="num san">{{ edge.edge.san }}</span>
            <span class="bar" :style="{ '--share': `${share(edge)}%` }" />
            <span class="num tally">{{ edge.edge.games }}</span>
          </button>
        </li>
      </ul>
    </section>

    <p v-if="placed.node.pruned_children > 0" class="hidden-branches">
      {{ placed.node.pruned_children }} more
      {{ placed.node.pruned_children === 1 ? "reply" : "replies" }} here, in
      {{ placed.node.pruned_child_games }}
      {{ placed.node.pruned_child_games === 1 ? "game" : "games" }}. Lower min games to show them.
    </p>

    <p v-if="previewing" class="preview">
      Click to analyse this position. Move away and the one you picked comes back.
    </p>

    <ExplanationPanel
      v-else-if="pinned"
      :explanation="explanation"
      :loading="explaining"
      :error="explanationError"
      @analyse="emit('analyse')"
    />
  </aside>
</template>

<style scoped>
.inspector {
  width: 268px;
  padding: 14px;
  display: grid;
  gap: 12px;
  justify-items: stretch;
  max-height: calc(100vh - 32px);
  overflow-y: auto;
}
.family {
  margin: -4px 0 -4px;
  display: flex;
  align-items: baseline;
  gap: 7px;
  font-size: 11.5px;
  color: var(--text);
}
.named {
  flex: 1;
  line-height: 1.4;
}
.eco {
  font-size: 9.5px;
  color: var(--faint);
}
.swatch {
  width: 9px;
  height: 9px;
  border-radius: 3px;
  flex: none;
}
.swatch.hollow {
  background: transparent !important;
  border: 1px solid var(--faint);
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
header button {
  width: 20px;
  height: 20px;
  font-size: 15px;
  color: var(--faint);
  border-radius: 5px;
}
header button:hover {
  color: var(--text);
  background: var(--raised);
}
.line {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--muted);
  word-break: break-word;
}
dl {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 2px 8px;
  padding: 9px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}
dl div {
  display: grid;
  gap: 1px;
}
dt {
  font-size: 10px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--faint);
}
dd {
  margin: 0;
  font-size: 13px;
  color: var(--text);
}
section {
  display: grid;
  gap: 6px;
}
ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 2px;
}
li button {
  width: 100%;
  display: grid;
  grid-template-columns: 42px 1fr auto;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 6px;
  text-align: left;
  transition: background 0.15s var(--ease);
}
li button:hover {
  background: var(--raised);
}
.san {
  font-size: 12px;
  color: var(--text);
}
.bar {
  height: 3px;
  border-radius: 2px;
  background: var(--accent-sunk);
  position: relative;
}
.bar::after {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: var(--share);
  border-radius: 2px;
  background: var(--accent);
}
.tally {
  font-size: 11px;
  color: var(--faint);
}
.hidden-branches {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--amber);
}
.preview {
  margin: 0;
  padding-top: 10px;
  border-top: 1px solid var(--line);
  font-size: 11px;
  line-height: 1.5;
  color: var(--faint);
}
</style>
