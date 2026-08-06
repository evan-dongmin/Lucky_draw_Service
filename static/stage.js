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
  idle: "idle",
  waiting: "idle",
  committed: "anticipation",
  roulette: "roulette",
  podium: "victory",
};

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
  gambling_open: { rate: 1.1, pitch: 1.06 },
  gambling_result: { rate: 1.12, pitch: 1.08 },
  gambling_champion: { rate: 1.1, pitch: 1.08 },
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

async function showMcLine(tag, params) {
  try {
    const qs = params
      ? "?" +
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== null)
          .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
          .join("&")
      : "";
    const result = await fetchJSON(`/api/mc/line/${tag}${qs}`);
    if (result.text) {
      mcCaptionEl.textContent = `"${result.text}"`;
      speak(result.text, tag);
    } else {
      mcCaptionEl.textContent = "";
    }
    return result.text;
  } catch (e) {
    mcCaptionEl.textContent = "";
    return "";
  }
}

// 레이스 도중 이벤트(선두 교체·추월)마다 매번 호출하면 자막이 정신없이
// 바뀌므로, 최소 간격을 두고 그 사이 이벤트는 걸러낸다.
const MC_LIVE_COOLDOWN_MS = 4500;
let lastMcLiveCallAt = 0;
function tryShowLiveMcLine(tag, params) {
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

function renderPredictionLeaderboard(top, mode) {
  if (!top || !top.length) return;
  predictionLeaderboardEl.classList.remove("hidden");
  const titleEl = predictionLeaderboardEl.querySelector("h3");
  if (titleEl) titleEl.textContent = mode === "gambling" ? "사이버머니 리더보드" : "예측 리더보드";
  predictionLeaderboardListEl.innerHTML = top
    .map((entry) =>
      mode === "gambling"
        ? `<li>${entry.participant_id} - ${entry.balance}</li>`
        : `<li>${entry.participant_id} - ${entry.score}점</li>`
    )
    .join("");
}

// ---------------------------------------------------------------------------
// 갬블링 실시간 배당률 패널(우측) -- 베팅/선택 창이 열려 있는 동안만 폴링한다.
// ---------------------------------------------------------------------------

let sessionPredictionMode = "confidence";
let liveOddsTimer = null;
const gamblingOddsPanelEl = document.getElementById("gambling-odds-panel");
const gamblingOddsListEl = document.getElementById("gambling-odds-list");

async function pollLiveOdds() {
  try {
    const data = await fetchJSON("/api/predict/live");
    if (data.mode !== "gambling") {
      stopLiveOddsPolling();
      return;
    }
    const rounds = data.rounds || {};
    const roundKeys = Object.keys(rounds);
    if (!roundKeys.length) {
      gamblingOddsPanelEl.classList.add("hidden");
      return;
    }
    gamblingOddsPanelEl.classList.remove("hidden");
    gamblingOddsListEl.innerHTML = roundKeys
      .map((r) => {
        const stat = rounds[r];
        const rows = Object.entries(stat.pool || {})
          .sort((a, b) => b[1] - a[1])
          .map(
            ([target, amount]) =>
              `<div class="odds-row"><span>${target}</span><span>${amount}</span><span>${
                stat.odds && stat.odds[target] ? stat.odds[target] + "배" : "-"
              }</span></div>`
          )
          .join("");
        return `<div class="odds-round-block"><div class="odds-round-title">R${r} · 총 판돈 ${stat.total_pool}</div>${rows}</div>`;
      })
      .join("");
  } catch (e) {
    // 세션 없음/리셋 직후 등 -- 다음 폴링에서 자연 복구되므로 조용히 무시
  }
}

function startLiveOddsPolling() {
  if (liveOddsTimer) return;
  liveOddsTimer = setInterval(pollLiveOdds, 2000);
  pollLiveOdds();
}

function stopLiveOddsPolling() {
  if (liveOddsTimer) {
    clearInterval(liveOddsTimer);
    liveOddsTimer = null;
  }
  gamblingOddsPanelEl.classList.add("hidden");
}

async function handleGamblingResult(data) {
  showBanner(`ROUND ${data.round} 베팅 정산!`, `총 판돈 ${data.total_pool}`, 2200);
  SFX.pass();
  FX.ring(window.innerWidth / 2, window.innerHeight * 0.5, "#ff9f45", 220);
  showMcLine("gambling_result", { round: data.round });
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

// -- 장애물(연출 전용 -- 순위/progress 값에는 절대 영향 없음) --------------
let activeObstacles = [];
let nextObstacleSpawnAt = 0;
let obstacleSeq = 0;
let kartWobbleUntil = {};
const OBSTACLE_MAX_ACTIVE = 3;
const OBSTACLE_TYPES = ["oil", "cone", "tire"];
const WOBBLE_DURATION_MS = 260;

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

function renderTeamLegend(groupNames) {
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
const TRACK_DEFS = {
  1: {
    // 웨이포인트 수를 줄이고 간격을 넓혀 방향 전환 지점(=곡률이 가장
    // 큰 지점)의 곡률 자체를 낮췄다 -- ΔY/ΔT 비율을 낮춰도 halfWidth가
    // 넓으면 커브 스트라이프(외곽 연석)가 방향 전환 지점에서 서로
    // 겹쳐 보이는 걸 스크린샷으로 실제 확인했다(리본 자체가 아니라
    // 연석 사각형들이 급격한 회전 때문에 겹치는 문제). 250대가 한눈에
    // 들어와야 하는 R1은 어차피 시각 디테일보다 가독성이 중요하므로
    // 가장 완만하게 뒀다.
    t: [0, 0.25, 0.5, 0.75, 1.0],
    yFrac: [0, -0.2, 0.2, -0.15, 0],
    halfWidthFrac: 0.55,
  },
  2: {
    t: [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    yFrac: [0, -0.2, -0.34, -0.16, 0.18, 0.34, 0.18, -0.14, -0.3, -0.1, 0],
    halfWidthFrac: 0.5,
  },
  3: {
    t: [0, 0.08, 0.16, 0.24, 0.32, 0.42, 0.52, 0.62, 0.72, 0.82, 0.91, 1.0],
    yFrac: [0, -0.16, -0.3, -0.1, 0.17, 0.32, 0.12, -0.22, -0.32, -0.08, 0.22, 0],
    halfWidthFrac: 0.48,
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
  const trackTop = H * 0.14;
  const trackBottom = H * 0.86;
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
    mode = round === 1 ? "wide" : round === 2 ? "medium" : "close";
  }
  if (mode === "wide") return { scale: 1, offsetX: 0, offsetY: 0 };
  const scale = mode === "close" ? 2.2 : 1.4;
  return {
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

function drawKart(ctx, x, y, h, angle, color, glow, isLeader, atRisk, pulse, wobble) {
  const w = h * (SPRITE_W / SPRITE_H);
  // 장애물 근처를 지날 때의 "부딪힌 척" 흔들림 -- 순수 연출, 위치 데이터는 불변
  const wobbleAngle = wobble ? Math.sin(performance.now() * 0.045) * 0.22 * wobble : 0;
  const wobbleScale = wobble ? 1 - 0.08 * wobble : 1;

  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle + wobbleAngle);
  ctx.scale(wobbleScale, wobbleScale);

  // 배기 연기 / 속도 자국. 진행 방향 반대쪽(로컬 -x)에 그려 회전해도 항상 뒤쪽에 남는다.
  if (h >= 9) {
    for (let t = 1; t <= 3; t++) {
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
    ctx.rotate(-(angle + wobbleAngle));
    ctx.font = `${Math.round(h * 0.8)}px serif`;
    ctx.textAlign = "center";
    ctx.fillText("👑", 0, -h * 0.9);
    ctx.restore();
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// 장애물: 마블 레이스 느낌의 움직이는 방해물(연출 전용). 트랙 위를 살짝
// 좌우로 요동치며 떠 있다가, 카트가 근처를 지나면 drawKart의 wobble 흔들림만
// 유발한다 -- positions[pid](순위 판정 값)는 이 파일 어디서도 바꾸지 않는다.
// ---------------------------------------------------------------------------

const obstacleSpriteCache = new Map();
const OBSTACLE_EMOJI = { oil: "🛢️", cone: "🚧", tire: "🛞" };

function buildObstacleSprite(type) {
  const c = document.createElement("canvas");
  c.width = 56;
  c.height = 56;
  const g = c.getContext("2d");
  g.font = "40px serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(OBSTACLE_EMOJI[type] || "🚧", 28, 30);
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

function drawObstacle(ctx, x, y, size, type) {
  ctx.save();
  ctx.globalAlpha = 0.94;
  ctx.shadowColor = "rgba(0,0,0,0.5)";
  ctx.shadowBlur = 4;
  ctx.drawImage(obstacleSpriteFor(type), x - size / 2, y - size / 2, size, size);
  ctx.restore();
}

// 리더 진행률 기준으로 앞쪽에 장애물을 스폰하고, 리더가 한참 지나친 것은
// 제거한다. 동시 활성 개수를 참가자 수·라운드와 무관하게 소수로 고정해
// R1(최대 250대, 와이드 뷰)에서도 화면이 안 빽빽하게 유지한다.
function maybeSpawnObstacle(now, leaderProgress) {
  activeObstacles = activeObstacles.filter((o) => leaderProgress - o.spawnProgress < 0.06);
  if (activeObstacles.length >= OBSTACLE_MAX_ACTIVE) return;
  if (now < nextObstacleSpawnAt) return;
  activeObstacles.push({
    id: `obs-${obstacleSeq++}`,
    type: OBSTACLE_TYPES[Math.floor(Math.random() * OBSTACLE_TYPES.length)],
    spawnProgress: Math.min(0.97, leaderProgress + 0.12 + Math.random() * 0.12),
    laneCenter: Math.random() * lastLaneCount,
    weaveAmp: 1.5 + Math.random() * 2,
    weaveSpeed: 0.0015 + Math.random() * 0.0012,
    spawnedAt: now,
  });
  nextObstacleSpawnAt = now + 2200 + Math.random() * 1800;
}

function obstacleScreenPoints(now, laneCount, laneWidth, round) {
  return activeObstacles.map((o) => {
    const weaveLane = o.laneCenter + Math.sin((now - o.spawnedAt) * o.weaveSpeed) * o.weaveAmp;
    const laneOffset = (weaveLane - (laneCount - 1) / 2) * laneWidth;
    const center = trackPointAt(o.spawnProgress, round);
    const nx = -Math.sin(center.angle);
    const ny = Math.cos(center.angle);
    return { ...o, x: center.x + nx * laneOffset, y: center.y + ny * laneOffset };
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
  const { lut, length: trackLength, halfWidth } = getTrackLUT(tick.round);
  const leaderPoint = trackPointAt(leaderPos, tick.round);
  const camera = computeCamera(leaderPoint, tick.round, W, H);

  raceCtx.save();
  raceCtx.translate(camera.offsetX, camera.offsetY);
  raceCtx.scale(camera.scale, camera.scale);

  drawTrackSurface(raceCtx, W, H, lut, halfWidth, tick.pass_line, leaderPos, trackLength, tick.round);
  drawFinishLine(raceCtx, trackPointAt(tick.pass_line, tick.round), halfWidth);

  const laneCount = Math.max(6, Math.min(36, ids.length));
  lastLaneCount = laneCount;
  const laneWidth = (halfWidth * 2) / laneCount;
  const kartH = Math.max(5, Math.min(64, laneWidth * 0.82));

  maybeSpawnObstacle(now, leaderPos);
  const obstaclePoints = obstacleScreenPoints(now, laneCount, laneWidth, tick.round);
  const obstacleSize = Math.max(16, kartH * 1.4);
  for (const o of obstaclePoints) {
    drawObstacle(raceCtx, o.x, o.y, obstacleSize, o.type);
  }
  const hitRadius = Math.max(14, kartH * 1.3);

  let riskCount = 0;
  for (let i = sorted.length - 1; i >= 0; i--) {
    const pid = sorted[i];
    const p = positions[pid];
    const lane = laneFor(pid, laneCount);
    const laneOffset = (lane - (laneCount - 1) / 2) * laneWidth + jitterFor(pid) * laneWidth * 0.5;
    const center = trackPointAt(p, tick.round);
    const nx = -Math.sin(center.angle);
    const ny = Math.cos(center.angle);
    const x = center.x + nx * laneOffset;
    const y = center.y + ny * laneOffset;

    // 장애물 근처를 지나면 짧게 흔들리는 연출만 유발한다 -- p(진행률)는 불변
    for (const o of obstaclePoints) {
      const dx = x - o.x;
      const dy = y - o.y;
      if (dx * dx + dy * dy < hitRadius * hitRadius) {
        kartWobbleUntil[pid] = now + WOBBLE_DURATION_MS;
        break;
      }
    }
    const wobbleRemain = kartWobbleUntil[pid] ? kartWobbleUntil[pid] - now : 0;
    const wobble = wobbleRemain > 0 ? wobbleRemain / WOBBLE_DURATION_MS : 0;

    const group = currentPidToGroup[pid];
    const isLeader = i === 0;
    const atRisk = !isLeader && p < tick.pass_line && tick.pass_line - p < 0.06;
    if (atRisk) riskCount += 1;

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
      wobble
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

  // 클로즈콜: 통과선 근처에 여러 대가 몰려 있으면 긴장 멘트 + 심장박동
  if (riskCount >= 3) {
    tryShowLiveMcLine("close_call");
  }

  // 파이널 랩: 라운드당 한 번, 진행률 85% 지점에서
  if (tick.progress_ratio >= 0.85 && finalLapShownForRound !== tick.round) {
    finalLapShownForRound = tick.round;
    showBanner("FINAL LAP", "마지막 스퍼트!", 1500);
    showMcLine("final_lap");
    SFX.heartbeat();
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
    if (["race_r1", "race_r2", "race_r3"].includes(data.phase)) {
      currentLeaderDept = null;
      const roundIndex = RACE_ROUND_INDEX_LOCAL[data.phase];
      finalLapShownForRound = null;
      if (roundIndex === 3) photoFinishShownForRound = null;
      runStartLights(roundIndex);
      SFX.startEngine();
      SFX.playScene("race", { round: roundIndex });
      fetchCharacterChoices(); // 레이스 시작 직전 최종 선택 스냅샷(매 라운드 -- 라운드 사이 선택창에서 바꾼 캐릭터도 반영)
    } else {
      SFX.stopEngine();
      FX.setSpeedLines(0);
      // 결과 발표 구간(final_announce/verify)은 승리감 있는 장면으로, 그 외
      // (오프닝/락/선택창)는 다음 결정을 기다리는 긴장감 있는 장면으로.
      SFX.playScene(data.phase === "final_announce" || data.phase === "verify" ? "victory" : "anticipation");
    }
    if (data.phase === "race_r1") showMcLine("opening");
    if (data.phase === "race_r3") showMcLine("race_progress");
    if (data.phase === "score_r1_select_r2" || data.phase === "score_r2_select_r3") {
      if (lastPredictionWindow && lastPredictionWindow.state === "open") {
        const tag = lastPredictionWindow.mode === "gambling" ? "gambling_open" : "prediction_open";
        showMcLine(tag, { round: lastPredictionWindow.round });
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
    FX.ring(window.innerWidth / 2, window.innerHeight * 0.5, "#7cf29c", 220);
    showMcLine("round_pass_announce", { pass_count: data.pass_ids ? data.pass_ids.length : undefined }).then(
      () => delay(2200)
    ).then(() => showMcLine("elimination"));
  } else if (data.type === "racing_complete") {
    if (countdownTimer) clearInterval(countdownTimer);
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
  gambling: "사이버머니 갬블링 최종 리더보드 기준 당첨자입니다 -- 레이스 순위와 다를 수 있습니다",
  confidence: "확신도 예측 최종 리더보드 기준 당첨자입니다 -- 레이스 순위와 다를 수 있습니다",
};

function buildPodium(winnerIds, nameById, basis) {
  if (podiumBasisHintEl) podiumBasisHintEl.textContent = PRIZE_BASIS_LABEL[basis] || "";
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
      return `<div class="podium-slot" data-rank="${rank}" style="animation-delay:${i * 0.15}s">
        <div class="podium-name">${label}</div>
        <div class="podium-block">${medal}</div>
      </div>`;
    })
    .join("");
  podiumRestEl.innerHTML = rest.map((id) => `<li>${nameById[id] || id}</li>`).join("");
}

async function playFinalReveal(winnerIds, nameById, basis) {
  showBanner("🏆 최종 당첨자 발표!", "", 1500);
  SFX.drumroll(1.4);
  await delay(1500);
  buildPodium(winnerIds, nameById, basis);
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

  sessionPredictionMode = session.prediction_mode || "confidence";
  if (session.mode === "racing" && sessionPredictionMode === "gambling") {
    startLiveOddsPolling();
  }

  if (session.mode === "racing") {
    ensureGroupLookup(latest, latest.commit);
    if (latest.revealed) {
      stopLiveOddsPolling();
      if (!latest.prize_winners) {
        // 레이스는 리빌됐지만 예측/갬블링 라운드 3 최종 채점이 아직 안
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
        playFinalReveal(latest.prize_winners, nameById, latest.prize_basis)
          .then(() => delay(2500))
          .then(() => (sessionPredictionMode === "gambling" ? showMcLine("gambling_champion") : Promise.resolve()))
          .then(() => delay(sessionPredictionMode === "gambling" ? 2500 : 0))
          .then(() => showMcLine("verification"));
      } else if (overlays.podium.classList.contains("hidden") && bannerHideTimer === null) {
        // 새로고침 등으로 재진입한 경우 이미 지나간 연출 없이 바로 시상대만 표시
        const nameById = Object.fromEntries(
          latest.snapshot.participants.map((p) => [p.id, participantLabel(p)])
        );
        buildPodium(latest.prize_winners, nameById, latest.prize_basis);
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

async function startDemo(button, predictionMode) {
  unlockAudioOnce();
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "데모 준비 중...";
  try {
    await fetchJSON("/api/demo/start", {
      method: "POST",
      body: JSON.stringify({ prediction_mode: predictionMode }),
    });
  } catch (e) {
    alert(e.message);
    button.disabled = false;
    button.textContent = originalText;
  }
}

document.getElementById("btn-demo-start").addEventListener("click", (e) => startDemo(e.target, "confidence"));
document
  .getElementById("btn-demo-start-gambling")
  .addEventListener("click", (e) => startDemo(e.target, "gambling"));

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
    kartWobbleUntil = {};
    shownLightsForRound.clear();
    stopRenderLoop();
    SFX.stopScene(); // 다음 render()가 idle 오버레이를 다시 켜면서 idle 장면으로 자연스럽게 복귀한다
    stopLiveOddsPolling();
    sessionPredictionMode = "confidence";
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
  if (data.type === "prediction_leaderboard") {
    renderPredictionLeaderboard(data.top, data.mode);
  }
  if (data.type === "gambling_result") {
    handleGamblingResult(data);
  }
  if (data.type === "prediction_window") {
    lastPredictionWindow = data;
    if (data.state === "open") {
      showMcLine(data.mode === "gambling" ? "gambling_open" : "prediction_open", { round: data.round });
    }
  }
  refresh();
}, "stage");
ws.addEventListener("open", () => {
  refresh();
});

refresh();
