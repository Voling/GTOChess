<script setup lang="ts">
import { computed, ref, watch } from "vue";

const props = defineProps<{
  epd: string;
  size?: number;
  moves?: { uci: string; san: string; child: string }[];
  flipped?: boolean;
  lastUci?: string | null;
  arrows?: { origin: string; target: string; role: string }[];
  highlights?: string[];
}>();

const emit = defineEmits<{ play: [uci: string]; blocked: [square: string] }>();

const GLYPHS: Record<string, string> = {
  k: "♚",
  q: "♛",
  r: "♜",
  b: "♝",
  n: "♞",
  p: "♟",
};

const FILES = "abcdefgh";

interface Cell {
  square: string;
  glyph: string | null;
  white: boolean;
  dark: boolean;
}

const whiteToMove = computed(() => props.epd.split(" ")[1] !== "b");

const cells = computed<Cell[]>(() => {
  const placement = props.epd.split(" ")[0] ?? "";
  const out: Cell[] = [];
  let index = 0;
  const push = (glyph: string | null, white: boolean) => {
    out.push({
      square: `${FILES[index % 8]}${8 - Math.floor(index / 8)}`,
      glyph,
      white,
      dark: (index + Math.floor(index / 8)) % 2 === 1,
    });
    index += 1;
  };
  for (const rank of placement.split("/")) {
    for (const symbol of rank) {
      const empty = Number(symbol);
      if (Number.isFinite(empty) && symbol.trim() !== "") {
        for (let i = 0; i < empty; i += 1) push(null, false);
      } else {
        push(
          GLYPHS[symbol.toLowerCase()] ?? null,
          symbol === symbol.toUpperCase(),
        );
      }
    }
  }
  return out;
});

const shown = computed(() =>
  props.flipped ? [...cells.value].reverse() : cells.value,
);

const selected = ref<string | null>(null);
watch(
  () => props.epd,
  () => (selected.value = null),
);

// The tree only holds moves that were played, so matching a drag against the
// node's own replies is both the move list and the legality check.
const fromSquares = computed(
  () => new Set((props.moves ?? []).map((m) => m.uci.slice(0, 2))),
);

const targets = computed(() => {
  const from = selected.value;
  if (!from) return new Map<string, string>();
  const out = new Map<string, string>();
  for (const move of props.moves ?? []) {
    if (move.uci.slice(0, 2) === from) out.set(move.uci.slice(2, 4), move.uci);
  }
  return out;
});

const lastSquares = computed(() => {
  const uci = props.lastUci;
  return uci ? new Set([uci.slice(0, 2), uci.slice(2, 4)]) : new Set<string>();
});

function occupantIsMover(cell: Cell): boolean {
  return cell.glyph !== null && cell.white === whiteToMove.value;
}

function tap(cell: Cell) {
  const played = targets.value.get(cell.square);
  if (played) {
    selected.value = null;
    emit("play", played);
    return;
  }
  if (selected.value && occupantIsMover(cell)) {
    selected.value = fromSquares.value.has(cell.square) ? cell.square : null;
    if (!selected.value) emit("blocked", cell.square);
    return;
  }
  if (selected.value) {
    // A piece was up and this square is not one of its mapped destinations.
    if (cell.glyph === null || !cell.white === whiteToMove.value)
      emit("blocked", cell.square);
    selected.value = null;
    return;
  }
  if (!occupantIsMover(cell)) return;
  if (!fromSquares.value.has(cell.square)) {
    emit("blocked", cell.square);
    return;
  }
  selected.value = cell.square;
}

const edge = computed(() => `${props.size ?? 168}px`);
const glyphSize = computed(() => `${((props.size ?? 168) / 8) * 0.82}px`);
const playable = computed(() => (props.moves?.length ?? 0) > 0);

const marked = computed(() => new Set(props.highlights ?? []));

const ARROW_COLOURS: Record<string, string> = {
  played: "#d8a75a",
  idea: "#6f9fd8",
  threat: "#cc6b5a",
};

function centre(square: string): [number, number] | null {
  const file = FILES.indexOf(square[0]);
  const rank = Number(square[1]);
  if (file < 0 || !Number.isFinite(rank)) return null;
  let col = file;
  let row = 8 - rank;
  if (props.flipped) {
    col = 7 - col;
    row = 7 - row;
  }
  return [(col + 0.5) * 12.5, (row + 0.5) * 12.5];
}

