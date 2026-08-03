<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import MiniBoard from "./MiniBoard.vue";
import type { Analysis, Beat } from "../api";

const props = defineProps<{
  analysis: Analysis | null;
  busy?: boolean;
  error?: string | null;
}>();

const emit = defineEmits<{ build: []; close: [] }>();

const scene = ref(0);
const step = ref(0);
const playing = ref(false);
let timer: number | null = null;

const scenes = computed(() => props.analysis?.storyboard.scenes ?? []);
const current = computed(() => scenes.value[scene.value] ?? null);
const beats = computed<Beat[]>(() => current.value?.beats ?? []);
const beat = computed<Beat | null>(() => beats.value[step.value] ?? null);
const flipped = computed(
  () => props.analysis?.storyboard.orientation === "black",
);

const atEnd = computed(() => step.value >= beats.value.length - 1);

function stop() {
  playing.value = false;
  if (timer !== null) {
    window.clearInterval(timer);
    timer = null;
  }
}

function forward() {
  if (atEnd.value) {
    stop();
    return;
  }
  step.value += 1;
}

function back() {
  stop();
  if (step.value > 0) step.value -= 1;
}

function toggle() {
  if (playing.value) {
    stop();
    return;
  }
  if (atEnd.value) step.value = 0;
  playing.value = true;
  timer = window.setInterval(forward, 1400);
}

function pick(index: number) {
  stop();
  scene.value = index;
  step.value = 0;
}

watch(
  () => props.analysis,
  () => {
    stop();
    scene.value = 0;
    step.value = 0;
  },
);

onBeforeUnmount(stop);

function pawns(cp: number | null | undefined): string {
  if (cp === null || cp === undefined) return "";
  if (Math.abs(cp) > 5000) return cp > 0 ? "+M" : "-M";
  return (cp / 100 >= 0 ? "+" : "") + (cp / 100).toFixed(2);
}

const bar = computed(() => {
  const cp = beat.value?.score_cp ?? 0;
  const clamped = Math.max(-600, Math.min(600, cp));
  return `${((clamped + 600) / 1200) * 100}%`;
});
</script>

<template>
  <section class="pane">
    <header>
      <span class="label">Analysis</span>
      <button type="button" class="ghost" @click="emit('close')">Close</button>
    </header>

    <p v-if="error" class="warn">{{ error }}</p>

    <div v-if="!analysis" class="empty">
      <p>
        The engine walks the lines and the model narrates them on the board.
        Costs one model call, then it is kept.
      </p>
      <button type="button" class="primary" :disabled="busy" @click="emit('build')">
        {{ busy ? "Working the position…" : "Analyse on the board" }}
      </button>
    </div>

    <template v-else>
      <h3 class="headline">{{ analysis.explanation.headline }}</h3>

      <nav v-if="scenes.length > 1" class="scenes">
        <button
          v-for="(item, index) in scenes"
          :key="item.title"
          type="button"
          :class="{ on: index === scene }"
          @click="pick(index)"
        >
          {{ item.title }}
        </button>
      </nav>

      <div class="stage">
        <MiniBoard
          v-if="beat"
          :epd="beat.epd"
          :size="316"
          :flipped="flipped"
          :arrows="beat.arrows"
          :highlights="beat.highlights"
        />
        <div class="gauge">
          <span class="fill" :style="{ left: bar }" />
        </div>
      </div>

      <div class="move">
        <span v-if="beat?.move_san" class="san">
          {{ beat.move_san }}<em class="glyph">{{ beat.glyph }}</em>
        </span>
        <span v-else class="san muted">Start</span>
        <span class="eval">{{ pawns(beat?.score_cp) }}</span>
      </div>

      <p class="note" :class="{ blank: !beat?.note }">
        {{ beat?.note || "…" }}
      </p>

      <div class="transport">
        <button type="button" :disabled="step === 0" @click="back">‹</button>
        <button type="button" class="play" @click="toggle">
          {{ playing ? "Pause" : "Play" }}
        </button>
        <button type="button" :disabled="atEnd" @click="forward">›</button>
        <span class="count">{{ step }} / {{ beats.length - 1 }}</span>
      </div>

      <ul class="claims">
        <li v-for="claim in analysis.explanation.claims" :key="claim.text">
          {{ claim.text }}
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.pane {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.label {
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #8b8781;
}
.headline {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #e7e3dc;
  line-height: 1.3;
}
.scenes {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.scenes button {
  padding: 3px 8px;
  font-size: 11px;
  color: #a5a099;
  background: #23211f;
  border: 1px solid #35322e;
  border-radius: 4px;
  cursor: pointer;
}
.scenes button.on {
  color: #e7e3dc;
  border-color: #6f6a62;
}
.stage {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
}
.gauge {
  position: relative;
  width: 316px;
  height: 4px;
  border-radius: 2px;
  background: linear-gradient(90deg, #2b2926, #4a4642, #d3cec6);
}
.gauge .fill {
  position: absolute;
  top: -2px;
  width: 2px;
  height: 8px;
  background: #d8a75a;
  transform: translateX(-1px);
  transition: left 0.25s ease;
}
.move {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.san {
  font-size: 16px;
  font-weight: 600;
  color: #e7e3dc;
}
.san.muted {
  color: #7d7973;
  font-weight: 400;
}
.glyph {
  font-style: normal;
  color: #cc6b5a;
}
.eval {
  font-variant-numeric: tabular-nums;
  font-size: 13px;
  color: #a5a099;
}
.note {
  margin: 0;
  min-height: 34px;
  font-size: 12.5px;
  line-height: 1.45;
  color: #c8c3bb;
}
.note.blank {
  color: #55524e;
}
.transport {
  display: flex;
  align-items: center;
  gap: 6px;
}
.transport button {
  padding: 4px 10px;
  color: #cdc8c0;
  background: #23211f;
  border: 1px solid #35322e;
  border-radius: 4px;
  cursor: pointer;
}
.transport button:disabled {
  opacity: 0.35;
  cursor: default;
}
.transport .play {
  flex: 1;
}
.count {
  font-size: 11px;
  color: #7d7973;
  font-variant-numeric: tabular-nums;
}
.claims {
  margin: 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 12px;
  line-height: 1.45;
  color: #9d9891;
}
.empty p {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.5;
  color: #8b8781;
}
.primary {
  width: 100%;
  padding: 7px;
  color: #e7e3dc;
  background: #2f2c29;
  border: 1px solid #4a4640;
  border-radius: 4px;
  cursor: pointer;
}
.primary:disabled {
  opacity: 0.5;
  cursor: default;
}
.ghost {
  padding: 0;
  font-size: 11px;
  color: #7d7973;
  background: none;
  border: 0;
  cursor: pointer;
}
.warn {
  margin: 0;
  padding: 6px 8px;
  font-size: 12px;
  color: #e0b48a;
  background: rgba(160, 110, 60, 0.14);
  border-radius: 4px;
}
</style>
