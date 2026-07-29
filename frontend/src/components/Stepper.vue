<script setup lang="ts">
const props = defineProps<{ label: string; modelValue: number; min: number; max: number }>();
const emit = defineEmits<{ "update:modelValue": [value: number] }>();

function nudge(delta: number) {
  const next = Math.min(props.max, Math.max(props.min, props.modelValue + delta));
  if (next !== props.modelValue) emit("update:modelValue", next);
}
</script>

<template>
  <div class="field">
    <span class="name">{{ label }}</span>
    <div class="stepper">
      <button
        type="button"
        :disabled="modelValue <= min"
        :aria-label="`Fewer ${label.toLowerCase()}`"
        @click="nudge(-1)"
      >
        &minus;
      </button>
      <span class="num value">{{ modelValue }}</span>
      <button
        type="button"
        :disabled="modelValue >= max"
        :aria-label="`More ${label.toLowerCase()}`"
        @click="nudge(1)"
      >
        +
      </button>
    </div>
  </div>
</template>

<style scoped>
.field {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.name {
  color: var(--muted);
}
.stepper {
  display: flex;
  align-items: center;
  background: var(--sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
}
button {
  width: 24px;
  height: 24px;
  color: var(--muted);
  border-radius: var(--r-control);
  transition: color 0.15s var(--ease), background 0.15s var(--ease);
}
button:hover:not(:disabled) {
  color: var(--text);
  background: var(--raised);
}
button:disabled {
  color: #4a4a4a;
  cursor: default;
}
.value {
  min-width: 24px;
  text-align: center;
  font-size: 12px;
  color: var(--text);
}
</style>
