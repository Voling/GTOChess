<script setup lang="ts">
import { onMounted, ref } from "vue";

defineProps<{
  busy: boolean;
  error: string | null;
  configured: boolean;
}>();

const emit = defineEmits<{ start: [] }>();

// One line from a real repertoire, written the way a scoresheet is. The mark on
// move 2 and the figures beside it are what the product produces; nothing here
// is a claim the engine did not make.
const LINE = [
  { no: 1, white: "e4", black: "d5", mark: "" },
  { no: 2, white: "Nf3", black: "", mark: "?" },
];

const revealed = ref(false);
onMounted(() => requestAnimationFrame(() => (revealed.value = true)));
</script>

<template>
  <div class="landing" :class="{ in: revealed }">
    <!-- The product itself, behind the words. Faded out to the left so the copy
         stays readable, whole on the right where it is the thing being
         described. Decorative, so it is hidden from assistive tech. -->
    <div class="shot" aria-hidden="true" />

    <main>
      <section class="say">
        <p class="eyebrow">GTO Chess</p>
        <h1>Fix your opening book.</h1>
        <p class="lede">
          Walk through your opening mistakes with LLM analysis backed by
          Stockfish.
        </p>

        <p class="hard">Not a chess coach.</p>

        <section class="sheet" aria-label="An example of a marked line">
          <header>
            <span class="eyebrow">Scandinavian Defense</span>
            <span class="eyebrow dim">47 games</span>
          </header>

          <ol>
            <li v-for="row in LINE" :key="row.no">
              <span class="no">{{ row.no }}</span>
              <span class="ply" :class="{ flawed: row.mark }">
                {{ row.white
                }}<span v-if="row.mark" class="mark">{{ row.mark }}</span>
              </span>
              <span class="ply">{{ row.black }}</span>
            </li>
          </ol>

          <footer>
            <div class="row"><span>engine prefers</span><b class="best">exd5</b></div>
            <div class="row"><span>you give up</span><b class="loss">1.74</b></div>
            <div class="row total">
              <span>across 47 games</span><b class="cost">82 pawns</b>
            </div>
          </footer>
        </section>

        <div class="act">
          <p class="soon">Coming soon</p>
          <button
            v-if="configured"
            type="button"
            class="go"
            :disabled="busy"
            @click="emit('start')"
          >
            {{ busy ? "Opening…" : "Sign in" }}
          </button>
          <p v-else class="note warn">
            No user pool is configured, so there is nothing to sign in to.
          </p>
          <p v-if="error" class="note bad" role="alert">{{ error }}</p>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.landing {
  position: absolute;
  inset: 0;
  overflow-y: auto;
  display: grid;
  align-items: center;
  padding: 40px clamp(24px, 6vw, 88px);
}

.shot {
  position: absolute;
  inset: 0;
  background: url("../assets/tool.jpg") no-repeat right center / cover;
  /* Two layers do the fade. The mask thins the image itself towards the left,
     and the gradient over it keeps the text side dark enough to read against. */
  -webkit-mask-image: linear-gradient(
    to right,
    transparent 0%,
    rgba(0, 0, 0, 0.05) 22%,
    rgba(0, 0, 0, 0.6) 46%,
    #000 66%
  );
  mask-image: linear-gradient(
    to right,
    transparent 0%,
    rgba(0, 0, 0, 0.05) 22%,
    rgba(0, 0, 0, 0.6) 46%,
    #000 66%
  );
  pointer-events: none;
}
.shot::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to right,
    var(--bg) 0%,
    rgba(22, 22, 22, 0.88) 28%,
    rgba(22, 22, 22, 0.28) 48%,
    transparent 64%
  );
}

main {
  position: relative;
  width: min(1120px, 100%);
  margin: 0 auto;
}

.say {
  max-width: 30rem;
}

/* --- the claim ---------------------------------------------------------- */
.eyebrow {
  margin: 0;
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--faint);
}

