<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";

defineProps<{
  busy: boolean;
  error: string | null;
  configured: boolean;
}>();

const emit = defineEmits<{ submit: [email: string, password: string] }>();

const open = ref(false);
const notice = ref(false);
const email = ref("");
const password = ref("");
const first = ref<HTMLInputElement | null>(null);

const CARDS = [
  {
    title: "Every line you have played",
    body: "Your lichess games, replayed move by move into one graph. A branch takes as much of the circle as it took of your games, and lines that transpose join rather than repeat.",
  },
  {
    title: "Priced by Stockfish",
    body: "The engine scores every reply you chose against its own. Marks land at 90, 160 and 300 centipawns, well above the middlegame convention: sound opening moves differ by tens of centipawns, and a lower floor would flag your whole book.",
  },
  {
    title: "What the flaws actually cost",
    body: "Your record, not the engine's opinion. Flagged moves are scored against your own sound rate, so what comes back is a gap in points per hundred games, plus the move your preparation stops holding at.",
  },
  {
    title: "Nothing claimed without evidence",
    body: "Topics are picked by taking pieces off the board and handing over a tempo, to see what the position turns on. The model reads that and can ask Stockfish about a line. A claim citing nothing is dropped before it reaches you.",
  },
];

// The benefits arrive as they are scrolled to, rather than being animated on
// load where nobody is looking at them yet.
const page = ref<HTMLElement | null>(null);
const below = ref<HTMLElement | null>(null);
// Hidden only once something is definitely going to reveal it. Starting at
// opacity 0 in the stylesheet would leave the text invisible for good if the
// observer never ran, and a hidden tab is enough to stop it running.
const armed = ref(false);
const arrived = ref(false);
let watcher: IntersectionObserver | undefined;

onMounted(() => {
  if (!below.value || !("IntersectionObserver" in window)) return;
  armed.value = true;
  watcher = new IntersectionObserver(
    ([entry]) => {
      if (!entry.isIntersecting) return;
      arrived.value = true;
      watcher?.disconnect();
    },
    { rootMargin: "-12% 0px" },
  );
  watcher.observe(below.value);
});

onBeforeUnmount(() => watcher?.disconnect());

function toBenefits() {
  // The scroller is the panel, not the document, so scrollIntoView would have
  // to guess. This tells it exactly where to go.
  const scroller = page.value;
  const target = below.value;
  if (!scroller || !target) return;
  arrived.value = true;
  scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
}

async function reveal() {
  open.value = true;
  await nextTick();
  first.value?.focus();
}

function send() {
  if (email.value && password.value)
    emit("submit", email.value, password.value);
}
</script>

