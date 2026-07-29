<script setup lang="ts">
import { computed } from "vue";
import type { OpeningFamily } from "../api";
import { ecoRange, familyColor, MAX_PICKS, sharpnessLabel } from "../families";

const props = defineProps<{
  families: OpeningFamily[];
  slots: Map<string, number>;
  picks: string[];
}>();

const emit = defineEmits<{ toggle: [key: string]; reset: [] }>();

const full = computed(() => props.picks.length >= MAX_PICKS);
const picked = (key: string) => props.slots.has(key);
</script>

<template>
  <section class="families material">
    <header>
      <span class="eyebrow">Openings &middot; {{ picks.length }}/{{ MAX_PICKS }}</span>
      <button type="button" class="clear" @click="emit('reset')">Reset</button>
    </header>

    <ul>
      <li v-for="family in families" :key="family.key">
        <button
          type="button"
          :class="{ muted: !picked(family.key), blocked: full && !picked(family.key) }"
          :aria-pressed="picked(family.key)"
          :title="
            full && !picked(family.key)
              ? `Deselect one first, ${MAX_PICKS} colours is the limit`
              : family.name
          "
          @click="emit('toggle', family.key)"
        >
          <span
            class="swatch"
            :style="{ background: familyColor(family.key, slots) }"
            :class="{ hollow: !picked(family.key) }"
          />
          <span class="name">{{ family.name }}</span>
          <span class="num games">{{ family.games }}</span>
          <span class="eco num">{{ ecoRange(family) ?? "" }}</span>
          <span class="meter" :title="`${sharpnessLabel(family.sharpness)} for you`">
            <span class="fill" :style="{ width: `${Math.round(family.sharpness * 100)}%` }" />
          </span>
          <span class="num score">{{ Math.round(family.score * 100) }}%</span>
        </button>
      </li>
    </ul>

    <p class="key">
      Click any opening to colour it, up to {{ MAX_PICKS }} at once. The bar is how sharp your
      games in it run, the figure is how you score.
    </p>
  </section>
</template>

<style scoped>
.families {
  width: 300px;
  padding: 12px 13px;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 8px;
  max-height: 44vh;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.clear {
  font-size: 10.5px;
  color: var(--accent-bright);
}
.clear:hover {
  text-decoration: underline;
}
ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  grid-auto-rows: max-content;
  gap: 1px;
  min-height: 0;
  overflow-y: auto;
}
li button {
  width: 100%;
  display: grid;
  grid-template-columns: 9px 1fr auto 30px 34px 30px;
  align-items: center;
  gap: 7px;
  padding: 3px 5px;
  border-radius: 5px;
  text-align: left;
  transition: background 0.15s var(--ease), opacity 0.15s var(--ease);
}
li button:hover {
  background: var(--raised);
}
li button.muted {
  opacity: 0.55;
}
li button.blocked {
  opacity: 0.3;
}
.swatch {
  width: 9px;
  height: 9px;
  border-radius: 3px;
}
.swatch.hollow {
  background: transparent !important;
  border: 1px solid var(--faint);
}
.name {
  font-size: 11.5px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.games {
  font-size: 10.5px;
  color: var(--muted);
}
.eco {
  font-size: 9.5px;
  color: var(--faint);
  text-align: right;
}
.meter {
  height: 3px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.09);
  position: relative;
}
.meter .fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 2px;
  background: var(--muted);
}
.score {
  font-size: 10.5px;
  color: var(--muted);
  text-align: right;
}
.key {
  margin: 2px 0 0;
  padding-top: 8px;
  border-top: 1px solid var(--line);
  font-size: 10.5px;
  line-height: 1.45;
  color: var(--faint);
}
</style>