const drawn = computed(() =>
  (props.arrows ?? [])
    .map((arrow) => {
      const from = centre(arrow.origin);
      const to = centre(arrow.target);
      if (!from || !to) return null;
      const [x1, y1] = from;
      const [x2, y2] = to;
      const length = Math.hypot(x2 - x1, y2 - y1) || 1;
      // Stop short of the centre so the arrowhead sits on the square's edge.
      const trim = 4.2;
      return {
        key: `${arrow.origin}${arrow.target}${arrow.role}`,
        x1,
        y1,
        x2: x2 - ((x2 - x1) / length) * trim,
        y2: y2 - ((y2 - y1) / length) * trim,
        colour: ARROW_COLOURS[arrow.role] ?? ARROW_COLOURS.idea,
      };
    })
    .filter((a): a is NonNullable<typeof a> => a !== null),
);
</script>

<template>
  <div
    class="board"
    :style="{ width: edge, height: edge, '--glyph': glyphSize }"
  >
    <button
      v-for="cell in shown"
      :key="cell.square"
      type="button"
      class="square"
      :class="{
        dark: cell.dark,
        pick: cell.square === selected,
        target: targets.has(cell.square),
        last: lastSquares.has(cell.square),
        marked: marked.has(cell.square),
        live: playable && occupantIsMover(cell) && fromSquares.has(cell.square),
      }"
      :aria-label="cell.square"
      :tabindex="-1"
      @click="tap(cell)"
    >
      <span
        v-if="cell.glyph"
        class="piece"
        :class="cell.white ? 'white' : 'black'"
      >
        {{ cell.glyph }}
      </span>
      <span
        v-if="targets.has(cell.square)"
        class="dot"
        :class="{ over: cell.glyph !== null }"
      />
    </button>
    <svg v-if="drawn.length" class="overlay" viewBox="0 0 100 100">
      <defs>
        <marker
          v-for="arrow in drawn"
          :id="`head-${arrow.key}`"
          :key="`m-${arrow.key}`"
          markerWidth="3.4"
          markerHeight="3.4"
          refX="2.6"
          refY="1.7"
          orient="auto"
        >
          <path d="M0,0 L3.4,1.7 L0,3.4 z" :fill="arrow.colour" />
        </marker>
      </defs>
      <line
        v-for="arrow in drawn"
        :key="arrow.key"
        :x1="arrow.x1"
        :y1="arrow.y1"
        :x2="arrow.x2"
        :y2="arrow.y2"
        :stroke="arrow.colour"
        stroke-width="1.6"
        stroke-linecap="round"
        :marker-end="`url(#head-${arrow.key})`"
      />
    </svg>
  </div>
</template>

<style scoped>
.overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
.board {
  position: relative;
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  grid-template-rows: repeat(8, 1fr);
  border-radius: 6px;
  overflow: hidden;
  box-shadow:
    0 0 0 1px rgba(0, 0, 0, 0.5),
    0 6px 18px rgba(0, 0, 0, 0.4);
}
.square {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: #9d9992;
  cursor: default;
}
.square.dark {
  background: #605c57;
}
.square.live,
.square.target {
  cursor: pointer;
}
.square.last::after {
  content: "";
  position: absolute;
  inset: 0;
  background: rgba(216, 167, 90, 0.22);
}
.square.marked::before {
  content: "";
  position: absolute;
  inset: 0;
  box-shadow: inset 0 0 0 2px rgba(216, 167, 90, 0.75);
}
.square.pick::after {
  content: "";
  position: absolute;
  inset: 0;
  background: rgba(139, 108, 239, 0.34);
}
.piece {
  position: relative;
  z-index: 1;
  font-size: var(--glyph);
  line-height: 1;
}
.piece.white {
  color: #f6f4f1;
  -webkit-text-stroke: 0.6px #23211d;
}
.piece.black {
  color: #1a1917;
}
.dot {
  position: absolute;
  z-index: 2;
  width: 26%;
  height: 26%;
  border-radius: 999px;
  background: rgba(22, 22, 22, 0.42);
}
.dot.over {
  width: 84%;
  height: 84%;
  background: transparent;
  border: 3px solid rgba(22, 22, 22, 0.42);
}
</style>
