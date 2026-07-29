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
  flipped: boolean;
  lastUci: string | null;
  canBack: boolean;
  canForward: boolean;
  blocked: string | null;
}>();

const emit = defineEmits<{
  go: [digest: string];
  close: [];
  analyse: [];
  play: [uci: string];
  blocked: [square: string];
  back: [];
  forward: [];
  reset: [];
}>();

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

const toMove = computed(() =>
  props.placed.node.epd.split(" ")[1] === "b" ? "Black" : "White",
);

const boardMoves = computed(() =>
  props.continuations.map((e) => ({
    uci: e.edge.uci,
    san: e.edge.san,
    child: e.edge.child,
  })),
);

const share = (edge: PlacedEdge) =>
  Math.round((edge.edge.games / Math.max(props.placed.node.games, 1)) * 100);
</script>

<template>
  <aside class="inspector material">
    <header>
      <span class="eyebrow">Position</span>
      <span class="num ply">{{ placed.node.depth_ply }} ply</span>
      <button
        v-if="pinned"
        type="button"
        aria-label="Clear selection"
        @click="emit('close')"
      >
        &times;
      </button>
    </header>

    <div class="stage">
      <MiniBoard
        :epd="placed.node.epd"
        :size="316"
        :moves="boardMoves"
        :flipped="flipped"
        :last-uci="lastUci"
        @play="emit('play', $event)"
        @blocked="emit('blocked', $event)"
      />
      <nav>
        <button
          type="button"
          :disabled="!canBack"
          aria-label="Back"
          @click="emit('back')"
        >
          &#8592;
        </button>
        <button
          type="button"
          :disabled="!canForward"
          aria-label="Forward"
          @click="emit('forward')"
        >
          &#8594;
        </button>
        <button type="button" class="reset" @click="emit('reset')">
          Reset
        </button>
        <span class="num turn">{{ toMove }} to move</span>
      </nav>
    </div>

    <p v-if="blocked" class="blocked">{{ blocked }}</p>

    <p v-if="family" class="family">
      <span
        class="swatch"
        :style="{ background: familyColor(family.key, slots) }"
        :class="{ hollow: familyColor(family.key, slots) === NEUTRAL }"
      />
      <span class="named">{{ opening ? opening.name : family.name }}</span>
      <span v-if="opening?.eco" class="num eco">{{ opening.eco }}</span>
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
        <dt>Opponents</dt>
        <dd class="num">{{ placed.node.rating ?? "n/a" }}</dd>
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
      {{ placed.node.pruned_child_games === 1 ? "game" : "games" }}. Lower min
      games to show them.
    </p>

    <p v-if="previewing" class="preview">
      Click to analyse this position. Move away and the one you picked comes
      back.
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
  width: 348px;
  padding: 14px;
  display: grid;
  gap: 11px;
  justify-items: stretch;
  max-height: calc(100vh - 32px);
  overflow-y: auto;
}
.stage {
  display: grid;
  gap: 8px;
  justify-items: center;
}
nav {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 5px;
}
nav button {
  padding: 3px 9px;
  background: var(--sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  font-size: 12px;
  color: var(--muted);
  transition:
    color 0.15s var(--ease),
    border-color 0.15s var(--ease);
}
nav button:hover:not(:disabled) {
  color: var(--text);
  border-color: var(--line-strong);
}
nav button:disabled {
  color: var(--line-strong);
  cursor: default;
}
nav .reset {
  font-size: 11px;
}
nav .turn {
  margin-left: auto;
  font-size: 11px;
  color: var(--faint);
}
.blocked {
  margin: 0;
  padding: 6px 8px;
  background: rgba(216, 167, 90, 0.1);
  border: 1px solid rgba(216, 167, 90, 0.3);
  border-radius: var(--r-control);
  font-size: 11px;
  line-height: 1.45;
  color: var(--amber);
}
.family {
  margin: -2px 0;
  display: flex;
  align-items: baseline;
  gap: 7px;
  font-size: 12px;
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
  gap: 8px;
}
header .ply {
  margin-left: auto;
  font-size: 10px;
  color: var(--faint);
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
  font-size: 14px;
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
  grid-template-columns: 1fr 1fr;
  gap: 2px 8px;
}
li button {
  width: 100%;
  display: grid;
  grid-template-columns: 40px 1fr auto;
  align-items: center;
  gap: 7px;
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
