// ---------------------------------------------------------------------------
// 엘리먼트 참조
// ---------------------------------------------------------------------------

const overlays = {
  idle: document.getElementById("overlay-idle"),
  waiting: document.getElementById("overlay-waiting"),
  committed: document.getElementById("overlay-committed"),
  roulette: document.getElementById("overlay-roulette"),
  podium: document.getElementById("overlay-podium"),
};
const bodyEl = document.body;
const statusEl = document.getElementById("ws-status");
const participantCountEl = document.getElementById("participant-count");
const departmentCountEl = document.getElementById("department-count");
const commitBadgeEl = document.getElementById("commit-badge");
const reelEl = document.getElementById("reel");
const winnerListEl = document.getElementById("winner-list");
const mcCaptionEl = document.getElementById("mc-caption");
const roundPillEl = document.getElementById("round-pill");
const phaseLabelEl = document.getElementById("phase-label");
const phaseTimerEl = document.getElementById("phase-timer");
const departmentBarsEl = document.getElementById("department-bars");
const positionListEl = document.getElementById("position-list");
const predictionLeaderboardEl = document.getElementById("prediction-leaderboard");
const predictionLeaderboardListEl = document.getElementById("prediction-leaderboard-list");
const raceCanvas = document.getElementById("race-canvas");
const raceCtx = raceCanvas.getContext("2d");
const fxCanvas = document.getElementById("fx-canvas");
const overtakeLayer = document.getElementById("overtake-layer");
const overlayLightsEl = document.getElementById("overlay-lights");
const lightsCaptionEl = document.getElementById("lights-caption");
const overlayBannerEl = document.getElementById("overlay-banner");
const bannerTextEl = document.getElementById("banner-text");
const bannerSubEl = document.getElementById("banner-sub");
const podiumStageEl = document.getElementById("podium-stage");
const podiumRestEl = document.getElementById("podium-rest");
const podiumBasisHintEl = document.getElementById("podium-basis-hint");
const btnSound = document.getElementById("btn-sound");
const btnVoice = document.getElementById("btn-voice");
const btnFullscreen = document.getElementById("btn-fullscreen");

FX.attach(fxCanvas, document.getElementById("scene"));

let lastPredictionWindow = null;

const animatedDrawKeys = new Set();
let lastOpeningShownFor = null;
let lastFinalShownFor = null;
let racingStarted = false;
let countdownTimer = null;
let latestDepartmentRates = {};
let currentPhase = null;

// -- 오버레이 스위칭 --------------------------------------------------------

// 오버레이(화면)별 배경음악 장면 매핑. 레이싱 구간(overlay 없음, hideAllOverlays)
// 은 handleRacingEvent의 phase 분기에서 별도로 처리한다.
const OVERLAY_BGM_SCENE = {
  idle: "idle", // 명단조차 없는 초기 화면 -- 가장 조용한 패드
  waiting: "idle", // 명단 등록 완료, 추첨 시작 대기
  committed: "standby", // 커밋 완료 -- 곧 출발한다는 긴장감 있는 대기 루프
  roulette: "roulette",
  podium: "victory",
};

// 레이스 라운드별 BGM. 라운드마다 곡 자체가 달라야 "단계가 올라간다"는
// 감각이 생긴다(audio.js의 race1/2/3 참고).
const RACE_BGM_SCENE = { 1: "race1", 2: "race2", 3: "race3" };

function showOverlay(name) {
  for (const key of Object.keys(overlays)) {
    overlays[key].classList.toggle("hidden", key !== name);
  }
  bodyEl.dataset.mode = name || "racing";
  const scene = OVERLAY_BGM_SCENE[name];
  if (scene) SFX.playScene(scene);
}

function hideAllOverlays() {
  for (const key of Object.keys(overlays)) overlays[key].classList.add("hidden");
  bodyEl.dataset.mode = "racing";
}

// ---------------------------------------------------------------------------
// 사운드/음성/전체화면 컨트롤
// ---------------------------------------------------------------------------

let soundOn = true;
let voiceOn = true;
let audioUnlocked = false;
const ttsSupported = typeof window !== "undefined" && "speechSynthesis" in window;
let koVoice = null;

function pickKoreanVoice() {
  if (!ttsSupported) return;
  const voices = window.speechSynthesis.getVoices();
  const koVoices = voices.filter((v) => v.lang && v.lang.toLowerCase().startsWith("ko"));
  // 로컬 OS 내장 음성(예: Windows "Heami")은 대체로 단조롭다. 브라우저가
  // 함께 제공하는 네트워크 기반 음성(예: Chrome의 "Google 한국어")은
  // 억양이 더 자연스러운 경우가 많아 있으면 우선한다.
  koVoice =
    koVoices.find((v) => /google/i.test(v.name)) ||
    koVoices.find((v) => !v.localService) ||
    koVoices[0] ||
    null;
}
if (ttsSupported) {
  pickKoreanVoice();
  window.speechSynthesis.onvoiceschanged = pickKoreanVoice;
}

// -- 상황 태그별 말투 프로파일 --------------------------------------------
// TTS 엔진 자체는 감정을 모르기 때문에, 상황마다 속도/피치를 다르게 줘서
// "그냥 글 읽는 AI" 느낌을 줄인다. 긴장/액션 상황은 빠르고 높게, 차분한
// 안내는 느리고 낮게. 같은 태그가 반복돼도 매번 살짝 다르게 들리도록
// 작은 무작위 지터를 더한다(완전히 똑같은 억양의 반복은 그 자체로 기계적
// 으로 들린다).
const MC_ENERGY = {
  opening: { rate: 1.0, pitch: 1.0 },
  countdown: { rate: 1.18, pitch: 1.1 },
  race_progress: { rate: 1.12, pitch: 1.06 },
  close_call: { rate: 1.22, pitch: 1.14 },
  final_lap: { rate: 1.24, pitch: 1.14 },
  photo_finish: { rate: 1.28, pitch: 1.18 },
  department_rank_shift: { rate: 1.14, pitch: 1.08 },
  round_pass_announce: { rate: 1.06, pitch: 1.04 },
  elimination: { rate: 0.94, pitch: 0.96 },
  prediction_open: { rate: 1.06, pitch: 1.04 },
  ability_trigger: { rate: 1.16, pitch: 1.1 },
  prediction_result: { rate: 1.12, pitch: 1.08 },
  prediction_champion: { rate: 1.1, pitch: 1.08 },
  final_announce: { rate: 1.08, pitch: 1.06 },
  podium: { rate: 1.0, pitch: 1.04 },
  verification: { rate: 0.96, pitch: 0.99 },
};
const MC_ENERGY_DEFAULT = { rate: 1.05, pitch: 1.02 };

function speak(text, tag) {
  if (!ttsSupported || !voiceOn || !text) return;
  try {
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = "ko-KR";
    if (koVoice) utter.voice = koVoice;
    const energy = MC_ENERGY[tag] || MC_ENERGY_DEFAULT;
    const jitter = () => (Math.random() - 0.5) * 0.07;
    utter.rate = Math.min(1.5, Math.max(0.8, energy.rate + jitter()));
    utter.pitch = Math.min(1.6, Math.max(0.75, energy.pitch + jitter()));
    // 느낌표로 끝나는 절규/환호 문구는 살짝 더 크게 -- 평서문과 대비를 준다
    utter.volume = /[!]\s*$/.test(text.trim()) ? 1.0 : 0.92;
    utter.onstart = () => SFX.duck();
    utter.onend = () => SFX.unduck();
    utter.onerror = () => SFX.unduck();
    window.speechSynthesis.speak(utter);
  } catch (e) {
    /* TTS 실패해도 자막은 이미 표시됨 -- 진행에 영향 없음 */
  }
}

function unlockAudioOnce() {
  if (audioUnlocked) return;
  audioUnlocked = true;
  SFX.unlock();
  updateControlButtons();
}
window.addEventListener("pointerdown", unlockAudioOnce, { once: true });
window.addEventListener("keydown", unlockAudioOnce, { once: true });

function updateControlButtons() {
  btnSound.textContent = soundOn ? "🔊" : "🔇";
  btnSound.classList.toggle("active", soundOn);
  btnVoice.textContent = voiceOn ? "🎙" : "🚫";
  btnVoice.classList.toggle("active", voiceOn && ttsSupported);
  if (!ttsSupported) {
    btnVoice.disabled = true;
    btnVoice.title = "이 브라우저는 음성 합성을 지원하지 않습니다";
  }
}
updateControlButtons();

btnSound.addEventListener("click", () => {
  unlockAudioOnce();
  soundOn = !soundOn;
  SFX.setEnabled(soundOn);
  updateControlButtons();
});

btnVoice.addEventListener("click", () => {
  voiceOn = !voiceOn;
  if (!voiceOn && ttsSupported) window.speechSynthesis.cancel();
  updateControlButtons();
});

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen?.().catch(() => {});
  } else {
    document.exitFullscreen?.().catch(() => {});
  }
}
btnFullscreen.addEventListener("click", toggleFullscreen);

window.addEventListener("keydown", (e) => {
  if (e.target && ["INPUT", "TEXTAREA"].includes(e.target.tagName)) return;
  if (e.key === "f" || e.key === "F") toggleFullscreen();
  if (e.key === "s" || e.key === "S") btnSound.click();
  if (e.key === "v" || e.key === "V") btnVoice.click();
});

// ---------------------------------------------------------------------------
// MC 자막 + 음성
// ---------------------------------------------------------------------------

// MC 멘트는 네트워크 왕복(멘트 조회) 뒤에 재생되므로, 요청을 보낸 뒤
// 도착하기 전에 장면이 바뀌면 "이미 끝난 레이스를 계속 중계하는" 상황이
// 벌어진다(사용자 신고). 장면이 바뀔 때마다 세대(epoch)를 올리고, 응답이
// 늦게 도착한 이전 세대의 멘트는 자막·음성 모두 조용히 버린다.
let mcEpoch = 0;

function cancelPendingMcLines({ clearCaption = false } = {}) {
  mcEpoch += 1;
  lastMcLiveCallAt = 0;
  if (ttsSupported) {
    try {
      window.speechSynthesis.cancel();
    } catch (e) {
      /* 취소 실패는 무시 -- 다음 발화가 어차피 cancel()로 시작한다 */
    }
  }
  if (clearCaption) mcCaptionEl.textContent = "";
}

async function showMcLine(tag, params) {
  const epoch = mcEpoch;
  try {
    const qs = params
      ? "?" +
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== null)
          .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
          .join("&")
      : "";
    const result = await fetchJSON(`/api/mc/line/${tag}${qs}`);
    if (epoch !== mcEpoch) return ""; // 그 사이 장면이 바뀌었다 -- 흘러간 멘트는 버린다
    if (result.text) {
      mcCaptionEl.textContent = `"${result.text}"`;
      speak(result.text, tag);
    } else {
      mcCaptionEl.textContent = "";
    }
    return result.text;
  } catch (e) {
    if (epoch === mcEpoch) mcCaptionEl.textContent = "";
    return "";
  }
}

// 레이스 도중 이벤트(선두 교체·추월)마다 매번 호출하면 자막이 정신없이
// 바뀌므로, 최소 간격을 두고 그 사이 이벤트는 걸러낸다.
const MC_LIVE_COOLDOWN_MS = 4500;
// 레이스 막판에 실황 멘트를 새로 띄우면, 그 멘트가 도착할 즈음엔 이미
// 결과 발표로 넘어가 있다 -- 진행률이 이 값을 넘으면 실황 멘트를 멈춘다.
const MC_LIVE_STOP_PROGRESS = 0.9;
let lastMcLiveCallAt = 0;
let liveMcSuppressed = false;

function tryShowLiveMcLine(tag, params) {
  if (liveMcSuppressed) return;
  const now = Date.now();
  if (now - lastMcLiveCallAt < MC_LIVE_COOLDOWN_MS) return;
  lastMcLiveCallAt = now;
  showMcLine(tag, params);
}