<template>
  <div ref="page" class="landing">
    <header>
      <p class="eyebrow">GTO Chess</p>
      <nav>
        <button type="button" class="ghost" @click="notice = true">
          Register
        </button>
        <button v-if="configured" type="button" class="go" @click="reveal">
          Sign in
        </button>
      </nav>
    </header>

    <!-- Registration is not open yet, so the button says so rather than being
         disabled with no explanation. -->
    <p v-if="notice" class="banner" role="status">
      <span><b>Coming soon.</b> Accounts are invitation only for now.</span>
      <button
        type="button"
        class="shut"
        aria-label="Dismiss"
        @click="notice = false"
      >
        &times;
      </button>
    </p>

    <section class="hero">
      <!-- Every line the player actually reaches, marked where the engine
           disagrees. Decorative here, so hidden from assistive tech. -->
      <div class="tree" aria-hidden="true" />

      <div class="say">
        <h1>Fix your opening&nbsp;book.</h1>
        <p class="lede">
          Walk through your opening mistakes with LLM analysis backed by
          Stockfish.
        </p>
        <p class="hard">Not a chess coach.</p>

        <button type="button" class="more" @click="toBenefits">
          <span>What it does</span>
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <path
              d="M8 3v9M4 8.5 8 12.5l4-4"
              fill="none"
              stroke="currentColor"
              stroke-width="1.4"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
      </div>
    </section>

    <section
      ref="below"
      class="benefits"
      :class="{ armed, arrived }"
      aria-label="What it does"
    >
      <div class="grid">
        <article v-for="item in CARDS" :key="item.title">
          <h2>{{ item.title }}</h2>
          <p>{{ item.body }}</p>
        </article>
      </div>
    </section>

    <div v-if="open" class="veil" @click.self="open = false">
      <form class="card" @submit.prevent="send">
        <h2>Sign in</h2>
        <label>
          <span>Email</span>
          <input
            ref="first"
            v-model="email"
            type="email"
            autocomplete="username"
            required
          />
        </label>
        <label>
          <span>Password</span>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
          />
        </label>

        <p v-if="error" class="bad" role="alert">{{ error }}</p>

        <button type="submit" class="go wide" :disabled="busy">
          {{ busy ? "Signing in…" : "Sign in" }}
        </button>
        <button type="button" class="link" @click="open = false">Cancel</button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.landing {
  position: absolute;
  inset: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

header::before {
  content: "";
  position: absolute;
  inset: -8px 0 -28px;
  background: linear-gradient(to bottom, var(--bg) 30%, transparent);
  pointer-events: none;
}
header {
  position: relative;
  z-index: 3;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 22px clamp(24px, 5vw, 64px);
}
nav {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
}

.eyebrow {
  position: relative;
  margin: 0;
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--faint);
}

.banner {
  position: relative;
  z-index: 2;
  margin: 0 clamp(24px, 5vw, 64px);
  padding: 9px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid rgba(201, 133, 0, 0.4);
  border-radius: var(--r-control);
  background: rgba(201, 133, 0, 0.08);
  font-size: 12px;
  color: var(--amber);
}
.banner b {
  font-weight: 600;
}

/* --- hero ---------------------------------------------------------------- */
/* One screen, so the benefits are something you scroll to rather than something
   already half visible. dvh so a mobile address bar does not crop it. */
.hero {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  margin-top: -84px;
  display: grid;
  align-items: center;
  padding: 84px clamp(24px, 5vw, 64px) 0;
}

/* Sized against the viewport height as well as its width, so the circle is whole
   rather than cropped by the top and bottom of the screen. */
.tree {
  position: absolute;
  top: 50%;
  right: clamp(-6rem, -3vw, -1rem);
  width: min(58vw, 82vh, 62rem);
  aspect-ratio: 1;
  transform: translateY(-50%);
  background: url("../assets/tool.jpg") center / contain no-repeat;
  -webkit-mask-image: linear-gradient(
    to right,
    transparent 4%,
    rgba(0, 0, 0, 0.35) 30%,
    #000 62%
  );
  mask-image: linear-gradient(
    to right,
    transparent 4%,
    rgba(0, 0, 0, 0.35) 30%,
    #000 62%
  );
  pointer-events: none;
}

.say {
  position: relative;
  width: min(1400px, 100%);
  margin: 0 auto;
}

/* The display face is the mono. Chess notation is a grid by nature, so the
   headline is set in the same face the moves on the graph are. */
h1 {
  margin: 0;
  max-width: 11ch;
  font-family: var(--mono);
  font-size: clamp(34px, 5.2vw, 58px);
  font-weight: 500;
  line-height: 1.04;
  letter-spacing: -0.035em;
  color: #f2f2f2;
}
.lede {
  margin: 22px 0 0;
  max-width: 34ch;
  font-size: 15.5px;
  line-height: 1.6;
  color: var(--muted);
}
.hard {
  margin: 10px 0 0;
  font-family: var(--mono);
  font-size: 13px;
  letter-spacing: -0.01em;
  color: var(--amber);
}

