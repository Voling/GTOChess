<script setup lang="ts">
defineProps<{
  busy: boolean;
  error: string | null;
  configured: boolean;
}>();

const emit = defineEmits<{ start: [] }>();
</script>

<template>
  <div class="gate">
    <section class="card material">
      <h1>GTO Chess</h1>
      <p class="tagline">Every line you actually play, measured.</p>

      <p v-if="!configured" class="note warn">
        No user pool is configured, so there is nothing to sign in to. Set the
        Cognito settings on the API, or turn auth off for local work.
      </p>
      <template v-else>
        <button type="button" class="go" :disabled="busy" @click="emit('start')">
          {{ busy ? "Opening…" : "Sign in" }}
        </button>
        <p class="note">
          Accounts are issued rather than opened. Every analysis is an engine run
          and a model call on this account's bill, so the door is closed by
          default.
        </p>
      </template>

      <p v-if="error" class="note bad" role="alert">{{ error }}</p>
    </section>
  </div>
</template>

<style scoped>
.gate {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
}
.card {
  width: min(360px, 100%);
  padding: 22px;
  display: grid;
  gap: 10px;
  text-align: center;
}
h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.tagline {
  margin: -4px 0 6px;
  font-size: 12px;
  color: var(--faint);
}
.go {
  padding: 9px 0;
  border-radius: var(--r-control);
  background: var(--accent-sunk);
  border: 1px solid var(--accent);
  font-size: 13px;
  color: var(--accent-bright);
  transition: background 0.15s var(--ease);
}
.go:hover:not(:disabled) {
  background: var(--accent);
  color: #fff;
}
.go:disabled {
  opacity: 0.6;
  cursor: default;
}
.note {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--faint);
}
.note.bad {
  color: #e66767;
}
.note.warn {
  color: var(--amber);
}
</style>
