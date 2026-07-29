<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  fetchExplanation,
  fetchGraph,
  GraphError,
  type Explanation,
  type GraphQuery,
  type RepertoireGraph,
} from "./api";
import { slotIndex } from "./families";
import { pathTo, placeRadial, walk, type PlacedNode } from "./layout";
import FamilyLegend from "./components/FamilyLegend.vue";
import Inspector from "./components/Inspector.vue";
import RepertoireGraphView from "./components/RepertoireGraph.vue";
import Stepper from "./components/Stepper.vue";

const RADIUS = 430;

const username = ref("dylanette");
const maxPly = ref(10);
const minVolume = ref(2);
const maxChildren = ref(4);

const graph = ref<RepertoireGraph | null>(null);
const error = ref<string | null>(null);
const missing = ref(false);
const loading = ref(false);

const hovered = ref<string | null>(null);
const pinned = ref<string | null>(null);
const highlighted = ref<string | null>(null);

const explanation = ref<Explanation | null>(null);
const explaining = ref(false);
const explanationError = ref<string | null>(null);

const placement = computed(() => (graph.value ? placeRadial(graph.value, RADIUS) : null));
const slots = computed(() => (graph.value ? slotIndex(graph.value) : new Map<string, number>()));

const query = computed<GraphQuery>(() => ({
  username: username.value,
  maxPly: maxPly.value,
  minVolume: minVolume.value,
  maxChildren: maxChildren.value,
}));

const active = computed<PlacedNode | null>(() => {
  const digest = pinned.value ?? hovered.value;
  if (!placement.value || !digest) return null;
  return placement.value.byDigest.get(digest) ?? null;
});

const trail = computed(() => pathTo(placement.value, active.value?.node.digest ?? null));

const continuations = computed(() => {
  if (!placement.value || !active.value) return [];
  return placement.value.outgoing.get(active.value.node.digest) ?? [];
});

const activeFamily = computed(() => {
  const key = active.value?.node.family;
  if (!graph.value || !key) return null;
  return graph.value.families.find((f) => f.key === key) ?? null;
});

const empty = computed(() => graph.value !== null && graph.value.edges.length === 0);

let request = 0;

async function load() {
  const mine = ++request;
  loading.value = true;
  error.value = null;
  missing.value = false;
  try {
    const result = await fetchGraph({
      username: username.value,
      maxPly: maxPly.value,
      minVolume: minVolume.value,
      maxChildren: maxChildren.value,
    });
    if (mine !== request) return;
    graph.value = result;
    pinned.value = result.root;
    hovered.value = null;
    highlighted.value = null;
  } catch (exc) {
    if (mine !== request) return;
    missing.value = exc instanceof GraphError && exc.status === 404;
    error.value = exc instanceof Error ? exc.message : String(exc);
    graph.value = null;
  } finally {
    if (mine === request) loading.value = false;
  }
}

let pending: number | undefined;

function schedule() {
  window.clearTimeout(pending);
  pending = window.setTimeout(load, 250);
}

function rename(event: Event) {
  const next = (event.target as HTMLInputElement).value.trim();
  if (!next || next === username.value) return;
  username.value = next;
  load();
}

watch([maxPly, minVolume, maxChildren], schedule);

let explainRequest = 0;
let explainTimer: number | undefined;

async function explain(digest: string) {
  const mine = ++explainRequest;
  explaining.value = true;
  explanationError.value = null;
  explanation.value = null;
  try {
    const result = await fetchExplanation(query.value, digest);
    if (mine !== explainRequest) return;
    explanation.value = result;
  } catch (exc) {
    if (mine !== explainRequest) return;
    explanationError.value = exc instanceof Error ? exc.message : String(exc);
  } finally {
    if (mine === explainRequest) explaining.value = false;
  }
}

function scheduleExplain() {
  window.clearTimeout(explainTimer);
  explainRequest += 1;
  explanation.value = null;
  explanationError.value = null;
  explaining.value = false;

  const digest = pinned.value;
  if (!digest) return;
  explaining.value = true;
  explainTimer = window.setTimeout(() => explain(digest), 200);
}

watch(pinned, scheduleExplain);

function onKey(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null;
  if (target instanceof HTMLInputElement) return;
  if (!placement.value) return;

  if (event.key === "Escape") {
    pinned.value = null;
    hovered.value = null;
    return;
  }
  if (!event.key.startsWith("Arrow")) return;

  event.preventDefault();
  const from = pinned.value ?? placement.value.root;
  const next = walk(placement.value, from, event.key);
  if (next) pinned.value = next;
  else if (!pinned.value) pinned.value = placement.value.root;
}

function select(digest: string) {
  pinned.value = pinned.value === digest ? null : digest;
}

onMounted(() => {
  window.addEventListener("keydown", onKey);
  load();
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  window.clearTimeout(pending);
  window.clearTimeout(explainTimer);
});
</script>

