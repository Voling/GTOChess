<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  completeAuth,
  disconnectAuth,
  fetchMoveLosses,
  fetchAuthStatus,
  fetchAnalysis,
  buildAnalysis,
  fetchExplanation,
  requestExplanation,
  fetchGraph,
  fetchImportJob,
  GraphError,
  fetchOpeningPhase,
  startAuth,
  startImport,
  type Analysis,
  type AuthStatus,
  type Explanation,
  type GraphQuery,
  type ImportJob,
  type MoveAnnotation,
  type OpeningName,
  type OpeningPhase,
  type RepertoireGraph,
  type Side,
} from "./api";
import { defaultPicks, MAX_PICKS, slotsFor } from "./families";
import { ancestry, pathTo, placeRadial, walk, type PlacedNode } from "./layout";
import AccountPanel from "./components/AccountPanel.vue";
import FamilyLegend from "./components/FamilyLegend.vue";
import Inspector from "./components/Inspector.vue";
import AnalysisPane from "./components/AnalysisPane.vue";
import MistakeTable, { type Mistake } from "./components/MistakeTable.vue";
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
const picks = ref<string[]>([]);
const blocked = ref<string | null>(null);

// The current line, root first, with a cursor on it. Stepping back keeps the
// moves ahead so forward replays them, the way a board viewer does.
const lineNodes = ref<string[]>([]);
const cursor = ref(-1);
const pinned = computed(() =>
  cursor.value < 0 ? null : (lineNodes.value[cursor.value] ?? null),
);

const authStatus = ref<AuthStatus | null>(null);
const accountOpen = ref(false);
const authorizeUrl = ref<string | null>(null);
const authBusy = ref(false);
const authError = ref<string | null>(null);
const importJob = ref<ImportJob | null>(null);

const annotations = ref<Map<string, MoveAnnotation>>(new Map());
const annotationNote = ref<string | null>(null);
const measuredMoves = ref(0);
const mistakesOpen = ref(false);

const explanation = ref<Explanation | null>(null);
const explaining = ref(false);
const explanationError = ref<string | null>(null);
const analysis = ref<Analysis | null>(null);
const analysing = ref(false);
const analysisError = ref<string | null>(null);
const analysisOpen = ref(false);

const placement = computed(() =>
  graph.value ? placeRadial(graph.value, RADIUS) : null,
);
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
  () =>
    active.value !== null &&
    pinned.value !== null &&
    active.value.node.digest !== pinned.value,
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

const empty = computed(
  () => graph.value !== null && graph.value.edges.length === 0,
);

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
    lineNodes.value = [result.root];
    cursor.value = 0;
    hovered.value = null;
    blocked.value = null;
    picks.value = defaultPicks(result);
    annotations.value = new Map();
    annotationNote.value = null;
    loadAnnotations();
    loadPhase();
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
    explanation.value =
      stored.state === "ready" ? (stored.explanation ?? null) : null;
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

async function openAnalysis() {
  const digest = pinned.value;
  if (!digest) return;
  analysisOpen.value = true;
  analysisError.value = null;
  if (analysis.value) return;
  try {
    const held = await fetchAnalysis(query.value, digest);
    if (held.state === "ready" && held.analysis) analysis.value = held.analysis;
  } catch {
    // Nothing stored yet is the normal case, not an error worth showing.
  }
}

// Spends a model call plus a run of engine searches, only on an explicit press.
async function buildBoardAnalysis() {
  const digest = pinned.value;
  if (!digest || analysing.value) return;
  analysing.value = true;
  analysisError.value = null;
  try {
    analysis.value = await buildAnalysis(query.value, digest);
  } catch (exc) {
    analysisError.value = exc instanceof Error ? exc.message : String(exc);
  } finally {
    analysing.value = false;
  }
}