// ---------------------------------------------------------------------------
// 배너(FINAL LAP / 라운드 발표 / 포토 피니시)
// ---------------------------------------------------------------------------

let bannerHideTimer = null;
function showBanner(text, sub = "", ms = 1800) {
  if (bannerHideTimer) clearTimeout(bannerHideTimer);
  bannerTextEl.textContent = text;
  bannerSubEl.textContent = sub;
  overlayBannerEl.classList.remove("hidden");
  // 타이머가 실행된 뒤에도 변수를 null로 되돌려야 한다 -- 그러지 않으면
  // render()의 "새로고침 등으로 재진입" 복구 분기(시상대 등)가
  // bannerHideTimer가 null인 걸 "아직 아무 배너도 안 뜬 상태"의
  // 신호로 쓰는데, 라운드 배너 한 번만 떠도 그 이후로 영원히 non-null로
  // 남아 그 분기가 죽은 코드가 되던 문제가 있었다.
  bannerHideTimer = setTimeout(() => {
    overlayBannerEl.classList.add("hidden");
    bannerHideTimer = null;
  }, ms);
}

// ---------------------------------------------------------------------------
// F1 스타트 라이트 시퀀스
// ---------------------------------------------------------------------------

const lightEls = Array.from(document.querySelectorAll(".light"));
const shownLightsForRound = new Set();

async function runStartLights(roundIndex) {
  const key = `${currentDrawKeyForGroups}:${roundIndex}`;
  if (shownLightsForRound.has(key)) return;
  shownLightsForRound.add(key);

  overlayLightsEl.classList.remove("hidden");
  lightsCaptionEl.textContent = `ROUND ${roundIndex} -- GET READY`;
  for (const el of lightEls) el.classList.remove("on", "go");

  for (let i = 0; i < lightEls.length; i++) {
    await delay(300);
    lightEls[i].classList.add("on");
    SFX.startLight(i);
  }
  await delay(550);
  for (const el of lightEls) el.classList.remove("on");
  for (const el of lightEls) el.classList.add("go");
  lightsCaptionEl.textContent = "GO!!";
  SFX.lightsOut();
  FX.screenFlash("rgba(255,255,255,0.9)", 260);
  FX.screenShake(9, 320);
  await delay(500);
  overlayLightsEl.classList.add("hidden");
}

// ---------------------------------------------------------------------------
// 룰렛 시퀀스
// ---------------------------------------------------------------------------

async function spinReel(el, pool, finalText, totalMs) {
  const start = Date.now();
  while (Date.now() - start < totalMs) {
    el.textContent = pool[Math.floor(Math.random() * pool.length)];
    SFX.tick(false);
    const elapsed = Date.now() - start;
    const interval = 40 + Math.pow(elapsed / totalMs, 2) * 260;
    await delay(interval);
  }
  el.textContent = finalText;
}

async function playRouletteSequence(draw) {
  const pool = draw.snapshot.participants.map((p) => participantLabel(p));
  const nameById = Object.fromEntries(
    draw.snapshot.participants.map((p) => [p.id, participantLabel(p)])
  );
  showOverlay("roulette");
  winnerListEl.innerHTML = "";
  for (const winnerId of draw.winners) {
    SFX.drumroll(1.6);
    await spinReel(reelEl, pool, nameById[winnerId] || winnerId, 1800);
    SFX.pop();
    FX.burst(window.innerWidth / 2, window.innerHeight * 0.35, "#ffd166", 26);
    const li = document.createElement("li");
    li.textContent = nameById[winnerId] || winnerId;
    winnerListEl.appendChild(li);
    await delay(500);
  }
  reelEl.textContent = "🎊 추첨 완료!";
  SFX.fanfare();
  SFX.playScene("victory"); // 룰렛 모드는 podium 오버레이가 따로 없어 여기서 직접 전환
  FX.confetti(160);
}

// ---------------------------------------------------------------------------
// 레이싱 모드: 부서 통과율 실시간 랭킹 + 라운드 진행 상태머신
// ---------------------------------------------------------------------------

function renderPredictionLeaderboard(top) {
  if (!top || !top.length) return;
  predictionLeaderboardEl.classList.remove("hidden");
  const titleEl = predictionLeaderboardEl.querySelector("h3");
  if (titleEl) titleEl.textContent = "예측 리더보드";
  predictionLeaderboardListEl.innerHTML = top
    .map((entry) => `<li>${entry.participant_id} - ${entry.score}점</li>`)
    .join("");
}

// ---------------------------------------------------------------------------
// 실시간 선택 분포 패널(우측) -- 선택 창이 열려 있는 동안만 폴링한다.
// "지금 표가 어디로 몰리고 있는가"가 무대 화면에서 바로 보여야, MC가
// "○○ 부서에 표가 쏠립니다!"로 받아칠 수 있다.
// ---------------------------------------------------------------------------

let liveDistributionTimer = null;
const liveDistributionPanelEl = document.getElementById("live-distribution-panel");
const liveDistributionListEl = document.getElementById("live-distribution-list");

async function pollLiveDistribution() {
  try {
    const data = await fetchJSON("/api/predict/live");
    const rounds = data.rounds || {};
    const roundKeys = Object.keys(rounds);
    if (!roundKeys.length) {
      liveDistributionPanelEl.classList.add("hidden");
      return;
    }
    liveDistributionPanelEl.classList.remove("hidden");
    liveDistributionListEl.innerHTML = roundKeys
      .map((r) => {
        const dist = rounds[r].distribution || {};
        const rows = Object.entries(dist)
          .sort((a, b) => b[1] - a[1])
          .map(
            ([target, share]) =>
              `<div class="dist-row"><span>${target}</span><span class="dist-bar"><i style="width:${Math.round(
                share * 100
              )}%"></i></span><span>${Math.round(share * 100)}%</span></div>`
          )
          .join("");
        return `<div class="dist-round-block"><div class="dist-round-title">R${r} 선택 분포</div>${
          rows || '<div class="dist-empty">아직 선택이 없습니다</div>'
        }</div>`;
      })
      .join("");
  } catch (e) {
    // 세션 없음/리셋 직후 등 -- 다음 폴링에서 자연 복구되므로 조용히 무시
  }
}

function startLiveDistributionPolling() {
  if (liveDistributionTimer) return;
  liveDistributionTimer = setInterval(pollLiveDistribution, 2000);
  pollLiveDistribution();
}

function stopLiveDistributionPolling() {
  if (liveDistributionTimer) {
    clearInterval(liveDistributionTimer);
    liveDistributionTimer = null;
  }
  liveDistributionPanelEl.classList.add("hidden");
}

let currentLeaderDept = null;

function renderDepartmentBars(rates) {
  latestDepartmentRates = rates;
  const sorted = Object.entries(rates).sort((a, b) => b[1] - a[1]);
  departmentBarsEl.innerHTML = sorted
    .map(
      ([name, rate]) => `
      <div class="dept-bar-row">
        <div class="dept-bar-label"><span>${name}</span><span>${(rate * 100).toFixed(0)}%</span></div>
        <div class="dept-bar-track"><div class="dept-bar-fill" style="width:${(rate * 100).toFixed(1)}%; background:${colorForDepartment(name)}"></div></div>
      </div>`
    )
    .join("");

  // 선두 부서가 바뀌면(0%끼리의 무의미한 교체는 제외) MC가 반응하도록 한다.
  if (sorted.length && sorted[0][1] > 0) {
    const newLeader = sorted[0][0];
    if (currentLeaderDept !== null && newLeader !== currentLeaderDept) {
      tryShowLiveMcLine("department_rank_shift", { team: newLeader });
      FX.ring(window.innerWidth / 2, 90, colorForDepartment(newLeader), 140);
    }
    currentLeaderDept = newLeader;
  }
}

function hashToUnit(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  return (h % 10000) / 10000; // 0..1
}

// 해시로 색을 만들면 부서 수가 적을 때 비슷한 색조로 몰려 대형 스크린에서
// 구분이 안 된다. 색조·명도가 모두 뚜렷이 갈리는 고정 팔레트를 부서명 정렬
// 순서대로 배정해 항상 최대 대비를 보장한다(같은 명단이면 항상 같은 색).
const TEAM_PALETTE = [
  { base: "#ff5252", glow: "#ff8a80" },
  { base: "#4f8cff", glow: "#82b1ff" },
  { base: "#7cf29c", glow: "#b9f6ca" },
  { base: "#ffd166", glow: "#ffe082" },
  { base: "#c77dff", glow: "#e1bee7" },
  { base: "#ff9f45", glow: "#ffcc80" },
  { base: "#4dd0e1", glow: "#84ffff" },
  { base: "#f06292", glow: "#f8bbd0" },
];

// 팀 특수능력(순수 연출용 -- 공정성 판정에는 어떤 영향도 주지 않는다).
// 팀 색상과 동일한 방식으로 부서명 정렬 순서 인덱스에 고정 배정한다(같은
// 명단이면 항상 같은 능력). 결과 자체를 바꾸지 않고, 이미 서버가 계산한
// 추월/위기/선두교체 순간을 팀마다 다른 이펙트·문구로 "포장"할 뿐이다.
const ABILITY_ROSTER = [
  { id: "nitro", label: "니트로 부스트", emoji: "🔥" },
  { id: "shield", label: "배리어 실드", emoji: "🛡️" },
  { id: "spark", label: "스파크 러시", emoji: "⚡" },
  { id: "draft", label: "슬립스트림", emoji: "🌪️" },
  { id: "lucky", label: "럭키 드래프트", emoji: "💎" },
  { id: "wave", label: "타이달 웨이브", emoji: "🌊" },
  { id: "stardust", label: "스타더스트", emoji: "⭐" },
  { id: "rocket", label: "로켓 대시", emoji: "🚀" },
];

function abilityForDepartment(name) {
  return departmentAbilityCache.get(name) || ABILITY_ROSTER[0];
}

const ABILITY_BY_ID = new Map(ABILITY_ROSTER.map((a) => [a.id, a]));

// 참가자가 모바일에서 직접 고른 캐릭터(app/characters.py와 id 동일) --
// 고르지 않은 참가자는 부서 기반 자동 배정으로 폴백한다. 카트 몸체 색은
// 항상 부서색을 유지하고(팀 우열을 한눈에 보기 위한 정보이므로), 능력
// (이모지 이펙트·MC 문구)만 개인화된다.
let characterChoiceByPid = {};

async function fetchCharacterChoices() {
  try {
    const data = await fetchJSON("/api/character/choices");
    characterChoiceByPid = data.choices || {};
  } catch (e) {
    /* 세션 없음 등 -- 다음 시도에서 자연 복구되므로 조용히 무시 */
  }
}

function abilityForParticipant(pid) {
  const chosenId = characterChoiceByPid[pid];
  const chosen = chosenId && ABILITY_BY_ID.get(chosenId);
  if (chosen) return chosen;
  const group = currentPidToGroup[pid];
  return group ? abilityForDepartment(group) : ABILITY_ROSTER[0];
}

const departmentColorCache = new Map();
const departmentAbilityCache = new Map();
let currentPidToGroup = {};
let currentDrawKeyForGroups = null;
let previousTickPositions = {};
let previousTickOrder = [];
let previousTickRound = null;
let lastLaneCount = 20;
let cameraMode = "auto"; // "auto" | "wide" | "medium" | "close" (admin에서 override 가능)
let roundParticipantsTotal = 0;
let finalLapShownForRound = null;
let photoFinishShownForRound = null;

// 결승(통과)선을 이미 넘은 카트 -- 통과음을 카트당 한 번만 내기 위한 기록.
// 라운드가 바뀌면 그 시점에 이미 선을 넘어 있는 카트를 "소리 없이" 채워
// 넣는다(중간 접속·복구 직후 수십 대의 통과음이 한꺼번에 터지는 것 방지).
let crossedPassLine = new Set();
let crossedRound = null;
let crossedSoundCount = 0;
const CROSS_SOUND_BUDGET = 24; // 라운드당 통과음 최대 횟수(그 뒤는 조용히 지나간다)

