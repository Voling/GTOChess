<script setup lang="ts">
import { computed, ref, watch } from "vue";

const props = defineProps<{
  epd: string;
  size?: number;
  moves?: { uci: string; san: string; child: string }[];
  flipped?: boolean;
  lastUci?: string | null;
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
  </div>
</template>

<style scoped>
.board {
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