<template>
  <div class="app">
    <RepertoireGraphView
      v-if="placement && !empty"
      :placement="placement"
      :trail="trail"
      :active-digest="active?.node.digest ?? null"
      :slots="slots"
      :highlighted="highlighted"
      @hover="hovered = $event"
      @select="select"
    />

    <section class="controls material">
      <header>
        <h1>FiftyMoves</h1>
        <span class="spinner" :class="{ on: loading }" aria-hidden="true" />
      </header>
      <p class="tagline">Every line {{ username }} actually plays.</p>

      <label class="player">
        <span class="name">Player</span>
        <input
          :value="username"
          spellcheck="false"
          autocapitalize="off"
          autocomplete="off"
          @change="rename"
          @keydown.enter="($event.target as HTMLInputElement).blur()"
        />
      </label>

      <Stepper v-model="maxPly" label="Depth" :min="2" :max="30" />
      <Stepper v-model="minVolume" label="Min games" :min="1" :max="40" />
      <Stepper v-model="maxChildren" label="Branches" :min="1" :max="12" />

      <p v-if="graph" class="counts num">
        {{ graph.nodes.length }} positions &middot; {{ graph.edges.length }} of
        {{ graph.considered_edges }} moves
      </p>
      <p class="hint">
        Rings count moves, amber ticks mark pruned replies, dashes join lines that transpose.
        Arrow keys walk the tree.
      </p>
    </section>

    <Transition name="rise">
      <Inspector
        v-if="active"
        :placed="active"
        :continuations="continuations"
        :pinned="pinned !== null"
        :family="activeFamily"
        :slots="slots"
        :explanation="explanation"
        :explaining="explaining"
        :explanation-error="explanationError"
        class="inspector-slot"
        @go="pinned = $event"
        @close="pinned = null"
        @retry="scheduleExplain"
      />
    </Transition>

    <FamilyLegend
      v-if="graph && graph.families.length > 0 && !empty"
      :families="graph.families"
      :slots="slots"
      :highlighted="highlighted"
      class="legend-slot"
      @highlight="highlighted = $event"
    />

    <div v-if="missing" class="notice material" role="alert">
      <span class="eyebrow">Not imported yet</span>
      <p>Import {{ username }}'s games from lichess, then load again.</p>
      <code class="num">python -m fiftymoves.tools.ingest_lichess {{ username }} --out data</code>
    </div>

    <div v-else-if="error" class="notice material" role="alert">
      <span class="eyebrow">Could not load</span>
      <p>{{ error }}</p>
    </div>

    <div v-else-if="empty" class="notice material">
      <span class="eyebrow">Nothing to map</span>
      <p>
        No line reaches {{ minVolume }} games at this depth. Lower min games, or import more of
        {{ username }}'s games.
      </p>
    </div>
  </div>
</template>

<style scoped>
.app {
  position: relative;
  height: 100%;
  overflow: hidden;
}

.controls {
  position: absolute;
  top: 16px;
  left: 16px;
  width: 244px;
  padding: 14px;
  display: grid;
  gap: 9px;
}
.controls header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
h1 {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.017em;
}
.tagline {
  margin: -6px 0 3px;
  font-size: 11.5px;
  color: var(--faint);
}
.spinner {
  width: 11px;
  height: 11px;
  border: 1.5px solid var(--line-strong);
  border-top-color: var(--accent);
  border-radius: 999px;
  opacity: 0;
  transition: opacity 0.2s var(--ease);
}
.spinner.on {
  opacity: 1;
  animation: spin 0.7s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.player {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 9px;
  border-bottom: 1px solid var(--line);
}
.player .name {
  color: var(--muted);
}
input {
  width: 118px;
  padding: 3px 8px;
  background: var(--sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  text-align: right;
}
input:focus {
  border-color: var(--accent);
  outline: none;
}
.counts {
  margin: 3px 0 0;
  padding-top: 9px;
  border-top: 1px solid var(--line);
  font-size: 10.5px;
  color: var(--faint);
}

.inspector-slot {
  position: absolute;
  top: 16px;
  right: 16px;
}

.legend-slot {
  position: absolute;
  left: 16px;
  bottom: 16px;
}
.hint {
  margin: 3px 0 0;
  padding-top: 9px;
  border-top: 1px solid var(--line);
  font-size: 10.5px;
  line-height: 1.45;
  color: var(--faint);
}

.notice {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: min(360px, calc(100% - 32px));
  padding: 16px;
  display: grid;
  gap: 5px;
}
.notice p {
  margin: 0;
  color: var(--muted);
  line-height: 1.5;
}
.notice code {
  margin-top: 4px;
  padding: 7px 9px;
  background: var(--sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  font-size: 11px;
  line-height: 1.5;
  color: var(--accent-bright);
  white-space: pre-wrap;
  word-break: break-word;
}

.rise-enter-active,
.rise-leave-active {
  transition: opacity 0.22s var(--ease), transform 0.22s var(--ease);
}
.rise-enter-from,
.rise-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

@media (max-width: 860px) {
  .app {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 12px;
    overflow: auto;
  }
  .controls,
  .inspector-slot {
    position: static;
    width: auto;
  }
  .controls {
    order: 1;
  }
  .app > :deep(.canvas) {
    order: 2;
    flex: none;
    height: 62vh;
  }
  .inspector-slot {
    order: 3;
    justify-items: center;
  }
  .legend {
    display: none;
  }
  .notice {
    position: static;
    order: 2;
    transform: none;
    width: auto;
  }
}
</style>
