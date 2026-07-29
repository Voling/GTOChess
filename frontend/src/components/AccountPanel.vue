<script setup lang="ts">
import { computed } from "vue";
import type { AuthStatus, ImportJob } from "../api";

const props = defineProps<{
  status: AuthStatus | null;
  authorizeUrl: string | null;
  username: string;
  job: ImportJob | null;
  busy: boolean;
  error: string | null;
}>();

const emit = defineEmits<{
  connect: [];
  disconnect: [];
  runImport: [];
  close: [];
}>();

const connected = computed(() => props.status?.connected === true);

const percent = computed(() => {
  const progress = props.job?.progress;
  if (!progress?.limit) return null;
  return Math.min(100, Math.round((progress.exported / progress.limit) * 100));
});

const eta = computed(() => {
  const seconds = props.job?.progress?.eta_seconds;
  if (!seconds || seconds <= 0) return null;
  if (seconds < 90) return `${Math.round(seconds)}s left`;
  return `${Math.round(seconds / 60)} min left`;
});
</script>

<template>
  <aside class="account material" role="dialog" aria-label="Lichess account">
    <header>
      <span class="eyebrow">Lichess account</span>
      <button type="button" class="close" aria-label="Close" @click="emit('close')">
        &times;
      </button>
    </header>

    <template v-if="!connected">
      <p class="lead">
        Signing in triples the export rate and lifts the ceiling that stops an anonymous
        import partway through a large history.
      </p>
      <dl class="rates">
        <div><dt>Now</dt><dd class="num">20 games/s</dd></div>
        <div><dt>Signed in</dt><dd class="num">60 games/s</dd></div>
      </dl>

      <template v-if="authorizeUrl">
        <a class="primary" :href="authorizeUrl" target="_blank" rel="noopener noreferrer">
          Authorize on lichess
        </a>
        <p class="note">
          Opens lichess in a new tab. Approve there and you will land back here signed in.
          FiftyMoves never sees your password.
        </p>
      </template>
      <button v-else type="button" class="primary" :disabled="busy" @click="emit('connect')">
        {{ busy ? "Preparing" : "Sign in with lichess" }}
      </button>
    </template>

    <template v-else>
      <p class="lead">
        Signed in<template v-if="status?.username"> as {{ status.username }}</template>. Exports
        run at {{ status?.export_rate ?? 60 }} games per second.
      </p>

      <button
        type="button"
        class="primary"
        :disabled="busy || job?.state === 'running' || job?.state === 'queued'"
        @click="emit('runImport')"
      >
        Import all of {{ username }}'s games
      </button>

      <div v-if="job && job.state !== 'done'" class="progress">
        <div class="track"><span :style="{ width: `${percent ?? 8}%` }" /></div>
        <p class="note num">
          <template v-if="job.progress">
            {{ job.progress.exported }} exported<template v-if="job.progress.limit">
              of {{ job.progress.limit }}</template>
            <template v-if="eta"> &middot; {{ eta }}</template>
          </template>
          <template v-else>Queued</template>
        </p>
      </div>

      <p v-else-if="job?.result" class="done">
        Imported {{ job.result.usable }} games in
        {{ Math.round(job.result.seconds / 60) }} min. Reload the graph to use them.
      </p>

      <button type="button" class="quiet" @click="emit('disconnect')">Forget this token</button>
    </template>

    <p v-if="error" class="failed">{{ error }}</p>

    <button type="button" class="dismiss" @click="emit('close')">
      Close <span class="key">Esc</span>
    </button>
  </aside>
</template>

<style scoped>
.account {
  width: 268px;
  padding: 14px;
  display: grid;
  gap: 11px;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.close {
  width: 26px;
  height: 26px;
  font-size: 19px;
  line-height: 1;
  color: var(--muted);
  border-radius: 6px;
}
.close:hover {
  color: var(--text);
  background: var(--raised);
}
.lead {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--muted);
}
.rates {
  margin: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px 8px;
  padding: 9px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}
.rates div {
  display: grid;
  gap: 1px;
}
dt {
  font-size: 10px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--faint);
}
dd {
  margin: 0;
  font-size: 13px;
  color: var(--text);
}
.primary {
  display: block;
  padding: 7px 10px;
  background: var(--accent);
  border: none;
  border-radius: var(--r-control);
  font-size: 12px;
  font-weight: 590;
  text-align: center;
  text-decoration: none;
  color: #14101f;
  transition: filter 0.15s var(--ease);
}
.primary:hover:not(:disabled) {
  filter: brightness(1.12);
}
.primary:disabled {
  background: var(--accent-sunk);
  color: var(--faint);
  cursor: default;
}
.note {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--faint);
}
.progress {
  display: grid;
  gap: 5px;
}
.track {
  height: 4px;
  border-radius: 2px;
  background: var(--sunken);
  overflow: hidden;
}
.track span {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: var(--accent);
  transition: width 0.4s var(--ease);
}
.done {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text);
}
.quiet {
  font-size: 10.5px;
  color: var(--faint);
  justify-self: start;
}
.quiet:hover {
  color: var(--amber);
  text-decoration: underline;
}
.failed {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--amber);
}
.dismiss {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 6px;
  margin-top: 1px;
  border-top: 1px solid var(--line);
  font-size: 11px;
  color: var(--muted);
}
.dismiss:hover {
  color: var(--text);
}
.key {
  padding: 1px 5px;
  background: var(--sunken);
  border: 1px solid var(--line);
  border-radius: 4px;
  font-family: var(--mono);
  font-size: 9.5px;
  color: var(--faint);
}
</style>
