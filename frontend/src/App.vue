<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  completeAuth,
  disconnectAuth,
  fetchAnnotations,
  fetchAuthStatus,
  fetchExplanation,
  requestExplanation,
  fetchGraph,
  fetchImportJob,
  GraphError,
  startAnnotation,
  startAuth,
  startImport,
  type AuthStatus,
  type Explanation,
  type GraphQuery,
  type ImportJob,
  type MoveAnnotation,
  type RepertoireGraph,
  type Side,
} from "./api";
import { defaultPicks, MAX_PICKS, slotsFor } from "./families";
import { pathTo, placeRadial, walk, type PlacedNode } from "./layout";
import AccountPanel from "./components/AccountPanel.vue";
import FamilyLegend from "./components/FamilyLegend.vue";
import Inspector from "./components/Inspector.vue";
import RepertoireGraphView from "./components/RepertoireGraph.vue";
import Segmented from "./components/Segmented.vue";
import Stepper from "./components/Stepper.vue";

const SIDES: { value: Side; label: string }[] = [
  { value: "white", label: "White" },
  { value: "black", label: "Black" },
  { value: "both", label: "Both" },
];

const RADIUS = 430;

const username = ref("dylanette");
const side = ref<Side>("white");
const maxPly = ref(10);
const minVolume = ref(2);
const maxChildren = ref(4);

const graph = ref<RepertoireGraph | null>(null);
const error = ref<string | null>(null);
const missing = ref(false);
const loading = ref(false);

const hovered = ref<string | null>(null);
const pinned = ref<string | null>(null);
const picks = ref<string[]>([]);

const authStatus = ref<AuthStatus | null>(null);
const accountOpen = ref(false);
const authorizeUrl = ref<string | null>(null);
const authBusy = ref(false);
const authError = ref<string | null>(null);
const importJob = ref<ImportJob | null>(null);

const annotations = ref<Map<string, MoveAnnotation>>(new Map());
const annotationState = ref<"missing" | "running" | "ready">("missing");
const annotationNote = ref<string | null>(null);

const explanation = ref<Explanation | null>(null);
const explaining = ref(false);
const explanationError = ref<string | null>(null);

const placement = computed(() => (graph.value ? placeRadial(graph.value, RADIUS) : null));
const slots = computed(() => slotsFor(picks.value));

function togglePick(key: string) {
  const held = picks.value;
  if (held.includes(key)) {
    picks.value = held.filter((k) => k !== key);
  } else if (held.length < MAX_PICKS) {
    picks.value = [...held, key];
  }
}

function resetPicks() {
  picks.value = graph.value ? defaultPicks(graph.value) : [];
}

const query = computed<GraphQuery>(() => ({
  username: username.value,
  side: side.value,
  maxPly: maxPly.value,
  minVolume: minVolume.value,
  maxChildren: maxChildren.value,
}));

// Hover previews, the clicked node is what the panel falls back to.
const active = computed<PlacedNode | null>(() => {
  const digest = hovered.value ?? pinned.value;
  if (!placement.value || !digest) return null;
  return placement.value.byDigest.get(digest) ?? null;
});

const previewing = computed(
  () => active.value !== null && pinned.value !== null && active.value.node.digest !== pinned.value,
);

// The lit filament stays on the node you clicked; hovering only previews the panel.
const trail = computed(() => pathTo(placement.value, pinned.value));

const continuations = computed(() => {
  if (!placement.value || !active.value) return [];
  return placement.value.outgoing.get(active.value.node.digest) ?? [];
});

const activeOpening = computed(() => {
  const index = active.value?.node.opening;
  if (!graph.value || index === null || index === undefined) return null;
  return graph.value.openings[index] ?? null;
});

const activeFamily = computed(() => {
  const key = active.value?.node.family;
  if (!graph.value || !key) return null;
  return graph.value.families.find((f) => f.key === key) ?? null;
});

const empty = computed(() => graph.value !== null && graph.value.edges.length === 0);

// Accelerates with the value, so small floors stay reachable on a large import.
const volumeStep = computed(() => {
  if (minVolume.value < 10) return 1;
  if (minVolume.value < 50) return 5;
  return 25;
});

// A line played twice out of ten thousand games is noise, not repertoire. Picked
// once per player and side, and shown in the stepper so it stays adjustable.
const tuned = new Set<string>();

function openingFloor(corpus: number): number {
  if (corpus >= 6000) return 25;
  if (corpus >= 2000) return 12;
  if (corpus >= 600) return 5;
  return 2;
}

let request = 0;

