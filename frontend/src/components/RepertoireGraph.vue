<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { MoveAnnotation } from "../api";
import { familyColor, HIGHLIGHT, NEUTRAL } from "../families";
import type { PlacedNode, Placement, Trail } from "../layout";

const props = defineProps<{
  placement: Placement;
  trail: Trail;
  activeDigest: string | null;
  pinnedDigest: string | null;
  slots: Map<string, number>;
  highlighted: string | null;
  annotations: Map<string, MoveAnnotation>;
}>();

const FLAW_COLORS: Record<string, string> = {
  "??": "#e66767",
  "?": "#d95926",
  "?!": "#c98500",
};

function flagFor(placed: PlacedNode): MoveAnnotation | null {
  const found = props.annotations.get(placed.node.digest);
  return found && found.quality !== "sound" ? found : null;
}

function flagOffset(placed: PlacedNode) {
  const gap = dotRadius(placed) + 8;
  if (placed.depth === 0) return { x: 0, y: gap + 9 };
  return { x: Math.cos(placed.angle) * gap, y: Math.sin(placed.angle) * gap - 7 };
}

const emit = defineEmits<{ hover: [digest: string | null]; select: [digest: string] }>();

const VIEW = 486;
const LIT = 0.34;
const BLOOM = 0.56;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 6;

const svgEl = ref<SVGSVGElement | null>(null);
const view = ref({ k: 1, x: 0, y: 0 });

let drag: { id: number; x: number; y: number; screenX: number; screenY: number } | null = null;
// Measured in screen pixels; viewBox units scale with the window and would make
// this threshold swallow ordinary clicks on a wide canvas.
let travelled = 0;
const DRAG_SLOP_PX = 6;

watch(
  () => props.placement,
  () => {
    view.value = { k: 1, x: 0, y: 0 };
  },
);

function toLocal(event: { clientX: number; clientY: number }) {
  const svg = svgEl.value;
  const ctm = svg?.getScreenCTM();
  if (!svg || !ctm) return { x: 0, y: 0 };
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const local = point.matrixTransform(ctm.inverse());
  return { x: local.x, y: local.y };
}

function zoomAt(point: { x: number; y: number }, factor: number) {
  const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.value.k * factor));
  const applied = k / view.value.k;
  view.value = {
    k,
    x: point.x - (point.x - view.value.x) * applied,
    y: point.y - (point.y - view.value.y) * applied,
  };
}

function onWheel(event: WheelEvent) {
  zoomAt(toLocal(event), Math.exp(-event.deltaY * 0.0016));
}

function onPointerDown(event: PointerEvent) {
  if (event.button !== 0) return;
  const point = toLocal(event);
  drag = {
    id: event.pointerId,
    x: point.x,
    y: point.y,
    screenX: event.clientX,
    screenY: event.clientY,
  };
  travelled = 0;
  svgEl.value?.setPointerCapture(event.pointerId);
}

function onPointerMove(event: PointerEvent) {
  if (!drag || drag.id !== event.pointerId) return;
  const point = toLocal(event);
  travelled += Math.abs(event.clientX - drag.screenX) + Math.abs(event.clientY - drag.screenY);
  view.value = {
    k: view.value.k,
    x: view.value.x + (point.x - drag.x),
    y: view.value.y + (point.y - drag.y),
  };
  drag.x = point.x;
  drag.y = point.y;
  drag.screenX = event.clientX;
  drag.screenY = event.clientY;
}

function onPointerUp(event: PointerEvent) {
  if (drag?.id === event.pointerId) svgEl.value?.releasePointerCapture(event.pointerId);
  drag = null;
}

function onNodeClick(placed: PlacedNode) {
  if (travelled > DRAG_SLOP_PX) return;
  emit("select", placed.node.digest);
}

function nudgeZoom(factor: number) {
  zoomAt({ x: 0, y: 0 }, factor);
}

function resetView() {
  view.value = { k: 1, x: 0, y: 0 };
}

// Dots shrink as the map fills so a busy repertoire stays readable.
const density = computed(() => {
  const count = props.placement.nodes.length;
  return Math.min(1, Math.max(0.5, Math.sqrt(300 / Math.max(count, 1))));
});