// ---------------------------------------------------------------------------
// 장애물 (연출 전용 -- 순위/progress 값에는 절대 영향 없음)
//
// 사용자 요청으로 종류·크기·개수를 늘리고, 부딪히면 스핀아웃/정지/감속처럼
// 눈에 띄는 변수가 생기도록 했다. **다만 실제 통과 판정은 서버가 보낸
// positions[pid]로만 결정되므로, 여기서 만드는 효과는 전부 "보이는 위치"에만
// 적용되는 일시적 오프셋이고 반드시 0으로 되돌아온다**:
//   - 각 효과는 자기 지속시간 안에서 감쇠해 사라진다.
//   - 그와 별개로 레이스 막판(EFFECT_FADE_TAIL 구간)에는 전체 효과 강도에
//     0으로 수렴하는 페이드를 곱한다 -> 결승선 통과 시점(ratio=1.0)에는
//     보이는 위치와 실제 위치가 정확히 일치한다(통과선 판정과 어긋날 수 없음).
// ---------------------------------------------------------------------------
let activeObstacles = [];
let nextObstacleSpawnAt = 0;
let obstacleSeq = 0;
let obstacleRound = null; // 라운드가 바뀌면 이전 라운드 트랙 좌표로 계산된 장애물을 전부 버린다
let kartEffects = {}; // pid -> { kind, until, duration, lag, lateral, spin }

// 종류별 스프라이트/크기/충돌 효과. severity가 클수록 크게 밀린다.
// lag/duration은 사용자 피드백(부딪혀도 진행이 안 더뎌 보인다)에 따라 크게
// 올렸다 -- 부딪힌 카트가 눈에 띄게 뒤처졌다가 1~2초에 걸쳐 따라잡는 정도.
// EFFECT_FADE_TAIL이 결승선 앞 12% 구간에서 모든 효과를 강제로 0까지
// 줄이므로, lag를 아무리 키워도 결승선에서 보이는 위치=실제 위치는 항상
// 보장된다(통과 판정과 절대 어긋나지 않음).
const OBSTACLE_DEFS = {
  cone: { emoji: "🚧", size: 0.9, kind: "wobble", duration: 350, lag: 0, lateral: 0.25, spin: 0.2 },
  oil: { emoji: "🛢️", size: 1.05, kind: "slow", duration: 1400, lag: 0.045, lateral: 0.5, spin: 0.5 },
  tire: { emoji: "🛞", size: 1.0, kind: "slow", duration: 1200, lag: 0.035, lateral: 0.7, spin: 0.35 },
  banana: { emoji: "🍌", size: 0.85, kind: "spin", duration: 1600, lag: 0.05, lateral: 1.2, spin: 6.3 },
  puddle: { emoji: "💧", size: 1.15, kind: "slide", duration: 1300, lag: 0.03, lateral: 1.6, spin: 0.3 },
  rock: { emoji: "🪨", size: 1.25, kind: "stall", duration: 1700, lag: 0.07, lateral: 0.4, spin: 0.8 },
  bomb: { emoji: "💣", size: 1.1, kind: "stall", duration: 2000, lag: 0.09, lateral: 1.4, spin: 9.4 },
  ice: { emoji: "🧊", size: 1.0, kind: "slide", duration: 1500, lag: 0.035, lateral: 1.9, spin: 1.2 },
};
const OBSTACLE_TYPES = Object.keys(OBSTACLE_DEFS);

// 라운드별 동시 활성 개수 -- 사용자 요청(장애물을 훨씬 더 많이)에 따라
// 예전 대비 4~5배로 늘렸다. 카메라가 이제 R1도 확대·추적이라(computeCamera
// 참고) 항상 화면에 보이는 건 선두권뿐이라, 250대가 한꺼번에 화면에 잡혀
// 빽빽해지는 걱정 없이 개수를 키울 수 있었다.
const OBSTACLE_MAX_ACTIVE_BY_ROUND = { 1: 35, 2: 45, 3: 50 };

// 레이스 막판 이 비율 구간에서 모든 시각 효과를 0으로 수렴시킨다
// (결승선에서 보이는 위치 = 실제 위치 보장).
const EFFECT_FADE_TAIL = 0.12;

function effectFadeFor(progressRatio) {
  const remain = 1 - Math.min(1, Math.max(0, progressRatio));
  return Math.min(1, remain / EFFECT_FADE_TAIL);
}

// 카트가 장애물에 닿았을 때 효과를 건다. 이미 더 센 효과가 걸려 있으면
// 덮어쓰지 않는다(연달아 스치며 영원히 멈춰 있는 것을 방지).
// 새로 효과가 걸렸을 때만 true -- 호출부가 이때만 충돌음을 낸다(같은 카트가
// 장애물에 붙어 있는 동안 매 프레임 소리가 나면 안 된다).
function applyKartEffect(pid, type, now) {
  const def = OBSTACLE_DEFS[type];
  if (!def) return false;
  const current = kartEffects[pid];
  if (current && current.until > now && current.lag >= def.lag) return false;
  kartEffects[pid] = {
    kind: def.kind,
    until: now + def.duration,
    duration: def.duration,
    lag: def.lag,
    lateral: def.lateral,
    spin: def.spin,
    dir: Math.random() < 0.5 ? -1 : 1,
  };
  return true;
}

// 장애물 종류 -> 충돌음 종류. 폭탄만 kind가 'stall'이면서도 화면에서는
// 폭발이므로 별도 사운드로 뺀다.
function hitSoundKindFor(type) {
  if (type === "bomb") return "explode";
  const def = OBSTACLE_DEFS[type];
  return def ? def.kind : null;
}

// 남은 시간 비율(1 -> 0)로 감쇠한 현재 효과 강도. 없으면 null.
function kartEffectStateFor(pid, now, fade) {
  const e = kartEffects[pid];
  if (!e) return null;
  const remain = e.until - now;
  if (remain <= 0) {
    delete kartEffects[pid];
    return null;
  }
  const t = remain / e.duration; // 1 -> 0
  const strength = t * fade;
  return {
    kind: e.kind,
    strength,
    // 감속/정지는 "뒤로 처져 보였다가 따라잡는" 형태 -- 진행률에서 잠시 뺀다
    lag: e.lag * strength,
    // 코스 이탈: 옆으로 밀렸다가 제자리로 복귀
    lateral: e.lateral * strength * e.dir,
    // 스핀: 남은 시간 동안 회전하고 멈춘다
    spin: e.spin * strength * e.dir,
  };
}

function colorForDepartment(name) {
  const entry = departmentColorCache.get(name);
  return entry ? entry.base : "#8b95a5";
}

function glowForDepartment(name) {
  const entry = departmentColorCache.get(name);
  return entry ? entry.glow : "#b0bac9";
}

function ensureGroupLookup(latest, drawKey) {
  if (currentDrawKeyForGroups === drawKey) return;
  currentDrawKeyForGroups = drawKey;
  const map = {};
  const departments = (latest.snapshot && latest.snapshot.departments) || {};
  for (const [group, ids] of Object.entries(departments)) {
    for (const id of ids) map[id] = group;
  }
  currentPidToGroup = map;

  departmentColorCache.clear();
  departmentAbilityCache.clear();
  const groupNames = Object.keys(departments).sort();
  groupNames.forEach((name, idx) => {
    departmentColorCache.set(name, TEAM_PALETTE[idx % TEAM_PALETTE.length]);
    departmentAbilityCache.set(name, ABILITY_ROSTER[idx % ABILITY_ROSTER.length]);
  });
  renderTeamLegend(groupNames);
  fetchCharacterChoices();
}

let lastLegendGroupNames = [];

function renderTeamLegend(groupNames) {
  lastLegendGroupNames = groupNames;
  const el = document.getElementById("team-legend");
  if (!el) return;
  el.innerHTML = groupNames
    .map((name) => {
      const ability = abilityForDepartment(name);
      return `<span class="legend-item"><span class="legend-swatch" style="background:${colorForDepartment(
        name
      )}"></span>${name} <span class="legend-ability" title="${ability.label}">${ability.emoji}</span></span>`;
    })
    .join("");
}

function laneFor(pid, laneCount) {
  return Math.floor(hashToUnit(pid + ":lane") * laneCount);
}

function jitterFor(pid) {
  return hashToUnit(pid + ":jitter") - 0.5; // -0.5..0.5
}

// 카트끼리 계속 엎치락뒤치락하며 선두가 바뀌는 것처럼 보이게 하는 잔물결
// (사용자 요청: 서로 추월하면서 선두가 계속 바뀌었으면). 카트마다 다른
// 주기·위상으로 흔들어 근접한 순위끼리 자연스럽게 앞서거니 뒤서거니
// 한다. **"보이는 위치"에만 더해지는 연출**이라 실제 순위(positions,
// 통과 판정, POSITION 패널·카메라가 따라가는 리더)는 전혀 바뀌지 않고,
// 결승선 앞 12%(fade)에서 0으로 수렴해 결승선에서는 다시 정확히 실제
// 위치와 일치한다.
function jockeyOffsetFor(pid, now, fade) {
  const freqSeed = hashToUnit(pid + ":jockeyFreq");
  const phaseSeed = hashToUnit(pid + ":jockeyPhase");
  const freq = 0.00025 + freqSeed * 0.00055; // 카트마다 8~25초대 주기
  const phase = phaseSeed * Math.PI * 2;
  const amp = 0.016; // 트랙 진행률 기준 최대 흔들림 폭
  return Math.sin(now * freq + phase) * amp * fade;
}

// 장애물 lag + 잔물결을 합친 "보이는 위치" 이동량의 안전 상한. R3처럼 트랙이
// 굴곡지고 카메라가 많이 확대된 상태에서는 진행률 0.1 정도의 어긋남도
// 카트가 화면 밖으로 통째로 사라져 보이는 사고로 이어질 수 있어(실제로
// 겪음), 개별 장애물 정의값과 무관하게 여기서 한 번 더 묶어둔다.
const VISUAL_DELTA_MIN = -0.06;
const VISUAL_DELTA_MAX = 0.02;

function visualDeltaFor(pid, now, fade, effect) {
  const raw = jockeyOffsetFor(pid, now, fade) - (effect ? effect.lag : 0);
  return Math.max(VISUAL_DELTA_MIN, Math.min(VISUAL_DELTA_MAX, raw));
}

// ---------------------------------------------------------------------------
// 트랙 경로: S자 웨이포인트 + Catmull-Rom 스플라인 + arc-length 파라미터화 LUT
//
// 서버(app/race.py)는 참가자 위치를 0..1 진행률 스칼라로만 계산하고, 공정성
// 판정도 그 값에만 의존한다. 여기서는 그 스칼라를 곡선 트랙 좌표로 "그려
// 보여줄" 뿐 -- 진행률 값 자체는 절대 바꾸지 않는다.
//
// 캔버스가 전체화면(가변 크기, resizeCanvases에서 window.innerWidth/Height에
// 맞춰 다시 잡힘)이라 트랙 형태를 절대 픽셀이 아니라 W/H 비율로 정의하고,
// 화면 크기가 바뀔 때만 LUT를 다시 만든다(매 프레임 재계산하지 않는다).
// ---------------------------------------------------------------------------