.more {
  margin-top: 40px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--faint);
  transition: color 0.16s var(--ease);
}
.more:hover {
  color: var(--text);
}
.more svg {
  transition: transform 0.16s var(--ease);
}
.more:hover svg {
  transform: translateY(2px);
}

/* --- benefits ------------------------------------------------------------ */
.benefits {
  position: relative;
  z-index: 1;
}
.benefits.armed {
  opacity: 0;
  transform: translateY(14px);
  transition:
    opacity 0.5s var(--ease),
    transform 0.5s var(--ease);
}
.benefits.armed.arrived {
  opacity: 1;
  transform: none;
}
.benefits {
  width: min(1400px, 100%);
  margin: 0 auto;
  padding: clamp(32px, 5vh, 64px) clamp(24px, 5vw, 64px) clamp(56px, 9vh, 104px);
}
.benefits .grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  gap: 1px;
  /* The gap shows this through, which is what draws the hairlines between
     cells without a border on each one. */
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--r-panel);
  overflow: hidden;
}
.benefits article {
  padding: 22px clamp(18px, 2vw, 26px);
  background: rgba(22, 22, 22, 0.82);
  backdrop-filter: blur(8px);
}
.benefits h2 {
  margin: 0;
  font-family: var(--mono);
  font-size: 13.5px;
  font-weight: 500;
  letter-spacing: -0.01em;
  color: var(--text);
}
.benefits p {
  margin: 9px 0 0;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--faint);
}

/* --- buttons ------------------------------------------------------------- */
.go {
  padding: 8px 18px;
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
.go.wide {
  width: 100%;
  padding: 10px 0;
  margin-top: 4px;
}
.ghost {
  padding: 8px 16px;
  border-radius: var(--r-control);
  border: 1px solid var(--line-strong);
  font-size: 13px;
  color: var(--muted);
  transition:
    border-color 0.16s var(--ease),
    color 0.16s var(--ease);
}
.ghost:hover {
  border-color: var(--faint);
  color: var(--text);
}
.link {
  font-size: 12px;
  color: var(--faint);
}
.link:hover {
  color: var(--text);
  text-decoration: underline;
}
.shut {
  flex: none;
  font-size: 15px;
  line-height: 1;
  color: inherit;
  opacity: 0.7;
}
.shut:hover {
  opacity: 1;
}

/* --- the form ------------------------------------------------------------ */
.veil {
  position: fixed;
  inset: 0;
  z-index: 10;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(10, 10, 10, 0.72);
  backdrop-filter: blur(3px);
}
.card {
  width: min(23rem, 100%);
  padding: 22px;
  display: grid;
  gap: 12px;
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-panel);
  box-shadow: var(--shadow);
  backdrop-filter: blur(18px);
}
.card h2 {
  margin: 0 0 2px;
  font-family: var(--mono);
  font-size: 15px;
  font-weight: 500;
  color: var(--text);
}
label {
  display: grid;
  gap: 5px;
}
label span {
  font-size: 11px;
  color: var(--faint);
}
input {
  padding: 8px 10px;
  border-radius: var(--r-control);
  border: 1px solid var(--line-strong);
  background: var(--sunken);
  font-size: 13px;
  color: var(--text);
}
input:focus-visible {
  outline: none;
  border-color: var(--accent);
}
.bad {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.5;
  color: #e66767;
}

/* Narrow: the graph moves behind the words as a faint wash, because there is no
   longer a right hand side to put it on. */
@media (prefers-reduced-motion: reduce) {
  .benefits.armed {
    opacity: 1;
    transform: none;
    transition: none;
  }
}

@media (max-width: 820px) {
  .tree {
    right: 50%;
    transform: translate(50%, -50%);
    width: min(130vw, 40rem);
    opacity: 0.26;
    -webkit-mask-image: radial-gradient(circle, #000 35%, transparent 72%);
    mask-image: radial-gradient(circle, #000 35%, transparent 72%);
  }
  h1,
  .lede {
    max-width: none;
  }
}
</style>