async function load() {
  const mine = ++request;
  loading.value = true;
  error.value = null;
  missing.value = false;
  try {
    const result = await fetchGraph(query.value);
    if (mine !== request) return;

    const seen = `${username.value}:${side.value}`;
    if (!tuned.has(seen)) {
      tuned.add(seen);
      const floor = openingFloor(result.max_games);
      if (floor > minVolume.value) {
        minVolume.value = floor;
        return;
      }
    }

    graph.value = result;
    pinned.value = result.root;
    hovered.value = null;
    picks.value = defaultPicks(result);
    annotations.value = new Map();
    annotationState.value = "missing";
    annotationNote.value = null;
    loadAnnotations();
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

watch([side, maxPly, minVolume, maxChildren], schedule);

let explainRequest = 0;
let explainTimer: number | undefined;

// Reading costs nothing, so this runs on every pin.
async function loadExplanation(digest: string) {
  const mine = ++explainRequest;
  try {
    const stored = await fetchExplanation(query.value, digest);
    if (mine !== explainRequest) return;
    explanation.value = stored.state === "ready" ? (stored.explanation ?? null) : null;
  } catch {
    if (mine === explainRequest) explanation.value = null;
  }
}

// Only this spends a model call, and only when the reader asks for one.
async function requestAnalysis() {
  const digest = pinned.value;
  if (!digest) return;
  const mine = ++explainRequest;
  explaining.value = true;
  explanationError.value = null;
  try {
    const result = await requestExplanation(query.value, digest);
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
  explainTimer = window.setTimeout(() => loadExplanation(digest), 150);
}

watch(pinned, scheduleExplain);

let importPoll: number | undefined;

async function refreshAuth() {
  try {
    authStatus.value = await fetchAuthStatus();
  } catch {
    authStatus.value = null;
  }
}

function openAccount() {
  accountOpen.value = true;
  authError.value = null;
  refreshAuth();
}

function closeAccount() {
  accountOpen.value = false;
  authorizeUrl.value = null;
  authError.value = null;
}

async function connect() {
  authBusy.value = true;
  authError.value = null;
  try {
    authorizeUrl.value = (await startAuth()).authorize_url;
  } catch (exc) {
    authError.value = exc instanceof Error ? exc.message : String(exc);
  } finally {
    authBusy.value = false;
  }
}

async function disconnect() {
  await disconnectAuth();
  authorizeUrl.value = null;
  await refreshAuth();
}

async function pollImport(jobId: string) {
  try {
    const job = await fetchImportJob(jobId, username.value);
    importJob.value = job;
    if (job.state === "queued" || job.state === "running") {
      importPoll = window.setTimeout(() => pollImport(jobId), 2000);
      return;
    }
    if (job.state === "failed") authError.value = job.error;
    if (job.state === "done") {
      tuned.clear();
      minVolume.value = 2;
      load();
    }
  } catch {
    importPoll = window.setTimeout(() => pollImport(jobId), 4000);
  }
}

async function runImport() {
  authBusy.value = true;
  authError.value = null;
  try {
    const job = await startImport(username.value);
    importJob.value = job;
    importPoll = window.setTimeout(() => pollImport(job.job_id), 1500);
  } catch (exc) {
    authError.value = exc instanceof Error ? exc.message : String(exc);
  } finally {
    authBusy.value = false;
  }
}

async function finishSignIn() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const state = params.get("state");
  if (!code || !state) return;

  window.history.replaceState({}, "", "/");
  accountOpen.value = true;
  authBusy.value = true;
  try {
    authStatus.value = await completeAuth(code, state);
    authorizeUrl.value = null;
  } catch (exc) {
    authError.value = exc instanceof Error ? exc.message : String(exc);
  } finally {
    authBusy.value = false;
  }
}

let annotationPoll: number | undefined;

async function loadAnnotations() {
  window.clearTimeout(annotationPoll);
  try {
    const response = await fetchAnnotations(query.value);
    if (response.state === "ready" && response.annotations) {
      const set = response.annotations;
      annotations.value = new Map(set.annotations.map((a) => [a.child, a]));
      annotationState.value = "ready";
      const flawed = set.annotations.filter((a) => a.quality !== "sound").length;
      annotationNote.value =
        `${flawed} of ${set.annotations.length} moves flagged` +
        (set.truncated ? ", busiest positions only" : "");
      return;
    }
    annotations.value = new Map();
    annotationNote.value = null;
    if (annotationState.value === "running") {
      annotationPoll = window.setTimeout(loadAnnotations, 3000);
    } else {
      annotationState.value = "missing";
    }
  } catch {
    annotationState.value = "missing";
  }
}

async function analyseMoves() {
  annotationState.value = "running";
  annotationNote.value = "Searching your positions";
  try {
    await startAnnotation(query.value);
    annotationPoll = window.setTimeout(loadAnnotations, 3000);
  } catch (exc) {
    annotationState.value = "missing";
    annotationNote.value = exc instanceof Error ? exc.message : String(exc);
  }
}

function onKey(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null;
  if (target instanceof HTMLInputElement) return;
  if (event.key === "Escape" && accountOpen.value) {
    closeAccount();
    return;
  }
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
  pinned.value = digest;
}

onMounted(() => {
  window.addEventListener("keydown", onKey);
  refreshAuth();
  finishSignIn();
  load();
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  window.clearTimeout(pending);
  window.clearTimeout(explainTimer);
  window.clearTimeout(annotationPoll);
  window.clearTimeout(importPoll);
});
</script>

<template>
  <div class="app">
    <RepertoireGraphView
      v-if="placement && !empty"
      :placement="placement"
      :trail="trail"
      :active-digest="active?.node.digest ?? null"
      :pinned-digest="pinned"
      :slots="slots"
      :annotations="annotations"
      @hover="hovered = $event"
      @select="select"
    />

    <section class="controls material">
      <header>
        <h1>FiftyMoves</h1>
        <span class="spinner" :class="{ on: loading }" aria-hidden="true" />
      </header>
      <p class="tagline">Every line {{ username }} actually plays.</p>

      <button
        type="button"
        class="account-button"
        :class="{ live: authStatus?.connected }"
        @click="accountOpen ? closeAccount() : openAccount()"
      >
        <span class="dot" />
        <span v-if="authStatus?.connected">
          {{ authStatus.username ?? "Signed in" }} &middot; {{ authStatus.export_rate }}/s
        </span>
        <span v-else>Sign in for faster imports</span>
      </button>

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

      <Segmented v-model="side" label="Playing" :options="SIDES" />
      <Stepper v-model="maxPly" label="Depth" :min="2" :max="30" />
      <Stepper v-model="minVolume" label="Min games" :min="1" :max="250" :step="volumeStep" />
      <Stepper v-model="maxChildren" label="Branches" :min="1" :max="12" />

      <p v-if="graph" class="counts num">
        {{ graph.nodes.length }} positions &middot; {{ graph.edges.length }} of
        {{ graph.considered_edges }} moves
      </p>
      <div v-if="graph && !empty" class="analyse">
        <button
          type="button"
          :disabled="annotationState === 'running'"
          @click="analyseMoves"
        >
          {{ annotationState === "ready" ? "Re-check moves" : "Check moves against the engine" }}
        </button>
        <p v-if="annotationNote" class="note">{{ annotationNote }}</p>
      </div>

      <p class="hint">
        A branch takes up as much of the circle as it took of your games. Rings count moves,
        amber ticks mark pruned replies, dashes join lines that transpose. Arrow keys walk the
        tree.
      </p>
    </section>

    <Transition name="rise">
      <AccountPanel
        v-if="accountOpen"
        :status="authStatus"
        :authorize-url="authorizeUrl"
        :username="username"
        :job="importJob"
        :busy="authBusy"
        :error="authError"
        class="inspector-slot"
        @connect="connect"
        @disconnect="disconnect"
        @run-import="runImport"
        @close="closeAccount"
      />
    </Transition>

    <Transition name="rise">
      <Inspector
        v-if="active && !accountOpen"
        :placed="active"
        :continuations="continuations"
        :pinned="pinned !== null"
        :previewing="previewing"
        :family="activeFamily"
        :opening="activeOpening"
        :slots="slots"
        :explanation="explanation"
        :explaining="explaining"
        :explanation-error="explanationError"
        class="inspector-slot"
        @go="pinned = $event"
        @close="pinned = null"
        @analyse="requestAnalysis"
      />
    </Transition>

    <FamilyLegend
      v-if="graph && graph.families.length > 0 && !empty"
      :families="graph.families"
      :slots="slots"
      :picks="picks"
      class="legend-slot"
      @toggle="togglePick"
      @reset="resetPicks"
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

.account-button {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 5px 8px;
  background: var(--sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  font-size: 11px;
  color: var(--muted);
  transition: color 0.15s var(--ease), border-color 0.15s var(--ease);
}
.account-button:hover {
  color: var(--text);
  border-color: var(--line-strong);
}
.account-button .dot {
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 999px;
  background: var(--faint);
}
.account-button.live .dot {
  background: #199e70;
}
.account-button.live {
  color: var(--text);
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
.analyse {
  display: grid;
  gap: 5px;
  padding-top: 9px;
  border-top: 1px solid var(--line);
}
.analyse button {
  padding: 5px 9px;
  background: var(--accent-sunk);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  font-size: 11.5px;
  color: var(--accent-bright);
  transition: background 0.15s var(--ease);
}
.analyse button:hover:not(:disabled) {
  background: rgba(139, 108, 239, 0.26);
}
.analyse button:disabled {
  color: var(--faint);
  cursor: default;
}
.analyse .note {
  margin: 0;
  font-size: 10.5px;
  color: var(--faint);
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