function scheduleExplain() {
  window.clearTimeout(explainTimer);
  explainRequest += 1;
  explanation.value = null;
  explanationError.value = null;
  explaining.value = false;
  analysis.value = null;
  analysisError.value = null;

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

// Costs are keyed by position, so a slider nudge rereads the same records
// instead of throwing away engine time.
async function loadAnnotations() {
  window.clearTimeout(annotationPoll);
  try {
    const response = await fetchMoveLosses(query.value);
    annotations.value = new Map(response.marks.map((m) => [m.child, m]));
    measuredMoves.value = response.measured_moves;
    annotationNote.value = response.measured_moves
      ? `${response.flagged} flagged of ${response.measured_moves} moves measured`
      : null;
  } catch {
    annotations.value = new Map();
    measuredMoves.value = 0;
    annotationNote.value = null;
  }
}

// The graph marks a flaw where it sits. This is the same set read end to end,
// so a habit spread thinly over a repertoire is still visible.
const mistakes = computed<Mistake[]>(() => {
  const place = placement.value;
  const held = graph.value;
  // The position after the move carries the more specific name, and a node only
  // takes one past the ply its opening becomes identifiable, so this falls back
  // up the line rather than leaving a row unlabelled.
  const nameFor = (digest: string | undefined): OpeningName | null => {
    if (!held || !digest) return null;
    const index = place?.byDigest.get(digest)?.node.opening;
    if (index === null || index === undefined) return null;
    return held.openings[index] ?? null;
  };

  return [...annotations.value.values()]
    .filter((mark) => mark.quality !== "sound")
    .map((mark) => {
      const placed = place?.byDigest.get(mark.child) ?? null;
      const path = placed?.node.san_path ?? [];
      const opening = nameFor(mark.child) ?? nameFor(mark.parent);
      const family = placed?.node.family ?? null;
      return {
        mark,
        move: placed ? Math.ceil(placed.depth / 2) : null,
        line: path.length ? path.join(" ") : mark.san,
        opening,
        family,
        familyName: held?.families.find((f) => f.key === family)?.name ?? null,
      };
    });
});

const phase = ref<OpeningPhase | null>(null);

async function loadPhase() {
  try {
    phase.value = await fetchOpeningPhase(query.value);
  } catch {
    phase.value = null;
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
    clearSelection();
    hovered.value = null;
    return;
  }
  if (!event.key.startsWith("Arrow")) return;

  event.preventDefault();
  if (event.key === "ArrowLeft") {
    back();
    return;
  }
  if (event.key === "ArrowRight") {
    forward();
    return;
  }
  const from = pinned.value ?? placement.value.root;
  const next = walk(placement.value, from, event.key);
  if (next) select(next);
  else if (!pinned.value) select(placement.value.root);
}

function select(digest: string) {
  blocked.value = null;
  const held = lineNodes.value;
  const known = held.indexOf(digest);
  if (known >= 0) {
    cursor.value = known;
    return;
  }
  const current = pinned.value;
  if (current && placement.value?.parent.get(digest) === current) {
    lineNodes.value = [...held.slice(0, cursor.value + 1), digest];
    cursor.value = lineNodes.value.length - 1;
    return;
  }
  const path = ancestry(placement.value, digest);
  lineNodes.value = path;
  cursor.value = path.length - 1;
}

function clearSelection() {
  lineNodes.value = [];
  cursor.value = -1;
  blocked.value = null;
}

const canBack = computed(() => cursor.value > 0);
const canForward = computed(
  () => cursor.value >= 0 && cursor.value < lineNodes.value.length - 1,
);

function back() {
  if (canBack.value) {
    cursor.value -= 1;
    blocked.value = null;
  }
}

function forward() {
  if (canForward.value) {
    cursor.value += 1;
    blocked.value = null;
    return;
  }
  // Nothing remembered ahead, so follow the line played most from here.
  const next = continuations.value[0];
  if (next) select(next.edge.child);
}

function resetLine() {
  if (lineNodes.value.length) cursor.value = 0;
  blocked.value = null;
}

function play(uci: string) {
  const edge = continuations.value.find((e) => e.edge.uci === uci);
  if (edge) select(edge.edge.child);
}

function refuse(square: string) {
  const node = active.value?.node;
  const hidden = node?.pruned_children ?? 0;
  blocked.value = hidden
    ? `That move is off the mapped tree. ${hidden} ${hidden === 1 ? "reply is" : "replies are"} pruned here, in ${node?.pruned_child_games} ${node?.pruned_child_games === 1 ? "game" : "games"}. Lower min games to walk them.`
    : `No game of yours continued from ${square} here, so the tree stops.`;
}

const flipped = computed(() => side.value === "black");

const lastUci = computed(() => {
  const digest = active.value?.node.digest;
  if (!placement.value || !digest) return null;
  const above = placement.value.parent.get(digest);
  if (!above) return null;
  const edge = placement.value.outgoing
    .get(above)
    ?.find((e) => e.edge.child === digest);
  return edge?.edge.uci ?? null;
});

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
        <h1>GTO Chess</h1>
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
          {{ authStatus.username ?? "Signed in" }} &middot;
          {{ authStatus.export_rate }}/s
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
      <Stepper
        v-model="minVolume"
        label="Min games"
        :min="1"
        :max="250"
        :step="volumeStep"
      />
      <Stepper v-model="maxChildren" label="Branches" :min="1" :max="12" />

      <p v-if="graph" class="counts num">
        {{ graph.nodes.length }} positions &middot; {{ graph.edges.length }} of
        {{ graph.considered_edges }} moves
      </p>
      <div v-if="phase && phase.positions_scored > 0" class="phase">
        <span class="eyebrow">Opening phase</span>
        <p class="book num">Book runs to move {{ phase.book_depth }}</p>
        <p class="note">
          {{ Math.round(phase.clean_share * 100) }}% of your opening moves stay
          within {{ (phase.band_cp / 100).toFixed(2) }}, across
          {{ phase.moves_scored }} moves
        </p>
        <button
          v-if="annotationNote"
          type="button"
          class="note flaws"
          :class="{ on: mistakesOpen }"
          :disabled="mistakes.length === 0"
          @click="mistakesOpen = !mistakesOpen"
        >
          {{ annotationNote }}
          <span v-if="mistakes.length" aria-hidden="true">&rsaquo;</span>
        </button>
      </div>

      <p class="hint">
        A branch takes up as much of the circle as it took of your games. Rings
        count moves, amber ticks mark pruned replies, dashes join lines that
        transpose. Play moves on the board, or walk the line with the arrow
        keys.
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
        :flipped="flipped"
        :last-uci="lastUci"
        :can-back="canBack"
        :can-forward="canForward"
        :blocked="blocked"
        @go="select"
        @close="clearSelection"
        @analyse="requestAnalysis"
        @play="play"
        @blocked="refuse"
        @back="back"
        @forward="forward"
        @reset="resetLine"
        @board="openAnalysis"
      />
    </Transition>

    <Transition name="rise">
      <AnalysisPane
        v-if="analysisOpen && pinned"
        :analysis="analysis"
        :busy="analysing"
        :error="analysisError"
        class="analysis-slot"
        @build="buildBoardAnalysis"
        @close="analysisOpen = false"
      />
    </Transition>

    <Transition name="rise">
      <MistakeTable
        v-if="mistakesOpen && mistakes.length > 0 && !empty"
        :mistakes="mistakes"
        :measured="measuredMoves"
        :pinned="pinned"
        :slots="slots"
        class="mistakes-slot"
        @select="select"
        @close="mistakesOpen = false"
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
      <code class="num"
        >python -m gtochess.tools.ingest_lichess {{ username }} --out
        data</code
      >
    </div>

    <div v-else-if="error" class="notice material" role="alert">
      <span class="eyebrow">Could not load</span>
      <p>{{ error }}</p>
    </div>

    <div v-else-if="empty" class="notice material">
      <span class="eyebrow">Nothing to map</span>
      <p>
        No line reaches {{ minVolume }} games at this depth. Lower min games, or
        import more of {{ username }}'s games.
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
  transition:
    color 0.15s var(--ease),
    border-color 0.15s var(--ease);
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
/* Clears the legend on the left and leaves the inspector's column free, so a
   row click has somewhere to land. */
.mistakes-slot {
  position: absolute;
  left: 336px;
  bottom: 16px;
}
.analysis-slot {
  position: absolute;
  top: 16px;
  left: 336px;
  width: 348px;
  max-height: calc(100vh - 32px);
  overflow-y: auto;
  padding: 12px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--r-panel);
}
.phase {
  display: grid;
  gap: 3px;
  padding-top: 9px;
  border-top: 1px solid var(--line);
}
.phase .book {
  margin: 1px 0 2px;
  font-size: 13px;
  color: var(--accent-bright);
}
.phase .note {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.45;
  color: var(--faint);
}
.flaws {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  width: 100%;
  padding: 3px 5px;
  margin-left: -5px;
  border-radius: 5px;
  text-align: left;
  transition: background 0.15s var(--ease), color 0.15s var(--ease);
}
.flaws:not(:disabled):hover,
.flaws.on {
  background: var(--raised);
  color: var(--text);
}
.flaws:disabled {
  cursor: default;
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
  transition:
    opacity 0.22s var(--ease),
    transform 0.22s var(--ease);
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
  .mistakes-slot {
    position: static;
    order: 4;
    width: auto;
    max-height: none;
  }
  .notice {
    position: static;
    order: 2;
    transform: none;
    width: auto;
  }
}
</style>
