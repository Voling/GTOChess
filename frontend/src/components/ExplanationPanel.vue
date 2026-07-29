<script setup lang="ts">
import { computed } from "vue";
import type { Explanation } from "../api";

const props = defineProps<{
  explanation: Explanation | null;
  loading: boolean;
  error: string | null;
}>();

const emit = defineEmits<{ analyse: [] }>();

const byId = computed(() => new Map((props.explanation?.evidence ?? []).map((e) => [e.id, e])));

const citation = (id: string) => byId.value.get(id)?.statement ?? id;

const attribution = computed(() => {
  const source = props.explanation?.source;
  if (!source) return "";
  if (source === "deterministic") return "Read straight off the measurements";
  return props.explanation?.model ?? source;
});
</script>

<template>
  <section class="explain">
    <header>
      <span class="eyebrow">What this turns on</span>
      <span v-if="loading" class="spinner" aria-label="Analysing" />
    </header>

    <p v-if="loading" class="status">Asking the engine, then writing it up.</p>

    <div v-else-if="error" class="status failed">
      <p>{{ error }}</p>
      <button type="button" @click="emit('analyse')">Try again</button>
    </div>

    <div v-else-if="!explanation" class="status idle">
      <p>No write up for this position yet.</p>
      <button type="button" class="ask" @click="emit('analyse')">Analyse this position</button>
      <p class="cost">Runs once, then everyone who reaches this position reads it.</p>
    </div>

    <template v-else>
      <p class="headline">{{ explanation.headline }}</p>
      <ol>
        <li v-for="(claim, i) in explanation.claims" :key="i">
          <p class="claim">{{ claim.text }}</p>
          <p v-if="citation(claim.evidence_id) !== claim.text" class="cite">
            {{ citation(claim.evidence_id) }}
          </p>
        </li>
      </ol>
      <p class="footnote">
        {{ attribution }}<template v-if="explanation.dropped_claims > 0">
          &middot; {{ explanation.dropped_claims }} uncited
          {{ explanation.dropped_claims === 1 ? "claim" : "claims" }} dropped</template>
      </p>
      <p v-if="explanation.fallback_reason" class="fallback">
        The model was unavailable, so this is read straight off the measurements.
      </p>
    </template>
  </section>
</template>

<style scoped>
.explain {
  display: grid;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.spinner {
  width: 10px;
  height: 10px;
  border: 1.5px solid var(--line-strong);
  border-top-color: var(--accent);
  border-radius: 999px;
  animation: spin 0.7s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.status {
  margin: 0;
  font-size: 11px;
  color: var(--faint);
}
.status.failed {
  display: grid;
  gap: 6px;
  justify-items: start;
  color: var(--amber);
}
.status.failed p {
  margin: 0;
  font-size: 11px;
  line-height: 1.45;
}
.status.failed button {
  font-size: 11px;
  color: var(--accent-bright);
}
.status.failed button:hover {
  text-decoration: underline;
}
.status.idle {
  display: grid;
  gap: 7px;
  justify-items: start;
}
.status.idle p {
  margin: 0;
}
.ask {
  padding: 5px 10px;
  background: var(--accent-sunk);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  font-size: 11.5px;
  color: var(--accent-bright);
  transition: background 0.15s var(--ease);
}
.ask:hover {
  background: rgba(139, 108, 239, 0.26);
}
.cost {
  font-size: 10px;
  color: var(--faint);
}
.headline {
  margin: 0;
  font-size: 13px;
  font-weight: 590;
  letter-spacing: -0.01em;
  color: var(--text);
}
ol {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 9px;
}
.claim {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--text);
}
.cite {
  margin: 3px 0 0;
  padding-left: 8px;
  border-left: 1px solid var(--line-strong);
  font-size: 10.5px;
  line-height: 1.45;
  color: var(--faint);
}
.footnote {
  margin: 0;
  font-size: 10px;
  color: var(--faint);
}
.fallback {
  margin: 0;
  font-size: 10px;
  line-height: 1.45;
  color: var(--amber);
}
</style>