const dotRadius = (placed: PlacedNode) => (2.2 + placed.intensity * 8.5) * density.value;
const hitRadius = (placed: PlacedNode) => Math.max(dotRadius(placed) + 4, 8);
const isLit = (placed: PlacedNode) => placed.intensity >= LIT;
const blooms = (placed: PlacedNode) => placed.intensity >= BLOOM;

function fill(placed: PlacedNode): string {
  if (props.highlighted) {
    return placed.node.family === props.highlighted ? HIGHLIGHT : NEUTRAL;
  }
  return familyColor(placed.node.family, props.slots);
}

function fillOpacity(placed: PlacedNode): number {
  if (props.highlighted && placed.node.family !== props.highlighted) return 0.18;
  return 0.45 + 0.55 * placed.intensity;
}

const LABEL_ROOM = 13;

// Arc length the node owns on its ring. Below a label's height there is nowhere
// to put text without it landing on a neighbour.
function room(placed: PlacedNode): number {
  return placed.depth === 0 ? Infinity : placed.span * Math.max(placed.radius, 1);
}

function labelled(placed: PlacedNode): boolean {
  if (props.trail.nodes.has(placed.node.digest)) return true;
  if (props.activeDigest === placed.node.digest) return true;
  return placed.intensity > 0.66 && room(placed) >= LABEL_ROOM;
}

function labelOffset(placed: PlacedNode) {
  const gap = dotRadius(placed) + 8;
  if (placed.depth === 0) return { x: 0, y: -gap - 4, anchor: "middle" };
  return {
    x: Math.cos(placed.angle) * gap,
    y: Math.sin(placed.angle) * gap + 4,
    anchor: Math.cos(placed.angle) >= 0 ? "start" : "end",
  };
}

const ringLabel = (depth: number) => (depth % 2 === 0 ? String(depth / 2) : "");
</script>

<template>
  <div class="canvas">
    <svg
      ref="svgEl"
      :viewBox="`${-VIEW} ${-VIEW} ${VIEW * 2} ${VIEW * 2}`"
      :class="{ tracing: trail.active }"
      @wheel.prevent="onWheel"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
    >
      <g :transform="`translate(${view.x},${view.y}) scale(${view.k})`">
        <g class="rings">
          <circle v-for="ring in placement.rings" :key="ring.depth" :r="ring.radius" />
          <text
            v-for="ring in placement.rings"
            :key="`t${ring.depth}`"
            x="0"
            :y="-ring.radius"
            dy="-4"
          >
            {{ ringLabel(ring.depth) }}
          </text>
        </g>

        <g class="links">
          <path
            v-for="link in placement.edges"
            :key="link.key"
            :d="link.path"
            :class="{
              transposition: link.transposition,
              lit: trail.edges.has(link.key),
            }"
            :stroke-opacity="link.transposition ? 0.3 : 0.12 + link.weight * 0.6"
            :stroke-width="link.transposition ? 0.7 : 0.5 + link.weight * 2.6"
          />
        </g>

        <g class="nodes">
          <g
            v-for="placed in placement.nodes"
            :key="placed.node.digest"
            :transform="`translate(${placed.x},${placed.y})`"
            :class="{
              dim: !isLit(placed),
              lit: trail.nodes.has(placed.node.digest),
              active: activeDigest === placed.node.digest,
              picked: pinnedDigest === placed.node.digest,
            }"
            @pointerenter="emit('hover', placed.node.digest)"
            @pointerleave="emit('hover', null)"
            @click="onNodeClick(placed)"
          >
            <circle class="hit" :r="hitRadius(placed)" />
            <circle
              v-if="pinnedDigest === placed.node.digest"
              class="pick-ring"
              :r="dotRadius(placed) + 4"
            />
            <circle
              v-if="blooms(placed)"
              class="halo"
              :r="dotRadius(placed) * 2.7"
              :fill="fill(placed)"
              :opacity="placed.intensity * 0.17"
            />
            <circle
              class="dot"
              :r="dotRadius(placed)"
              :fill="fill(placed)"
              :fill-opacity="fillOpacity(placed)"
            />
            <text
              v-if="labelled(placed)"
              :x="labelOffset(placed).x"
              :y="labelOffset(placed).y"
              :text-anchor="labelOffset(placed).anchor"
            >
              {{ placed.node.san_path.at(-1) ?? "start" }}
            </text>
            <text
              v-if="flagFor(placed)"
              class="flaw"
              :x="flagOffset(placed).x"
              :y="flagOffset(placed).y"
              :style="{ fill: FLAW_COLORS[flagFor(placed)!.quality] }"
              text-anchor="middle"
            >
              {{ flagFor(placed)!.quality }}
            </text>
          </g>
        </g>
      </g>
    </svg>

    <div class="zoom material">
      <button type="button" aria-label="Zoom out" @click="nudgeZoom(1 / 1.4)">&minus;</button>
      <button type="button" class="num level" aria-label="Reset view" @click="resetView">
        {{ Math.round(view.k * 100) }}%
      </button>
      <button type="button" aria-label="Zoom in" @click="nudgeZoom(1.4)">+</button>
    </div>
  </div>
