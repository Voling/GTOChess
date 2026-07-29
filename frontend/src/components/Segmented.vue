<script setup lang="ts" generic="T extends string">
defineProps<{
  label: string;
  modelValue: T;
  options: { value: T; label: string }[];
}>();

const emit = defineEmits<{ "update:modelValue": [value: T] }>();
</script>

<template>
  <div class="field">
    <span class="name">{{ label }}</span>
    <div class="track" role="group" :aria-label="label">
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        :class="{ on: option.value === modelValue }"
        :aria-pressed="option.value === modelValue"
        @click="emit('update:modelValue', option.value)"
      >
        {{ option.label }}
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
.track {
  display: flex;
  padding: 2px;
  background: var(--sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
}
button {
  padding: 2px 9px;
  font-size: 11px;
  color: var(--muted);
  border-radius: 5px;
  transition: color 0.15s var(--ease), background 0.15s var(--ease);
}
button:hover:not(.on) {
  color: var(--text);
}
button.on {
  color: var(--text);
  background: var(--raised);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.35);
}
</style>
