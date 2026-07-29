<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ epd: string; size?: number }>();

const GLYPHS: Record<string, string> = {
  k: "♚",
  q: "♛",
  r: "♜",
  b: "♝",
  n: "♞",
  p: "♟",
};

interface Cell {
  glyph: string | null;
  white: boolean;
  dark: boolean;
}

const squares = computed<Cell[]>(() => {
  const placement = props.epd.split(" ")[0] ?? "";
  const cells: Cell[] = [];
  let index = 0;
  const push = (glyph: string | null, white: boolean) => {
    cells.push({ glyph, white, dark: (index + Math.floor(index / 8)) % 2 === 1 });
    index += 1;
  };
  for (const rank of placement.split("/")) {
    for (const symbol of rank) {
      const empty = Number(symbol);
      if (Number.isFinite(empty) && symbol.trim() !== "") {
        for (let i = 0; i < empty; i += 1) push(null, false);
      } else {
        push(GLYPHS[symbol.toLowerCase()] ?? null, symbol === symbol.toUpperCase());
      }
    }
  }
  return cells;
});

const edge = computed(() => `${props.size ?? 168}px`);
const glyphSize = computed(() => `${((props.size ?? 168) / 8) * 0.82}px`);
</script>

<template>
  <div class="board" :style="{ width: edge, height: edge, '--glyph': glyphSize }">
    <div v-for="(cell, i) in squares" :key="i" class="square" :class="{ dark: cell.dark }">
      <span v-if="cell.glyph" class="piece" :class="cell.white ? 'white' : 'black'">
        {{ cell.glyph }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.board {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  grid-template-rows: repeat(8, 1fr);
  border-radius: 6px;
  overflow: hidden;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.5), 0 6px 18px rgba(0, 0, 0, 0.4);
}
.square {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #9d9992;
}
.square.dark {
  background: #605c57;
}
.piece {
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
</style>