</template>

<style scoped>
.canvas {
  position: relative;
  height: 100%;
}
svg {
  width: 100%;
  height: 100%;
  display: block;
  touch-action: none;
  cursor: grab;
}
svg:active {
  cursor: grabbing;
}

.rings circle {
  fill: none;
  stroke: rgba(255, 255, 255, 0.045);
  stroke-width: 0.6;
}
.rings text {
  fill: var(--faint);
  font-family: var(--mono);
  font-size: 11px;
  text-anchor: middle;
  pointer-events: none;
  paint-order: stroke;
  stroke: #161616;
  stroke-width: 3px;
  stroke-linejoin: round;
}

.links path {
  fill: none;
  stroke: #565061;
  transition: stroke 0.2s var(--ease), stroke-opacity 0.2s var(--ease);
}
.links path.transposition {
  stroke: #7b7490;
  stroke-dasharray: 3 4;
}
svg.tracing .links path {
  stroke-opacity: 0.07 !important;
}
svg.tracing .links path.lit {
  stroke: var(--accent-bright);
  stroke-opacity: 0.95 !important;
}

.nodes g {
  cursor: pointer;
}
.hit {
  fill: transparent;
}
.dot {
  transition: opacity 0.2s var(--ease);
}
.nodes g.dim .dot {
  opacity: 0.6;
}
svg.tracing .nodes g .dot {
  opacity: 0.32;
}
svg.tracing .nodes g.lit .dot,
svg.tracing .nodes g.active .dot {
  opacity: 1;
}
svg.tracing .nodes g .halo {
  opacity: 0 !important;
}
svg.tracing .nodes g.lit .halo {
  opacity: 0.2 !important;
}
.nodes g.active .dot {
  stroke: #ffffff;
  stroke-width: 1.4;
  opacity: 1;
}
.pick-ring {
  fill: none;
  stroke: var(--accent-bright);
  stroke-width: 1.3;
  pointer-events: none;
}
svg.tracing .nodes g .pick-ring {
  opacity: 0.5;
}
svg.tracing .nodes g.lit .pick-ring,
.nodes g.picked .pick-ring {
  opacity: 1;
}
.nodes text {
  fill: var(--muted);
  font-family: var(--mono);
  font-size: 12px;
  pointer-events: none;
  paint-order: stroke;
  stroke: #161616;
  stroke-width: 2.6px;
  stroke-linejoin: round;
}
.nodes g.lit text,
.nodes g.active text {
  fill: #ececec;
}
svg.tracing .nodes g.active text {
  opacity: 1;
}
svg.tracing .nodes g.active .halo {
  opacity: 0.2 !important;
}
.nodes text.flaw {
  font-family: var(--ui);
  font-size: 13px;
  font-weight: 700;
  stroke-width: 3px;
}
svg.tracing .nodes g text.flaw {
  opacity: 0.35;
}
svg.tracing .nodes g.lit text.flaw,
svg.tracing .nodes g.active text.flaw {
  opacity: 1;
}

.zoom {
  position: absolute;
  right: 16px;
  bottom: 16px;
  display: flex;
  align-items: center;
  padding: 3px;
  border-radius: 999px;
}
.zoom button {
  height: 26px;
  min-width: 26px;
  color: var(--muted);
  border-radius: 999px;
  transition: color 0.15s var(--ease), background 0.15s var(--ease);
}
.zoom button:hover {
  color: var(--text);
  background: var(--raised);
}
.zoom .level {
  padding: 0 8px;
  font-size: 11px;
}
</style>