// 웨이포인트 10구간(11점) -- 진폭을 들쭉날쭉하게 둬서(얕은 굴곡 + 깊은
// 굴곡을 섞음) 같은 폭이 반복되는 사인파처럼 안 보이게 했다. 단, 구간당
// 진폭 변화폭(ΔY/ΔT)은 검증된 값(~3.4) 이내로 제한했다 -- 그보다 급하게
// 꺾으면 Catmull-Rom이 오버슈트해 리본 폴리곤이 스스로 겹치는 "나비매듭"
// 아티팩트가 생긴다(실제로 한 번 겪음). 굴곡이 늘어난 만큼 실제 호 길이도
// 늘어나 arc-length 균등 LUT 덕분에 카트가 체감상 "더 먼 트랙"을 도는
// 것처럼 보인다(진행률 0..1의 의미 자체는 서버 쪽과 무관하게 그대로 유지됨).
//
// 라운드마다 트랙을 다르게 둔다(사용자 요청 -- 매번 같은 맵이면 지루하다).
// R1은 250대가 한눈에 들어와야 하니 완만하게, R2는 중간, R3(결선·클로즈업
// 카메라)는 가장 굴곡지게 -- 세 세트 전부 위 ΔY/ΔT 안전 규칙을 지킨다.
// 라운드마다 트랙이 "확실히 달라 보여야" 한다(사용자 피드백: 맵이 다 똑같다).
// 예전 정의는 모양은 조금씩 달랐지만 halfWidthFrac이 0.48~0.55로 다 넓어서,
// 코스 굴곡(진폭 ±0.2~0.34)보다 트랙 폭이 2~3배 크다 보니 셋 다 "굵고
// 뭉툭한 띠"로 뭉개져 사실상 구분이 안 됐다. 그래서 라운드별로 성격을
// 명확히 갈랐다:
//   R1 = 넓고 곧은 고속 스피드웨이 (250대가 한 화면에 들어와야 함)
//   R2 = 폭이 절반인 지그재그 시케인 (약 100대)
//   R3 = 가장 좁고 굴곡이 심한 서킷 (5~10대, 클로즈업 카메라)
// 폭이 좁아질수록 급커브에서 연석이 겹치는 문제도 함께 줄어들기 때문에,
// 진폭을 키우면서 폭을 줄이는 조합이 안전하다.
const TRACK_DEFS = {
  1: {
    // 완만한 롱 스트레이트 + 큰 곡선 두 개. 250대가 한눈에 들어와야
    // 하므로 폭을 가장 넓게 유지한다(차선 수가 많아야 카트가 안 겹침).
    t: [0, 0.3, 0.55, 0.8, 1.0],
    yFrac: [0, -0.16, 0.2, -0.12, 0.04],
    halfWidthFrac: 0.52,
  },
  2: {
    // 지그재그 시케인 -- 진폭을 키우고 폭은 절반 가까이 줄여 "굽이치는
    // 길"로 읽히게 한다.
    t: [0, 0.14, 0.28, 0.42, 0.56, 0.7, 0.85, 1.0],
    yFrac: [0, -0.52, 0.46, -0.44, 0.5, -0.36, 0.3, 0],
    halfWidthFrac: 0.26,
  },
  3: {
    // 결선 전용 테크니컬 서킷 -- 가장 좁고 가장 많이 꺾인다. 카트가
    // 5~10대뿐이라 좁아도 충분히 크게 보인다.
    t: [0, 0.09, 0.19, 0.29, 0.39, 0.5, 0.61, 0.72, 0.83, 0.92, 1.0],
    yFrac: [0, -0.44, 0.52, -0.58, 0.38, -0.5, 0.56, -0.4, 0.46, -0.24, 0],
    halfWidthFrac: 0.17,
  },
};
const TRACK_LUT_SIZE = 400;
const TRACK_RAW_SAMPLES_PER_SEGMENT = 40;