/* The display face is the mono. Chess notation is a grid by nature, so the
   headline is set in the same face the moves are. */
h1 {
  margin: 16px 0 0;
  max-width: 12ch;
  font-family: var(--mono);
  font-size: clamp(32px, 4.6vw, 48px);
  font-weight: 500;
  line-height: 1.08;
  letter-spacing: -0.03em;
  color: var(--text);
}

.lede {
  margin: 20px 0 0;
  max-width: 34ch;
  font-size: 15px;
  line-height: 1.6;
  color: var(--muted);
}

.hard {
  margin: 14px 0 0;
  font-family: var(--mono);
  font-size: 13px;
  letter-spacing: -0.01em;
  color: var(--amber);
}

.act {
  margin-top: 26px;
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.soon {
  margin: 0;
  padding: 5px 10px;
  border: 1px solid rgba(201, 133, 0, 0.45);
  border-radius: 999px;
  background: rgba(201, 133, 0, 0.1);
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.13em;
  text-transform: uppercase;
  color: #c98500;
}
.go {
  padding: 9px 20px;
  border-radius: var(--r-control);
  border: 1px solid var(--accent);
  background: var(--accent-sunk);
  font-size: 13px;
  color: var(--accent-bright);
  transition:
    background 0.16s var(--ease),
    color 0.16s var(--ease);
}
.go:hover:not(:disabled) {
  background: var(--accent);
  color: #fff;
}
.go:disabled {
  opacity: 0.55;
  cursor: default;
}
.note {
  margin: 0;
  flex-basis: 100%;
  font-size: 11px;
  line-height: 1.5;
  color: var(--faint);
}
.note.bad {
  color: #e66767;
}
.note.warn {
  color: var(--amber);
}

/* --- the scoresheet ----------------------------------------------------- */
.sheet {
  margin-top: 24px;
  max-width: 21rem;
  padding: 16px 18px 14px;
  background: rgba(0, 0, 0, 0.42);
  border: 1px solid var(--line);
  border-radius: var(--r-panel);
  box-shadow: var(--hairline);
  backdrop-filter: blur(6px);
}
.sheet header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}
.dim {
  color: rgba(107, 107, 107, 0.7);
}

.sheet ol {
  margin: 0;
  padding: 4px 0 0;
  list-style: none;
}
.sheet li {
  display: grid;
  grid-template-columns: 26px 1fr 1fr;
  align-items: baseline;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}
.no {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--faint);
}
.ply {
  font-family: var(--mono);
  font-size: 17px;
  letter-spacing: -0.01em;
  color: var(--text);
}
.ply.flawed {
  color: #d95926;
}

.sheet footer {
  margin-top: 11px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
  display: grid;
  gap: 6px;
}
.row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: 11px;
  color: var(--faint);
}
.row b {
  font-family: var(--mono);
  font-size: 13px;
  font-weight: 500;
}
.best {
  color: var(--muted);
}
.loss {
  color: var(--amber);
}
.row.total {
  margin-top: 2px;
  padding-top: 8px;
  border-top: 1px dashed var(--line);
}
.cost {
  color: var(--accent-bright);
}

/* One moment, and it is the product's: the mark lands on the move a beat after
   you have read it. Everything else is simply present. */
.mark {
  margin-left: 3px;
  font-weight: 700;
  color: #d95926;
  opacity: 0;
  transition: opacity 0.4s var(--ease) 0.55s;
}
.landing.in .mark {
  opacity: 1;
}

@media (prefers-reduced-motion: reduce) {
  .mark {
    opacity: 1;
    transition: none;
  }
}

/* Narrow: the screenshot behind the words stops being a backdrop and becomes
   noise, so it goes. */
@media (max-width: 780px) {
  .shot {
    display: none;
  }
  .say {
    max-width: none;
  }
  .landing {
    align-items: start;
  }
}
</style>