function catmullRomPoint(p0, p1, p2, p3, t) {
  const t2 = t * t;
  const t3 = t2 * t;
  return {
    x:
      0.5 *
      (2 * p1.x +
        (-p0.x + p2.x) * t +
        (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * t2 +
        (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * t3),
    y:
      0.5 *
      (2 * p1.y +
        (-p0.y + p2.y) * t +
        (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * t2 +
        (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * t3),
  };
}

function buildTrackLUT(W, H, round) {
  const def = TRACK_DEFS[round] || TRACK_DEFS[1];
  // 세로 대역을 더 넓게 써서(0.14~0.86 -> 0.05~0.95) 트랙 실제 이동 거리를
  // 늘렸다(사용자 요청: 맵을 더 크게). Catmull-Rom은 아핀 변환에 불변이라
  // yFrac 진폭 "비율" 자체는 그대로 균등 확대될 뿐이라 자기교차(나비매듭)
  // 위험이 새로 생기지 않는다 -- 대역폭만 키우는 안전한 확대 방식이다.
  const trackTop = H * 0.05;
  const trackBottom = H * 0.95;
  const centerY = (trackTop + trackBottom) / 2;
  const halfBand = (trackBottom - trackTop) / 2;
  const halfWidth = halfBand * def.halfWidthFrac;
  const pts = def.t.map((t, i) => ({
    x: TRACK_PAD_X + t * (W - TRACK_PAD_X * 2),
    y: centerY + def.yFrac[i] * halfBand,
  }));

  const padded = [pts[0], ...pts, pts[pts.length - 1]];
  const raw = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const [p0, p1, p2, p3] = [padded[i], padded[i + 1], padded[i + 2], padded[i + 3]];
    for (let s = 0; s < TRACK_RAW_SAMPLES_PER_SEGMENT; s++) {
      raw.push(catmullRomPoint(p0, p1, p2, p3, s / TRACK_RAW_SAMPLES_PER_SEGMENT));
    }
  }
  raw.push(pts[pts.length - 1]);

  // 누적 호 길이(진짜 이동 거리) -- 균등 t 샘플링만 쓰면 커브에서 카트 간격이
  // 압축/팽창돼 보이므로, 이 길이 기준으로 LUT을 재샘플링한다.
  const cumLen = [0];
  for (let i = 1; i < raw.length; i++) {
    cumLen.push(cumLen[i - 1] + Math.hypot(raw[i].x - raw[i - 1].x, raw[i].y - raw[i - 1].y));
  }
  const total = cumLen[cumLen.length - 1];

  const angles = raw.map((_, i) => {
    const a = raw[Math.max(0, i - 1)];
    const b = raw[Math.min(raw.length - 1, i + 1)];
    return Math.atan2(b.y - a.y, b.x - a.x);
  });

  const lut = [];
  let rawIdx = 0;
  for (let i = 0; i < TRACK_LUT_SIZE; i++) {
    const targetLen = (i / (TRACK_LUT_SIZE - 1)) * total;
    while (rawIdx < cumLen.length - 2 && cumLen[rawIdx + 1] < targetLen) rawIdx++;
    const segLen = cumLen[rawIdx + 1] - cumLen[rawIdx] || 1;
    const localT = Math.min(1, Math.max(0, (targetLen - cumLen[rawIdx]) / segLen));
    const a = raw[rawIdx];
    const b = raw[rawIdx + 1];
    lut.push({
      x: a.x + (b.x - a.x) * localT,
      y: a.y + (b.y - a.y) * localT,
      angle: angles[rawIdx],
    });
  }
  return { lut, length: total, halfWidth };
}

const trackLUTCacheByRound = new Map(); // round -> { w, h, lut, length, halfWidth }
function getTrackLUT(round) {
  const r = round || 1;
  const W = window.innerWidth;
  const H = window.innerHeight;
  const cached = trackLUTCacheByRound.get(r);
  if (!cached || cached.w !== W || cached.h !== H) {
    const built = buildTrackLUT(W, H, r);
    const entry = { w: W, h: H, ...built };
    trackLUTCacheByRound.set(r, entry);
    return entry;
  }
  return cached;
}

function trackPointAt(progress, round) {
  const { lut } = getTrackLUT(round);
  const t = Math.min(1, Math.max(0, progress));
  const idx = t * (lut.length - 1);
  const i0 = Math.floor(idx);
  const i1 = Math.min(lut.length - 1, i0 + 1);
  const frac = idx - i0;
  const a = lut[i0];
  const b = lut[i1];
  return {
    x: a.x + (b.x - a.x) * frac,
    y: a.y + (b.y - a.y) * frac,
    angle: a.angle + (b.angle - a.angle) * frac,
  };
}

function computeCamera(leaderPoint, round, W, H) {
  let mode = cameraMode;
  if (mode === "auto") {
    // 예전엔 R1(250대)만 광각 고정이라 카메라가 전혀 안 움직였다 -- 사용자
    // 피드백(속도감·박진감 부족)에 따라 R1도 리더를 따라 확대·이동하는
    // 카메라로 바꿨다. 뒤처진 카트가 화면 밖으로 빠지는 건 의도된 것이다
    // (사용자 요청: 뒤처진 카트는 안 보여도 됨). "광각"은 수동 선택지로만 남는다.
    mode = round === 1 ? "medium" : round === 2 ? "medium" : "close";
  }
  if (mode === "wide") return { mode, scale: 1, offsetX: 0, offsetY: 0 };
  const scale = mode === "close" ? 2.6 : 1.7;
  return {
    mode,
    scale,
    offsetX: W / 2 - leaderPoint.x * scale,
    offsetY: H / 2 - leaderPoint.y * scale,
  };
}

// 응원 이모지(모바일 -> 서버 허용목록 검증 -> stage 전체 브로드캐스트) 수신
// 시 화면 하단 임의 위치에서 떠오르는 요소를 만든다. 순수 연출이라 순위/
// 진행률 등 실제 상태는 전혀 건드리지 않는다.
function spawnCheerBadge(emoji) {
  const el = document.createElement("div");
  el.className = "cheer-badge";
  el.textContent = emoji;
  el.style.left = `${8 + Math.random() * 84}%`;
  overtakeLayer.appendChild(el);
  setTimeout(() => el.remove(), 2500);
}

function spawnOvertakeBadge(pid) {
  const laneCount = lastLaneCount;
  const lane = laneFor(pid, laneCount);
  const round = previousTickRound || 1;
  const { halfWidth } = getTrackLUT(round);
  const laneWidth = (halfWidth * 2) / laneCount;
  const laneOffset = (lane - (laneCount - 1) / 2) * laneWidth + jitterFor(pid) * laneWidth * 0.5;
  const p = previousTickPositions[pid] !== undefined ? previousTickPositions[pid] : 0.5;
  const center = trackPointAt(p, round);
  const nx = -Math.sin(center.angle);
  const ny = Math.cos(center.angle);
  const x = center.x + nx * laneOffset;
  const y = center.y + ny * laneOffset;
  const ability = abilityForParticipant(pid);
  const el = document.createElement("div");
  el.className = "overtake-badge";
  el.textContent = ability.emoji;
  el.style.left = `${Math.min(96, Math.max(4, (x / window.innerWidth) * 100))}%`;
  el.style.top = `${Math.min(94, Math.max(6, (y / window.innerHeight) * 100))}%`;
  overtakeLayer.appendChild(el);
  setTimeout(() => el.remove(), 700);
  return ability;
}

function detectOvertakes(sortedIds, round) {
  if (previousTickRound === round && previousTickOrder.length) {
    const prevRank = new Map(previousTickOrder.map((pid, idx) => [pid, idx]));
    let badges = 0;
    let mcFired = false;
    for (let i = 0; i < sortedIds.length && badges < 4; i++) {
      const pid = sortedIds[i];
      const prevIdx = prevRank.get(pid);
      if (prevIdx === undefined) continue;
      if (prevIdx - i >= 3) {
        const ability = spawnOvertakeBadge(pid);
        SFX.whoosh();
        badges += 1;
        if (!mcFired) {
          // 쿨다운이 걸려 있으면 tryShowLiveMcLine이 조용히 무시한다.
          // 팀이 확인되면 그 팀의 특수능력 이름으로, 아니면 일반 문구로.
          const group = currentPidToGroup[pid];
          if (group) {
            tryShowLiveMcLine("ability_trigger", { team: group, ability: ability.label });
          } else {
            tryShowLiveMcLine("race_progress");
          }
          mcFired = true;
        }
      }
    }
  }
  previousTickOrder = sortedIds;
  previousTickRound = round;
}

// ---------------------------------------------------------------------------
// 실시간 포지션 타워 (좌측 패널)
// ---------------------------------------------------------------------------

let prevRankById = new Map();

function renderPositionTower(sortedIds, positions, passLine) {
  const shown = sortedIds.slice(0, 14);
  positionListEl.innerHTML = shown
    .map((pid, i) => {
      const group = currentPidToGroup[pid];
      const swatch = group ? colorForDepartment(group) : "#8b95a5";
      const prevRank = prevRankById.get(pid);
      let deltaHtml = "";
      if (prevRank !== undefined && prevRank !== i) {
        const diff = prevRank - i;
        deltaHtml = `<span class="pos-delta ${diff > 0 ? "up" : "down"}">${diff > 0 ? "▲" : "▼"}${Math.abs(diff)}</span>`;
      }
      const isLeader = i === 0;
      const atRisk = !isLeader && positions[pid] < passLine && passLine - positions[pid] < 0.06;
      const rowClass = isLeader ? "pos-leader" : atRisk ? "pos-risk" : "";
      const label = pid.length > 14 ? pid.slice(0, 14) + "…" : pid;
      const abilityBadge = isLeader ? `${abilityForParticipant(pid).emoji} ` : "";
      return `<li class="pos-row ${rowClass}">
        <span class="pos-rank">${i + 1}</span>
        <span class="pos-swatch" style="background:${swatch}"></span>
        <span class="pos-name">${abilityBadge}${group ? `${label} · ${group}` : label}</span>
        ${deltaHtml}
      </li>`;
    })
    .join("");
  prevRankById = new Map(sortedIds.map((pid, i) => [pid, i]));
}

// ---------------------------------------------------------------------------
// 캔버스 리사이즈 (풀스크린 대응, devicePixelRatio 보정)
// ---------------------------------------------------------------------------

function resizeCanvases() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = window.innerWidth;
  const h = window.innerHeight;
  raceCanvas.width = Math.round(w * dpr);
  raceCanvas.height = Math.round(h * dpr);
  raceCanvas.style.width = w + "px";
  raceCanvas.style.height = h + "px";
  raceCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  FX.resize(w, h, dpr);
}
resizeCanvases();
window.addEventListener("resize", resizeCanvases);

// ---------------------------------------------------------------------------
// V1: 틱 버퍼 + 60fps 보간 렌더 루프
// ---------------------------------------------------------------------------

const TRACK_PAD_X = 60;
let prevTickState = null;
let currTickState = null;
let tickArrivalAt = 0;
let tickIntervalEstimate = 300;
let rafHandle = null;

function pushTick(tick) {
  const now = performance.now();
  if (currTickState) {
    const delta = now - tickArrivalAt;
    if (delta > 40 && delta < 2000) {
      tickIntervalEstimate = tickIntervalEstimate * 0.7 + delta * 0.3;
    }
  }
  const roundChanged = currTickState && currTickState.round !== tick.round;
  prevTickState = roundChanged ? null : currTickState;
  currTickState = tick;
  tickArrivalAt = now;
  if (rafHandle === null) rafHandle = requestAnimationFrame(renderLoop);
}

function stopRenderLoop() {
  if (rafHandle !== null) {
    cancelAnimationFrame(rafHandle);
    rafHandle = null;
  }
  prevTickState = null;
  currTickState = null;
  SFX.stopEngine();
  FX.setSpeedLines(0);
}

function renderLoop() {
  rafHandle = requestAnimationFrame(renderLoop);
  if (!currTickState) return;

  const alpha = prevTickState
    ? Math.min(1, (performance.now() - tickArrivalAt) / tickIntervalEstimate)
    : 1;

  const positions = {};
  const from = prevTickState ? prevTickState.positions : currTickState.positions;
  const to = currTickState.positions;
  for (const pid of Object.keys(to)) {
    const a = from[pid] !== undefined ? from[pid] : to[pid];
    positions[pid] = a + (to[pid] - a) * alpha;
  }

  drawFrame(positions, currTickState);
}

// ---------------------------------------------------------------------------
// V2/V4/V5: 트랙 맵 · 카트 · 긴장 연출
// ---------------------------------------------------------------------------

function drawTrackSurface(ctx, W, H, lut, halfWidth, passLine, scrollPhase, trackLength, round) {
  const n = lut.length;
  const left = new Array(n);
  const right = new Array(n);
  for (let i = 0; i < n; i++) {
    const p = lut[i];
    const nx = -Math.sin(p.angle);
    const ny = Math.cos(p.angle);
    left[i] = { x: p.x + nx * halfWidth, y: p.y + ny * halfWidth };
    right[i] = { x: p.x - nx * halfWidth, y: p.y - ny * halfWidth };
  }

  // 노면 (곡선 리본 폴리곤)
  ctx.fillStyle = "#171d26";
  ctx.beginPath();
  ctx.moveTo(left[0].x, left[0].y);
  for (let i = 1; i < n; i++) ctx.lineTo(left[i].x, left[i].y);
  for (let i = n - 1; i >= 0; i--) ctx.lineTo(right[i].x, right[i].y);
  ctx.closePath();
  ctx.fill();

  // 통과선 기준 위험(왼쪽)/안전(오른쪽) 구역 -- 웨이포인트가 좌→우로 단조
  // 증가하므로, 가로 그라데이션을 리본 모양에 클립해도 진행률과 대략
  // 일치한다. 리본 폴리곤이 여전히 현재 경로이므로 그대로 clip에 쓴다.
  const passX = trackPointAt(passLine, round).x;
  ctx.save();
  ctx.clip();
  const danger = ctx.createLinearGradient(0, 0, passX, 0);
  danger.addColorStop(0, "rgba(179,38,30,0.28)");
  danger.addColorStop(1, "rgba(179,38,30,0.05)");
  ctx.fillStyle = danger;
  ctx.fillRect(0, 0, passX, H);
  const safe = ctx.createLinearGradient(passX, 0, W, 0);
  safe.addColorStop(0, "rgba(26,127,55,0.06)");
  safe.addColorStop(1, "rgba(26,127,55,0.20)");
  ctx.fillStyle = safe;
  ctx.fillRect(passX, 0, W - passX, H);
  ctx.restore();

  // 가장자리 커브(빨강/흰색 줄무늬) -- arc-length 기준 세그먼트, 스크롤로 속도감.
  // stripeArc가 너무 길면 급커브 구간에서 회전한 직사각형끼리 서로 겹쳐
  // "나비매듭"처럼 보인다(라운드별 트랙 검증 중 실제로 발견) -- 짧게 잘라
  // 곡선을 더 촘촘히 따라가게 한다.
  const curbH = 8;
  const stripeArc = 14;
  const stripeCount = Math.max(4, Math.round(trackLength / stripeArc));
  const segLenPx = trackLength / stripeCount;
  const phaseOffset = scrollPhase * stripeCount * 2;
  for (let i = 0; i < stripeCount; i++) {
    const idx = Math.min(n - 1, Math.round((i / stripeCount) * (n - 1)));
    const color = Math.floor(i + phaseOffset) % 2 === 0 ? "#c0392b" : "#ecf0f1";
    const p = lut[idx];
    ctx.save();
    ctx.translate(p.x, p.y);
    ctx.rotate(p.angle);
    ctx.fillStyle = color;
    ctx.fillRect(-segLenPx / 2, -halfWidth - curbH, segLenPx, curbH);
    ctx.fillRect(-segLenPx / 2, halfWidth, segLenPx, curbH);
    ctx.restore();
  }

  // 중앙 차선 파선 -- 스크롤로 전진감, 경로를 따라 그린다
  ctx.strokeStyle = "rgba(255,255,255,0.16)";
  ctx.lineWidth = 2;
  ctx.setLineDash([22, 20]);
  ctx.lineDashOffset = -(scrollPhase * trackLength) % 42;
  ctx.beginPath();
  ctx.moveTo(lut[0].x, lut[0].y);
  for (let i = 1; i < n; i++) ctx.lineTo(lut[i].x, lut[i].y);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.lineDashOffset = 0;

  // 출발선
  const start = lut[0];
  ctx.save();
  ctx.translate(start.x, start.y);
  ctx.rotate(start.angle);
  ctx.fillStyle = "rgba(255,255,255,0.75)";
  ctx.fillRect(-3, -halfWidth, 3, halfWidth * 2);
  ctx.restore();
}

function drawFinishLine(ctx, point, halfWidth) {
  const cell = 11;
  const cols = 2;
  const span = halfWidth * 2;
  const rows = Math.ceil(span / cell);
  ctx.save();
  ctx.translate(point.x, point.y);
  ctx.rotate(point.angle);
  for (let row = 0; row < rows; row++) {
    const ly = -halfWidth + row * cell;
    for (let col = 0; col < cols; col++) {
      ctx.fillStyle = (row + col) % 2 === 0 ? "#ffffff" : "#11161d";
      ctx.fillRect(col * cell, ly, cell, Math.min(cell, span - row * cell));
    }
  }
  ctx.strokeStyle = "#ffd166";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(-1, -halfWidth);
  ctx.lineTo(-1, halfWidth);
  ctx.stroke();
  ctx.restore();
}

// ---------------------------------------------------------------------------
// 카트 스프라이트 캐시
// ---------------------------------------------------------------------------

const kartSpriteCache = new Map();
const SPRITE_W = 96;
const SPRITE_H = 60;

function roundRectPath(g, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + rr, y);
  g.arcTo(x + w, y, x + w, y + h, rr);
  g.arcTo(x + w, y + h, x, y + h, rr);
  g.arcTo(x, y + h, x, y, rr);
  g.arcTo(x, y, x + w, y, rr);
  g.closePath();
}

function buildKartSprite(color, glow) {
  const c = document.createElement("canvas");
  c.width = SPRITE_W;
  c.height = SPRITE_H;
  const g = c.getContext("2d");
  const W = SPRITE_W;
  const H = SPRITE_H;
  const cy = H / 2;

  g.fillStyle = "rgba(0,0,0,0.30)";
  g.beginPath();
  g.ellipse(W * 0.52, cy + H * 0.30, W * 0.33, H * 0.12, 0, 0, Math.PI * 2);
  g.fill();

  g.fillStyle = "#0d1117";
  roundRectPath(g, W * 0.17, cy - H * 0.44, W * 0.21, H * 0.22, 4); g.fill();
  roundRectPath(g, W * 0.17, cy + H * 0.22, W * 0.21, H * 0.22, 4); g.fill();
  roundRectPath(g, W * 0.66, cy - H * 0.40, W * 0.17, H * 0.19, 3); g.fill();
  roundRectPath(g, W * 0.66, cy + H * 0.21, W * 0.17, H * 0.19, 3); g.fill();

  g.fillStyle = glow;
  roundRectPath(g, W * 0.10, cy - H * 0.26, W * 0.07, H * 0.52, 3);
  g.fill();

  g.fillStyle = color;
  roundRectPath(g, W * 0.28, cy - H * 0.32, W * 0.34, H * 0.64, 5);
  g.fill();

  g.beginPath();
  g.moveTo(W * 0.20, cy - H * 0.19);
  g.lineTo(W * 0.72, cy - H * 0.15);
  g.lineTo(W * 0.90, cy - H * 0.07);
  g.lineTo(W * 0.90, cy + H * 0.07);
  g.lineTo(W * 0.72, cy + H * 0.15);
  g.lineTo(W * 0.20, cy + H * 0.19);
  g.closePath();
  g.fillStyle = color;
  g.fill();

  g.fillStyle = "rgba(255,255,255,0.22)";
  roundRectPath(g, W * 0.30, cy - H * 0.28, W * 0.30, H * 0.10, 4);
  g.fill();

  g.fillStyle = "rgba(0,0,0,0.55)";
  g.beginPath();
  g.ellipse(W * 0.47, cy, W * 0.09, H * 0.17, 0, 0, Math.PI * 2);
  g.fill();

  g.fillStyle = glow;
  g.beginPath();
  g.arc(W * 0.47, cy, H * 0.11, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "rgba(0,0,0,0.45)";
  g.beginPath();
  g.ellipse(W * 0.50, cy, H * 0.045, H * 0.07, 0, 0, Math.PI * 2);
  g.fill();

  g.fillStyle = glow;
  g.beginPath();
  g.moveTo(W * 0.88, cy - H * 0.06);
  g.lineTo(W * 0.98, cy);
  g.lineTo(W * 0.88, cy + H * 0.06);
  g.closePath();
  g.fill();

  return c;
}

function kartSpriteFor(color, glow) {
  const key = color + "|" + glow;
  let sprite = kartSpriteCache.get(key);
  if (!sprite) {
    sprite = buildKartSprite(color, glow);
    kartSpriteCache.set(key, sprite);
  }
  return sprite;
}

// 충돌 효과 종류별 표식 -- 뭘 밟았는지 한눈에 보이게 한다(연출 전용).
const EFFECT_MARK = { stall: "💥", spin: "💫", slide: "🌀", slow: "🐌", wobble: "" };

function drawKart(ctx, x, y, h, angle, color, glow, isLeader, atRisk, pulse, effect) {
  const w = h * (SPRITE_W / SPRITE_H);
  // 장애물 충돌 연출(스핀아웃/정지/미끄러짐) -- 전부 보이는 각도·크기만
  // 건드리며, 위치 데이터(progress)는 drawFrame에서 이미 분리해 두었다.
  const s = effect ? effect.strength : 0;
  const spinAngle = effect ? effect.spin : 0;
  const shakeAngle = s ? Math.sin(performance.now() * 0.045) * 0.22 * s : 0;
  const squash = s ? 1 - 0.12 * s : 1;

  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle + spinAngle + shakeAngle);
  ctx.scale(squash, squash);

  // 배기 연기 / 속도 자국. 진행 방향 반대쪽(로컬 -x)에 그려 회전해도 항상 뒤쪽에 남는다.
  // 감속·정지 중에는 트레일을 줄여 "속도를 잃었다"는 게 보이게 한다.
  if (h >= 9) {
    const trailCount = Math.max(0, 3 - Math.round(s * 3));
    for (let t = 1; t <= trailCount; t++) {
      ctx.globalAlpha = 0.14 / t;
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(-w * (0.5 + t * 0.26), 0, h * (0.16 + t * 0.05), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  if (isLeader) {
    ctx.shadowColor = glow;
    ctx.shadowBlur = 16;
  } else if (atRisk) {
    ctx.shadowColor = "#ff5252";
    ctx.shadowBlur = 5 + pulse * 9;
  }

  ctx.drawImage(kartSpriteFor(color, glow), -w / 2, -h / 2, w, h);
  ctx.shadowBlur = 0;

  // 선두 왕관 -- 회전을 상쇄해 항상 위를 향한다.
  if (isLeader && h >= 14) {
    ctx.save();
    ctx.rotate(-(angle + spinAngle + shakeAngle));
    ctx.font = `${Math.round(h * 0.8)}px serif`;
    ctx.textAlign = "center";
    ctx.fillText("👑", 0, -h * 0.9);
    ctx.restore();
  }

  // 피격 표식 -- 회전을 상쇄해 항상 똑바로 보이게 한다.
  if (effect && s > 0.25 && h >= 10 && EFFECT_MARK[effect.kind]) {
    ctx.save();
    ctx.rotate(-(angle + spinAngle + shakeAngle));
    ctx.globalAlpha = Math.min(1, s * 1.6);
    ctx.font = `${Math.round(h * 0.85)}px serif`;
    ctx.textAlign = "center";
    ctx.fillText(EFFECT_MARK[effect.kind], 0, -h * 0.75);
    ctx.restore();
    ctx.globalAlpha = 1;
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// 장애물 렌더/스폰: 마블 레이스 느낌의 움직이는 방해물(연출 전용). 트랙 위를
// 좌우로 요동치며 떠 있다가, 카트가 닿으면 applyKartEffect로 스핀/정지/감속
// 효과를 건다 -- positions[pid](순위 판정 값)는 이 파일 어디서도 바꾸지 않고,
// 효과는 결승선 전에 반드시 0으로 수렴한다(EFFECT_FADE_TAIL 참고).
// ---------------------------------------------------------------------------

const obstacleSpriteCache = new Map();

function buildObstacleSprite(type) {
  const c = document.createElement("canvas");
  c.width = 56;
  c.height = 56;
  const g = c.getContext("2d");
  g.font = "40px serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText((OBSTACLE_DEFS[type] || OBSTACLE_DEFS.cone).emoji, 28, 30);
  return c;
}

function obstacleSpriteFor(type) {
  let sprite = obstacleSpriteCache.get(type);
  if (!sprite) {
    sprite = buildObstacleSprite(type);
    obstacleSpriteCache.set(type, sprite);
  }
  return sprite;
}

function drawObstacle(ctx, o, size) {
  ctx.save();
  ctx.globalAlpha = 0.94;
  ctx.shadowColor = "rgba(0,0,0,0.5)";
  ctx.shadowBlur = 4;
  ctx.translate(o.x, o.y);
  ctx.rotate(o.spinPhase);
  const s = size * o.sizeScale;
  ctx.drawImage(obstacleSpriteFor(o.type), -s / 2, -s / 2, s, s);
  ctx.restore();
}

// 화면에 보이는 무리(tailProgress~leaderProgress)를 기준으로 앞쪽에 장애물을
// 스폰하고, 그 무리 전체가 한참 지나친 것만 제거한다(사용자 요청: 장애물이
// 없어지지 않게). tailProgress는 카메라 밖으로 밀려난 카트까지 포함해서
// 넉넉하게 잡으므로, 예전(리더 기준 0.08)보다 훨씬 오래 트랙 위에 남는다.
// 라운드가 바뀌면 이전 라운드 트랙 좌표로 만든 장애물은 의미가 없으므로
// 전부 비운다.
function maybeSpawnObstacle(now, leaderProgress, tailProgress, round) {
  if (obstacleRound !== round) {
    obstacleRound = round;
    activeObstacles = [];
    nextObstacleSpawnAt = 0;
  }
  const PERSIST_MARGIN = 0.2;
  activeObstacles = activeObstacles.filter((o) => tailProgress - o.spawnProgress < PERSIST_MARGIN);
  const maxActive = OBSTACLE_MAX_ACTIVE_BY_ROUND[round] || 35;
  if (activeObstacles.length >= maxActive) return;
  if (now < nextObstacleSpawnAt) return;
  const type = OBSTACLE_TYPES[Math.floor(Math.random() * OBSTACLE_TYPES.length)];
  activeObstacles.push({
    id: `obs-${obstacleSeq++}`,
    type,
    // 같은 종류라도 크기를 흔들어 "다양한 크기"가 실제로 보이게 한다
    sizeScale: OBSTACLE_DEFS[type].size * (0.75 + Math.random() * 0.7),
    spawnProgress: Math.min(0.97, leaderProgress + 0.1 + Math.random() * 0.16),
    laneCenter: Math.random() * lastLaneCount,
    weaveAmp: 1.5 + Math.random() * 3,
    weaveSpeed: 0.0015 + Math.random() * 0.0018,
    spinPhase: Math.random() * Math.PI * 2,
    spinSpeed: (Math.random() - 0.5) * 0.0025,
    spawnedAt: now,
  });
  // 상한을 훨씬 올린 만큼 그 상한까지 빨리 채워지도록 간격도 더 좁혔다
  // (사용자 요청: 장애물 수를 훨씬 많게).
  nextObstacleSpawnAt = now + 120 + Math.random() * 220;
}

function obstacleScreenPoints(now, laneCount, laneWidth, round) {
  return activeObstacles.map((o) => {
    const weaveLane = o.laneCenter + Math.sin((now - o.spawnedAt) * o.weaveSpeed) * o.weaveAmp;
    const laneOffset = (weaveLane - (laneCount - 1) / 2) * laneWidth;
    const center = trackPointAt(o.spawnProgress, round);
    const nx = -Math.sin(center.angle);
    const ny = Math.cos(center.angle);
    return {
      ...o,
      spinPhase: o.spinPhase + (now - o.spawnedAt) * o.spinSpeed,
      x: center.x + nx * laneOffset,
      y: center.y + ny * laneOffset,
    };
  });
}

function drawFrame(positions, tick) {
  const ids = Object.keys(positions);
  if (!ids.length) return;
  const W = window.innerWidth;
  const H = window.innerHeight;
  const now = performance.now();
  const pulse = (Math.sin(now / 140) + 1) / 2;

  raceCtx.clearRect(0, 0, W, H);
  raceCtx.fillStyle = "#05070c";
  raceCtx.fillRect(0, 0, W, H);

  const sorted = [...ids].sort((a, b) => positions[b] - positions[a]);
  const leaderPos = positions[sorted[0]];
  const fade = effectFadeFor(tick.progress_ratio);
  // 카메라는 리더의 "실제" 진행률이 아니라 리더 본인에게 걸린 효과까지
  // 반영한 "보이는" 위치를 따라간다 -- 그래야 리더가 장애물에 맞아
  // 뒤로 밀린 순간에도 카메라 중심 = 리더가 그려지는 자리가 항상
  // 일치해서, 화면이 확대된 R2/R3에서도 리더가 프레임 밖으로 사라지는
  // 일이 없다.
  const leaderEffect = kartEffectStateFor(sorted[0], now, fade);
  const leaderShownPos = Math.max(0, leaderPos + visualDeltaFor(sorted[0], now, fade, leaderEffect));
  const { lut, length: trackLength, halfWidth } = getTrackLUT(tick.round);
  const leaderPoint = trackPointAt(leaderShownPos, tick.round);
  const camera = computeCamera(leaderPoint, tick.round, W, H);

  raceCtx.save();
  raceCtx.translate(camera.offsetX, camera.offsetY);
  raceCtx.scale(camera.scale, camera.scale);

  drawTrackSurface(raceCtx, W, H, lut, halfWidth, tick.pass_line, leaderShownPos, trackLength, tick.round);
  drawFinishLine(raceCtx, trackPointAt(tick.pass_line, tick.round), halfWidth);

  // 트랙이 라운드마다 좁아지므로 차선 수도 폭에 맞춘다 -- 좁은 트랙에서
  // 36차선을 그대로 쓰면 카트가 점처럼 작아져 아무것도 안 보인다.
  const laneCount = Math.max(6, Math.min(36, ids.length, Math.floor((halfWidth * 2) / 11)));
  lastLaneCount = laneCount;
  const laneWidth = (halfWidth * 2) / laneCount;
  const kartH = Math.max(6, Math.min(64, laneWidth * 0.82));

  // 카메라가 확대·추적 중일 때(광각이 아닐 때)는 화면에 안 잡히는 후미
  // 카트를 그리지도, 장애물 충돌 판정도 하지 않는다(사용자 요청: 뒤처진
  // 카트는 안 보여도 됨). 어차피 카메라 밖이라 원래도 안 보였을 대상이고,
  // 장애물을 훨씬 늘려도(위 OBSTACLE_MAX_ACTIVE_BY_ROUND) 프레임이 안
  // 무거워지도록 판정 대상 자체를 줄인다. 광각은 트랙 전체가 화면에 다
  // 들어오므로 컬링하지 않는다.
  const CULL_MARGIN_BY_MODE = { wide: Infinity, medium: 0.34, close: 0.24 };
  const cullMargin = CULL_MARGIN_BY_MODE[camera.mode] ?? 0.34;
  const tailProgress = Math.max(0, leaderShownPos - cullMargin);

  maybeSpawnObstacle(now, leaderShownPos, tailProgress, tick.round);
  const obstaclePoints = obstacleScreenPoints(now, laneCount, laneWidth, tick.round);
  const obstacleSize = Math.max(18, kartH * 1.5);
  for (const o of obstaclePoints) {
    drawObstacle(raceCtx, o, obstacleSize);
  }
  const hitRadius = Math.max(14, kartH * 1.2);

  if (crossedRound !== tick.round) {
    crossedRound = tick.round;
    crossedPassLine = new Set(sorted.filter((pid) => positions[pid] >= tick.pass_line));
    crossedSoundCount = 0;
  }

  let riskCount = 0;
  for (let i = sorted.length - 1; i >= 0; i--) {
    const pid = sorted[i];
    const p = positions[pid];
    if (p < tailProgress) continue; // 카메라 밖 후미 -- 그리지도 판정하지도 않음
    const effect = kartEffectStateFor(pid, now, fade);

    // 충돌 효과 + 엎치락뒤치락 잔물결 모두 "보이는 위치"에만 적용한다
    // -- p(순위 판정 값)는 불변.
    const shownP = Math.max(0, p + visualDeltaFor(pid, now, fade, effect));
    const lane = laneFor(pid, laneCount);
    const lateral = effect ? effect.lateral * laneWidth : 0;
    const laneOffset =
      (lane - (laneCount - 1) / 2) * laneWidth + jitterFor(pid) * laneWidth * 0.5 + lateral;
    const center = trackPointAt(shownP, tick.round);
    const nx = -Math.sin(center.angle);
    const ny = Math.cos(center.angle);
    const x = center.x + nx * laneOffset;
    const y = center.y + ny * laneOffset;

    // 장애물에 닿으면 종류별 효과(스핀아웃/정지/감속/미끄러짐)를 건다.
    if (fade > 0) {
      for (const o of obstaclePoints) {
        const dx = x - o.x;
        const dy = y - o.y;
        const r = hitRadius + (obstacleSize * o.sizeScale) / 2;
        if (dx * dx + dy * dy < r * r) {
          if (applyKartEffect(pid, o.type, now)) {
            // 충돌음은 선두권일수록 크게. 250대가 동시에 부딪히는 R1에서도
            // 소리가 뭉치지 않도록 볼륨을 순위로 깎고, 나머지는 audio.js의
            // 게이트가 솎아낸다. 화면 밖(카메라 줌 아웃 전) 뒤쪽 집단은
            // 아주 작게만 들린다.
            // sorted는 선두가 0번(루프는 겹침 순서 때문에 뒤에서부터 돈다)
            const scale = i === 0 ? 1.15 : i < 5 ? 0.85 : i < 20 ? 0.5 : 0.28;
            SFX.hit(hitSoundKindFor(o.type), scale);
            if (i === 0 && (o.type === "bomb" || o.type === "rock")) {
              FX.screenShake(o.type === "bomb" ? 8 : 5, 240);
            }
          }
          break;
        }
      }
    }

    const group = currentPidToGroup[pid];
    const isLeader = i === 0;
    const atRisk = !isLeader && p < tick.pass_line && tick.pass_line - p < 0.06;
    if (atRisk) riskCount += 1;

    // 결승(통과)선 통과 -- 선두 통과는 체커기 + 함성으로 크게 가고, 뒤따르는
    // 카트는 짧은 블립만 낸다. 판정 자체는 서버가 하므로 여기서는 연출만.
    if (p >= tick.pass_line && !crossedPassLine.has(pid)) {
      crossedPassLine.add(pid);
      if (crossedSoundCount < CROSS_SOUND_BUDGET) {
        crossedSoundCount += 1;
        SFX.finishCross(i);
      }
      if (isLeader) {
        FX.screenFlash("rgba(255,255,255,0.4)", 200);
        FX.ring(x, y, "#ffd166", 180);
      }
    }

    drawKart(
      raceCtx,
      x,
      y,
      kartH,
      center.angle,
      group ? colorForDepartment(group) : "#8b95a5",
      group ? glowForDepartment(group) : "#b0bac9",
      isLeader,
      atRisk,
      pulse,
      effect
    );
  }
  raceCtx.globalAlpha = 1;
  raceCtx.restore();

  drawHud(raceCtx, W, H, positions, tick, sorted);
  renderPositionTower(sorted, positions, tick.pass_line);

  // 속도 연출: 진행률 + 근접 경쟁 강도에 비례해 속도선/엔진 피치/BGM 텐션을 올린다
  const speedIntensity = Math.min(1, tick.progress_ratio * 1.05 + riskCount * 0.03);
  FX.setSpeedLines(Math.min(1, tick.progress_ratio * 1.1));
  SFX.setRpm(speedIntensity);
  SFX.setSceneIntensity(speedIntensity);

  // 레이스 막판에 새로 띄운 실황 멘트는 도착할 즈음 이미 결과 발표로
  // 넘어가 있다 -- 진행률이 임계값을 넘으면 실황 멘트를 잠근다(라운드
  // 시작 시 다시 열린다). FINAL LAP/포토피니시 같은 "그 순간의 연출"은
  // 이 잠금과 무관하게 그대로 나간다.
  if (tick.progress_ratio >= MC_LIVE_STOP_PROGRESS) liveMcSuppressed = true;

  // 클로즈콜: 통과선 근처에 여러 대가 몰려 있으면 긴장 멘트 + 심장박동
  if (riskCount >= 3) {
    tryShowLiveMcLine("close_call");
  }

  // 파이널 랩: 라운드당 한 번, 진행률 85% 지점에서
  if (tick.progress_ratio >= 0.85 && finalLapShownForRound !== tick.round) {
    finalLapShownForRound = tick.round;
    showBanner("FINAL LAP", "마지막 스퍼트!", 1500);
    showMcLine("final_lap");
    SFX.bell(); // 마지막 랩 종
    SFX.heartbeat();
    SFX.crowd(0.5, 1.4);
  }

  // 포토 피니시: 결선(R3)에서 선두 두 대가 초박빙으로 들어올 때
  if (
    tick.round === 3 &&
    tick.progress_ratio >= 0.97 &&
    sorted.length >= 2 &&
    photoFinishShownForRound !== tick.round &&
    Math.abs(positions[sorted[0]] - positions[sorted[1]]) < 0.012
  ) {
    photoFinishShownForRound = tick.round;
    showBanner("PHOTO FINISH", "", 1600);
    showMcLine("photo_finish");
    SFX.riser(0.9);
    SFX.crowd(0.9, 1.6);
    FX.screenFlash("rgba(255,255,255,0.6)", 200);
  }
}

function drawHud(ctx, W, H, positions, tick, sorted) {
  const passing = sorted.filter((pid) => positions[pid] >= tick.pass_line).length;
  const barY = H * 0.06;
  ctx.fillStyle = "rgba(255,255,255,0.08)";
  ctx.fillRect(TRACK_PAD_X, barY, W - TRACK_PAD_X * 2, 6);
  ctx.fillStyle = "#4f8cff";
  ctx.fillRect(TRACK_PAD_X, barY, (W - TRACK_PAD_X * 2) * tick.progress_ratio, 6);

  ctx.font = "bold 18px 'Malgun Gothic', sans-serif";
  ctx.fillStyle = "#7cf29c";
  ctx.textAlign = "left";
  ctx.fillText(`통과권 ${passing}대`, TRACK_PAD_X, barY - 8);

  ctx.textAlign = "right";
  ctx.fillStyle = "#8b95a5";
  ctx.fillText(`${Math.round(tick.progress_ratio * 100)}%`, W - TRACK_PAD_X, barY - 8);
  ctx.textAlign = "left";
}

function renderTrack(tick) {
  pushTick(tick);
  const sorted = Object.keys(tick.positions).sort(
    (a, b) => tick.positions[b] - tick.positions[a]
  );
  detectOvertakes(sorted, tick.round);
  previousTickPositions = tick.positions;
}

// ---------------------------------------------------------------------------
// 카운트다운 (HUD 타이머)
// ---------------------------------------------------------------------------

let lastTickSecond = null;

function startCountdown(durationSeconds, startedAtIso, phaseLabel, roundIndex) {
  if (countdownTimer) clearInterval(countdownTimer);
  lastTickSecond = null;
  const startedAt = new Date(startedAtIso).getTime();
  phaseLabelEl.textContent = phaseLabel;
  roundPillEl.textContent = roundIndex ? `ROUND ${roundIndex}` : "READY";
  const update = () => {
    const remain = Math.max(0, durationSeconds - (Date.now() - startedAt) / 1000);
    phaseTimerEl.textContent = `${remain.toFixed(1)}s`;
    const urgent = remain <= 5 && remain > 0;
    phaseTimerEl.classList.toggle("urgent", urgent);
    const wholeSecond = Math.ceil(remain);
    if (urgent && wholeSecond !== lastTickSecond) {
      lastTickSecond = wholeSecond;
      SFX.tick(true);
    }
    if (remain <= 0) clearInterval(countdownTimer);
  };
  update();
  countdownTimer = setInterval(update, 100);
}

const PHASE_LABELS = {
  opening: "오프닝",
  r1_lock: "1라운드 준비",
  race_r1: "1라운드 레이스",
  score_r1_select_r2: "1라운드 결과 발표",
  race_r2: "2라운드 레이스",
  score_r2_select_r3: "2라운드 결과 발표",
  race_r3: "결선",
  final_announce: "최종 발표",
  verify: "공정성 검증",
};

function handleRacingEvent(data) {
  if (data.type === "phase") {
    racingStarted = true;
    // final_announce 중 리빌되면 playFinalReveal()이 시상대(overlay-podium)를
    // 띄운다. 그 직후 도착하는 verify phase 이벤트가 여기서 무조건
    // hideAllOverlays()를 부르면 막 띄운 시상대가 바로 가려지고, 복구
    // 분기(render()의 "else if")는 bannerHideTimer가 이미 최소 한 번은
    // 세팅돼 있어 사실상 다시 null이 되지 않으므로 영영 안 돌아온다 --
    // 시상대가 떠 있는 동안은 오버레이를 건드리지 않는다.
    if (overlays.podium.classList.contains("hidden")) {
      hideAllOverlays();
    }
    currentPhase = data.phase;
    startCountdown(
      data.duration_seconds,
      data.started_at,
      PHASE_LABELS[data.phase] || data.phase,
      RACE_ROUND_INDEX_LOCAL[data.phase]
    );
    // 장면이 바뀌었다 -- 아직 도착하지 않은 이전 장면의 멘트는 전부 버린다
    // (레이스가 끝났는데 계속 레이스를 중계하던 문제).
    cancelPendingMcLines();
    if (["race_r1", "race_r2", "race_r3"].includes(data.phase)) {
      currentLeaderDept = null;
      liveMcSuppressed = false; // 새 레이스가 시작됐으니 실황 멘트를 다시 허용
      const roundIndex = RACE_ROUND_INDEX_LOCAL[data.phase];
      finalLapShownForRound = null;
      if (roundIndex === 3) photoFinishShownForRound = null;
      runStartLights(roundIndex);
      SFX.startEngine();
      SFX.playScene(RACE_BGM_SCENE[roundIndex] || "race1", { round: roundIndex });
      fetchCharacterChoices(); // 레이스 시작 직전 최종 선택 스냅샷(매 라운드 -- 라운드 사이 선택창에서 바꾼 캐릭터도 반영)
    } else {
      SFX.stopEngine();
      FX.setSpeedLines(0);
      // 구간별 BGM:
      //  - 결과 발표(final_announce/verify): 승리감 있는 장조 루프
      //  - 레이스 직전 대기(opening/r1_lock): 출발을 기다리는 그리드 루프
      //  - 라운드 사이 선택창(score_rX_select_rY): 결정을 기다리는 긴장 루프
      let scene = "anticipation";
      if (data.phase === "final_announce" || data.phase === "verify") scene = "victory";
      else if (data.phase === "opening" || data.phase === "r1_lock") scene = "standby";
      SFX.playScene(scene);
    }
    if (data.phase === "race_r1") showMcLine("opening");
    if (data.phase === "race_r3") showMcLine("race_progress");
    if (data.phase === "score_r1_select_r2" || data.phase === "score_r2_select_r3") {
      if (lastPredictionWindow && lastPredictionWindow.state === "open") {
        showMcLine("prediction_open", { round: lastPredictionWindow.round });
      }
    }
  } else if (data.type === "race_tick") {
    renderTrack(data);
    if (data.department_live_rate) {
      renderDepartmentBars(data.department_live_rate);
    }
  } else if (data.type === "round_revealed") {
    showBanner(
      `ROUND ${data.round} 통과!`,
      `${data.pass_ids ? data.pass_ids.length : "-"}명이 다음 라운드로 진출합니다`,
      2000
    );
    SFX.pass();
    SFX.crowd(0.6, 1.5);
    FX.ring(window.innerWidth / 2, window.innerHeight * 0.5, "#7cf29c", 220);
    // 후속 멘트("elimination")는 2.2초 뒤에 나가는데, 그 사이 다음 구간으로
    // 넘어갔으면 흘러간 멘트이므로 내보내지 않는다.
    const revealEpoch = mcEpoch;
    showMcLine("round_pass_announce", { pass_count: data.pass_ids ? data.pass_ids.length : undefined })
      .then(() => delay(2200))
      .then(() => {
        if (revealEpoch === mcEpoch) showMcLine("elimination");
      });
  } else if (data.type === "racing_complete") {
    if (countdownTimer) clearInterval(countdownTimer);
    // 진행이 끝났는데 뒤늦게 도착한 레이스 중계 멘트가 흘러나오지 않도록 한다.
    cancelPendingMcLines({ clearCaption: true });
    liveMcSuppressed = true;
    phaseLabelEl.textContent = "진행 완료";
    phaseTimerEl.textContent = "--";
    phaseTimerEl.classList.remove("urgent");
    if (rafHandle !== null) {
      cancelAnimationFrame(rafHandle);
      rafHandle = null;
    }
    SFX.stopEngine();
    FX.setSpeedLines(0);
  }
}

const RACE_ROUND_INDEX_LOCAL = { race_r1: 1, race_r2: 2, race_r3: 3 };

// ---------------------------------------------------------------------------
// 최종 시상대
// ---------------------------------------------------------------------------

const PRIZE_BASIS_LABEL = {
  prediction: "예측 게임 최종 리더보드 기준 당첨자입니다 -- 레이스 순위와 다를 수 있습니다",
};

function formatPrizeScore(score) {
  if (score === undefined || score === null) return "";
  return `${Number(score).toLocaleString("ko-KR")}점`;
}

function buildPodium(winnerIds, nameById, basis, scores) {
  if (podiumBasisHintEl) podiumBasisHintEl.textContent = PRIZE_BASIS_LABEL[basis] || "";
  const scoreList = scores || [];
  const scoreOf = (id) => {
    const i = winnerIds.indexOf(id);
    return i >= 0 ? scoreList[i] : undefined;
  };
  const top3 = winnerIds.slice(0, 3);
  const rest = winnerIds.slice(3);
  // 시각적 배치: 2위-1위-3위 순서(가운데가 1위)
  const order = [top3[1], top3[0], top3[2]].filter((x) => x !== undefined);
  const rankOf = (id) => top3.indexOf(id) + 1;
  podiumStageEl.innerHTML = order
    .map((id, i) => {
      const rank = rankOf(id);
      const label = nameById[id] || id;
      const medal = rank === 1 ? "🥇" : rank === 2 ? "🥈" : "🥉";
      const scoreText = formatPrizeScore(scoreOf(id));
      return `<div class="podium-slot" data-rank="${rank}" style="animation-delay:${i * 0.15}s">
        <div class="podium-name">${label}</div>
        ${scoreText ? `<div class="podium-score">${scoreText}</div>` : ""}
        <div class="podium-block">${medal}</div>
      </div>`;
    })
    .join("");
  podiumRestEl.innerHTML = rest
    .map((id) => {
      const scoreText = formatPrizeScore(scoreOf(id));
      return `<li>${nameById[id] || id}${
        scoreText ? ` <span class="podium-rest-score">${scoreText}</span>` : ""
      }</li>`;
    })
    .join("");
}

async function playFinalReveal(winnerIds, nameById, basis, scores) {
  showBanner("🏆 최종 당첨자 발표!", "", 1500);
  SFX.drumroll(1.4);
  SFX.riser(1.4); // 드럼롤 위에 상승음을 겹쳐 발표 직전 긴장을 끌어올린다
  await delay(1500);
  buildPodium(winnerIds, nameById, basis, scores);
  showOverlay("podium");
  SFX.fanfare();
  SFX.crowd(0.8, 1.8);
  FX.confetti(220);
  FX.screenFlash("rgba(255,209,102,0.35)", 260);
  for (let i = 0; i < 3; i++) {
    setTimeout(() => FX.burst(window.innerWidth * (0.3 + i * 0.2), window.innerHeight * 0.4, "#ffd166", 24), i * 220);
  }
}

// ---------------------------------------------------------------------------
// 세션 상태 조회(폴링/이벤트 트리거) -- 룰렛 모드 렌더링
// ---------------------------------------------------------------------------

function render(session) {
  if (!session) {
    showOverlay("idle");
    statusEl.textContent = "명단 등록을 기다리는 중입니다";
    return;
  }
  const latest = session.draws[session.draws.length - 1];
  if (!latest) {
    const departmentCount = new Set(session.participants.map((p) => p.team || "미지정")).size;
    participantCountEl.textContent = session.participants.length;
    departmentCountEl.textContent = departmentCount;
    showOverlay("waiting");
    return;
  }

  if (session.mode === "racing" && session.predictions_enabled) {
    startLiveDistributionPolling();
  }

  if (session.mode === "racing") {
    ensureGroupLookup(latest, latest.commit);
    if (latest.revealed) {
      stopLiveDistributionPolling();
      if (!latest.prize_winners) {
        // 레이스는 리빌됐지만 예측 게임 라운드 3 최종 채점이 아직 안
        // 끝났다 -- 실제 당첨자가 확정될 때까지는 시상대를 띄우지 않는다
        // (막판까지 리더보드가 뒤집힐 수 있다는 게 이 설계의 핵심이라,
        // 레이스 리빌 순간과 최종 당첨자 발표 순간을 일부러 분리했다).
        return;
      }
      if (lastFinalShownFor !== "racing-final") {
        lastFinalShownFor = "racing-final";
        const nameById = Object.fromEntries(
          latest.snapshot.participants.map((p) => [p.id, participantLabel(p)])
        );
        const hasPrediction = latest.prize_basis === "prediction";
        playFinalReveal(latest.prize_winners, nameById, latest.prize_basis, latest.prize_scores)
          .then(() => delay(2500))
          .then(() => (hasPrediction ? showMcLine("prediction_champion") : Promise.resolve()))
          .then(() => delay(hasPrediction ? 2500 : 0))
          .then(() => showMcLine("verification"));
      } else if (overlays.podium.classList.contains("hidden") && bannerHideTimer === null) {
        // 새로고침 등으로 재진입한 경우 이미 지나간 연출 없이 바로 시상대만 표시
        const nameById = Object.fromEntries(
          latest.snapshot.participants.map((p) => [p.id, participantLabel(p)])
        );
        buildPodium(latest.prize_winners, nameById, latest.prize_basis, latest.prize_scores);
        showOverlay("podium");
      }
      return;
    }
    if (racingStarted) {
      return; // race_tick/phase 이벤트가 실시간 갱신을 담당
    }
    commitBadgeEl.textContent = latest.commit;
    showOverlay("committed");
    if (lastOpeningShownFor !== latest.commit) {
      lastOpeningShownFor = latest.commit;
      showMcLine("opening");
    }
    return;
  }

  // 룰렛 모드
  if (!latest.revealed) {
    commitBadgeEl.textContent = latest.commit;
    showOverlay("committed");
    if (lastOpeningShownFor !== latest.commit) {
      lastOpeningShownFor = latest.commit;
      showMcLine("opening");
    }
    return;
  }
  const drawKey = `${session.draws.length - 1}:${latest.revealed_at}`;
  if (!animatedDrawKeys.has(drawKey)) {
    animatedDrawKeys.add(drawKey);
    playRouletteSequence(latest).then(() => {
      if (lastFinalShownFor !== drawKey) {
        lastFinalShownFor = drawKey;
        showMcLine("final_announce").then(() => delay(2500)).then(() => showMcLine("verification"));
      }
    });
  } else {
    showOverlay("roulette");
    winnerListEl.innerHTML = latest.winners
      .map((id) => {
        const p = latest.snapshot.participants.find((x) => x.id === id);
        return `<li>${p ? participantLabel(p) : id}</li>`;
      })
      .join("");
    reelEl.textContent = "🎊 추첨 완료!";
  }
}

async function startDemo(button) {
  unlockAudioOnce();
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "데모 준비 중...";
  try {
    await fetchJSON("/api/demo/start", { method: "POST", body: JSON.stringify({}) });
  } catch (e) {
    alert(e.message);
    button.disabled = false;
    button.textContent = originalText;
  }
}

document.getElementById("btn-demo-start").addEventListener("click", (e) => startDemo(e.target));

async function refresh() {
  try {
    const session = await fetchJSON("/api/session");
    render(session);
  } catch (e) {
    if (e.status === 404) {
      render(null);
    }
  }
}

const ws = connectWS((data) => {
  if (data.type === "reset") {
    racingStarted = false;
    lastOpeningShownFor = null;
    lastFinalShownFor = null;
    currentDrawKeyForGroups = null;
    currentPidToGroup = {};
    characterChoiceByPid = {};
    previousTickPositions = {};
    previousTickOrder = [];
    previousTickRound = null;
    currentLeaderDept = null;
    lastMcLiveCallAt = 0;
    finalLapShownForRound = null;
    photoFinishShownForRound = null;
    activeObstacles = [];
    nextObstacleSpawnAt = 0;
    kartEffects = {};
    cancelPendingMcLines({ clearCaption: true });
    liveMcSuppressed = false;
    shownLightsForRound.clear();
    stopRenderLoop();
    SFX.stopScene(); // 다음 render()가 idle 오버레이를 다시 켜면서 idle 장면으로 자연스럽게 복귀한다
    stopLiveDistributionPolling();
    raceCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    FX.clear();
    overtakeLayer.innerHTML = "";
    const legendEl = document.getElementById("team-legend");
    if (legendEl) legendEl.innerHTML = "";
    if (countdownTimer) clearInterval(countdownTimer);
    if (bannerHideTimer) clearTimeout(bannerHideTimer);
    bannerHideTimer = null;
    overlayBannerEl.classList.add("hidden");
    phaseLabelEl.textContent = "대기 중";
    phaseTimerEl.textContent = "--";
    roundPillEl.textContent = "READY";
  }
  if (data.type === "camera_mode") {
    cameraMode = data.mode;
  }
  if (data.type === "cheer") {
    spawnCheerBadge(data.emoji);
  }
  if (["phase", "race_tick", "round_revealed", "racing_complete"].includes(data.type)) {
    handleRacingEvent(data);
  }
  if (data.type === "prediction_result") {
    showBanner(`ROUND ${data.round} 예측 채점!`, "순위가 가까울수록 점수가 큽니다", 2200);
    SFX.pass();
    FX.ring(window.innerWidth / 2, window.innerHeight * 0.5, "#ff9f45", 220);
    showMcLine("prediction_result", { round: data.round });
  }
  if (data.type === "prediction_leaderboard") {
    renderPredictionLeaderboard(data.top);
  }
  if (data.type === "prediction_window") {
    lastPredictionWindow = data;
    if (data.state === "open") {
      showMcLine("prediction_open", { round: data.round });
    }
  }
  refresh();
}, "stage");
ws.addEventListener("open", () => {
  refresh();
});

refresh();
