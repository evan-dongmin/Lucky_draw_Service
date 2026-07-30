const views = {
  idle: document.getElementById("idle-view"),
  waiting: document.getElementById("waiting-view"),
  committed: document.getElementById("committed-view"),
  drawing: document.getElementById("drawing-view"),
  racing: document.getElementById("racing-view"),
};
const statusEl = document.getElementById("ws-status");
const participantCountEl = document.getElementById("participant-count");
const departmentCountEl = document.getElementById("department-count");
const commitBadgeEl = document.getElementById("commit-badge");
const reelEl = document.getElementById("reel");
const winnerListEl = document.getElementById("winner-list");
const mcCaptionEl = document.getElementById("mc-caption");
const phaseBannerEl = document.getElementById("racing-phase-banner");
const departmentBarsEl = document.getElementById("department-bars");
const racingFinalEl = document.getElementById("racing-final");
const racingReelEl = document.getElementById("racing-reel");
const racingWinnerListEl = document.getElementById("racing-winner-list");
const predictionLeaderboardEl = document.getElementById("prediction-leaderboard");
const predictionLeaderboardListEl = document.getElementById("prediction-leaderboard-list");
const raceCanvas = document.getElementById("race-canvas");
const raceCtx = raceCanvas.getContext("2d");
const overtakeLayer = document.getElementById("overtake-layer");

let lastPredictionWindow = null;

const animatedDrawKeys = new Set();
let lastOpeningShownFor = null;
let lastFinalShownFor = null;
let racingStarted = false;
let countdownTimer = null;
let latestDepartmentRates = {};

// -- R1~R3: 트랙 렌더러 / 카메라 / 추월 연출 상태 --------------------------
const departmentColorCache = new Map();
let currentPidToGroup = {};
let currentDrawKeyForGroups = null;
let previousTickPositions = {};
let previousTickOrder = [];
let previousTickRound = null;
let lastLaneCount = 20;
let cameraMode = "auto"; // "auto" | "wide" | "medium" | "close" (admin에서 override 가능)

function showView(name) {
  for (const key of Object.keys(views)) {
    views[key].classList.toggle("hidden", key !== name);
  }
}

async function showMcLine(tag) {
  try {
    const result = await fetchJSON(`/api/mc/line/${tag}`);
    mcCaptionEl.textContent = result.text ? `"${result.text}"` : "";
  } catch (e) {
    mcCaptionEl.textContent = "";
  }
}

async function spinReel(el, pool, finalText, totalMs) {
  const start = Date.now();
  while (Date.now() - start < totalMs) {
    el.textContent = pool[Math.floor(Math.random() * pool.length)];
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
  showView("drawing");
  winnerListEl.innerHTML = "";
  for (const winnerId of draw.winners) {
    await spinReel(reelEl, pool, nameById[winnerId] || winnerId, 1800);
    const li = document.createElement("li");
    li.textContent = nameById[winnerId] || winnerId;
    winnerListEl.appendChild(li);
    await delay(500);
  }
  reelEl.textContent = "🎊 추첨 완료!";
}

// ---------------------------------------------------------------------------
// 레이싱 모드: 부서 통과율 실시간 랭킹 + 라운드 진행 상태머신
// ---------------------------------------------------------------------------

function renderPredictionLeaderboard(top) {
  if (!top || !top.length) return;
  predictionLeaderboardEl.classList.remove("hidden");
  predictionLeaderboardListEl.innerHTML = top
    .map((entry) => `<li>${entry.participant_id} - ${entry.score}점</li>`)
    .join("");
}

function renderDepartmentBars(rates) {
  latestDepartmentRates = rates;
  const sorted = Object.entries(rates).sort((a, b) => b[1] - a[1]);
  departmentBarsEl.innerHTML = sorted
    .map(
      ([name, rate]) => `
      <div class="dept-bar-row">
        <div class="dept-bar-label"><span>${name}</span><span>${(rate * 100).toFixed(0)}%</span></div>
        <div class="dept-bar-track"><div class="dept-bar-fill" style="width:${(rate * 100).toFixed(1)}%"></div></div>
      </div>`
    )
    .join("");
}

function hashToUnit(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  return (h % 10000) / 10000; // 0..1
}

function colorForDepartment(name) {
  if (departmentColorCache.has(name)) return departmentColorCache.get(name);
  const hue = Math.floor(hashToUnit(name) * 360);
  const color = `hsl(${hue}, 70%, 60%)`;
  departmentColorCache.set(name, color);
  return color;
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
}

function laneFor(pid, laneCount) {
  return Math.floor(hashToUnit(pid + ":lane") * laneCount);
}

function jitterFor(pid) {
  return hashToUnit(pid + ":jitter") - 0.5; // -0.5..0.5
}

function computeCamera(leaderX, round, W, H) {
  let mode = cameraMode;
  if (mode === "auto") {
    mode = round === 1 ? "wide" : round === 2 ? "medium" : "close";
  }
  if (mode === "wide") return { scale: 1, offsetX: 0, offsetY: 0 };
  const scale = mode === "close" ? 2.2 : 1.4;
  return {
    scale,
    offsetX: W / 2 - leaderX * scale,
    offsetY: H / 2 - (H / 2) * scale,
  };
}

function spawnOvertakeBadge(pid) {
  const lane = laneFor(pid, lastLaneCount);
  const x = previousTickPositions[pid] !== undefined ? previousTickPositions[pid] : 0.5;
  const el = document.createElement("div");
  el.className = "overtake-badge";
  el.textContent = "🚀";
  el.style.left = `${Math.min(96, Math.max(4, x * 100))}%`;
  el.style.top = `${Math.min(94, Math.max(6, ((lane + 0.5) / lastLaneCount) * 100))}%`;
  overtakeLayer.appendChild(el);
  setTimeout(() => el.remove(), 700);
}

function detectOvertakes(sortedIds, round) {
  if (previousTickRound === round && previousTickOrder.length) {
    const prevRank = new Map(previousTickOrder.map((pid, idx) => [pid, idx]));
    let badges = 0;
    for (let i = 0; i < sortedIds.length && badges < 4; i++) {
      const pid = sortedIds[i];
      const prevIdx = prevRank.get(pid);
      if (prevIdx === undefined) continue;
      if (prevIdx - i >= 3) {
        spawnOvertakeBadge(pid);
        badges += 1;
      }
    }
  }
  previousTickOrder = sortedIds;
  previousTickRound = round;
}

function renderTrack(tick) {
  const positions = tick.positions;
  const ids = Object.keys(positions);
  if (!ids.length) return;
  const W = raceCanvas.width;
  const H = raceCanvas.height;

  raceCtx.clearRect(0, 0, W, H);
  raceCtx.fillStyle = "#0f1720";
  raceCtx.fillRect(0, 0, W, H);
  raceCtx.strokeStyle = "rgba(255,255,255,0.04)";
  raceCtx.lineWidth = 1;
  for (let y = 20; y < H; y += 24) {
    raceCtx.beginPath();
    raceCtx.moveTo(0, y);
    raceCtx.lineTo(W, y);
    raceCtx.stroke();
  }

  const sorted = [...ids].sort((a, b) => positions[b] - positions[a]);
  const leaderX = 30 + positions[sorted[0]] * (W - 60);
  const camera = computeCamera(leaderX, tick.round, W, H);

  raceCtx.save();
  raceCtx.translate(camera.offsetX, camera.offsetY);
  raceCtx.scale(camera.scale, camera.scale);

  const lineX = 30 + tick.pass_line * (W - 60);
  raceCtx.strokeStyle = "#ffd166";
  raceCtx.lineWidth = 2 / camera.scale;
  raceCtx.setLineDash([6 / camera.scale, 6 / camera.scale]);
  raceCtx.beginPath();
  raceCtx.moveTo(lineX, 0);
  raceCtx.lineTo(lineX, H);
  raceCtx.stroke();
  raceCtx.setLineDash([]);

  const laneCount = Math.max(6, Math.min(40, ids.length));
  lastLaneCount = laneCount;
  const laneHeight = (H - 40) / laneCount;
  const radius = ids.length > 80 ? 2.5 : ids.length > 20 ? 4 : 7;

  for (let i = sorted.length - 1; i >= 0; i--) {
    const pid = sorted[i];
    const p = positions[pid];
    const x = 30 + p * (W - 60);
    const lane = laneFor(pid, laneCount);
    const y = 20 + lane * laneHeight + jitterFor(pid) * laneHeight * 0.6 + laneHeight / 2;
    const group = currentPidToGroup[pid];
    const color = group ? colorForDepartment(group) : "#8b95a5";
    const isLeader = i === 0;

    raceCtx.globalAlpha = isLeader ? 1 : 0.85;
    raceCtx.fillStyle = color;
    raceCtx.beginPath();
    raceCtx.arc(x, y, isLeader ? radius * 1.6 : radius, 0, Math.PI * 2);
    raceCtx.fill();
    if (isLeader) {
      raceCtx.strokeStyle = "#fff";
      raceCtx.lineWidth = 1.5 / camera.scale;
      raceCtx.stroke();
    }
  }
  raceCtx.globalAlpha = 1;
  raceCtx.restore();

  detectOvertakes(sorted, tick.round);
  previousTickPositions = positions;
}

function startCountdown(durationSeconds, startedAtIso, phaseLabel) {
  if (countdownTimer) clearInterval(countdownTimer);
  const startedAt = new Date(startedAtIso).getTime();
  const update = () => {
    const remain = Math.max(0, durationSeconds - (Date.now() - startedAt) / 1000);
    phaseBannerEl.innerHTML = `${phaseLabel} <span class="countdown">${remain.toFixed(1)}초</span>`;
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
    showView("racing");
    racingFinalEl.classList.add("hidden");
    startCountdown(data.duration_seconds, data.started_at, PHASE_LABELS[data.phase] || data.phase);
    if (data.phase === "race_r1") showMcLine("opening");
    if (data.phase === "race_r3") showMcLine("race_progress");
  } else if (data.type === "race_tick") {
    renderTrack(data);
    if (data.department_live_rate) {
      renderDepartmentBars(data.department_live_rate);
    }
  } else if (data.type === "round_revealed") {
    showMcLine("round_pass_announce");
  } else if (data.type === "racing_complete") {
    if (countdownTimer) clearInterval(countdownTimer);
    phaseBannerEl.textContent = "진행 완료";
  }
}

// ---------------------------------------------------------------------------
// 세션 상태 조회(폴링/이벤트 트리거) -- 룰렛 모드 렌더링
// ---------------------------------------------------------------------------

function render(session) {
  if (!session) {
    showView("idle");
    statusEl.textContent = "명단 등록을 기다리는 중입니다";
    return;
  }
  const latest = session.draws[session.draws.length - 1];
  if (!latest) {
    const departmentCount = new Set(session.participants.map((p) => p.team || "미지정")).size;
    participantCountEl.textContent = session.participants.length;
    departmentCountEl.textContent = departmentCount;
    showView("waiting");
    return;
  }

  if (session.mode === "racing") {
    ensureGroupLookup(latest, latest.commit);
    if (latest.revealed) {
      showView("racing");
      racingFinalEl.classList.remove("hidden");
      racingReelEl.textContent = "🎊 최종 당첨자 발표!";
      racingWinnerListEl.innerHTML = latest.winners
        .map((id) => {
          const p = latest.snapshot.participants.find((x) => x.id === id);
          return `<li>${p ? participantLabel(p) : id}</li>`;
        })
        .join("");
      if (lastFinalShownFor !== "racing-final") {
        lastFinalShownFor = "racing-final";
        showMcLine("final_announce").then(() => delay(2500)).then(() => showMcLine("verification"));
      }
      return;
    }
    if (racingStarted) {
      showView("racing");
      return; // race_tick/phase 이벤트가 실시간 갱신을 담당
    }
    commitBadgeEl.textContent = latest.commit;
    showView("committed");
    if (lastOpeningShownFor !== latest.commit) {
      lastOpeningShownFor = latest.commit;
      showMcLine("opening");
    }
    return;
  }

  // 룰렛 모드
  if (!latest.revealed) {
    commitBadgeEl.textContent = latest.commit;
    showView("committed");
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
    showView("drawing");
    winnerListEl.innerHTML = latest.winners
      .map((id) => {
        const p = latest.snapshot.participants.find((x) => x.id === id);
        return `<li>${p ? participantLabel(p) : id}</li>`;
      })
      .join("");
    reelEl.textContent = "🎊 추첨 완료!";
  }
}

document.getElementById("btn-demo-start").addEventListener("click", async (event) => {
  event.target.disabled = true;
  event.target.textContent = "데모 준비 중...";
  try {
    await fetchJSON("/api/demo/start", { method: "POST", body: JSON.stringify({}) });
  } catch (e) {
    alert(e.message);
    event.target.disabled = false;
    event.target.textContent = "🚀 데모로 체험하기";
  }
});

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
    previousTickPositions = {};
    previousTickOrder = [];
    previousTickRound = null;
    raceCtx.clearRect(0, 0, raceCanvas.width, raceCanvas.height);
    overtakeLayer.innerHTML = "";
    if (countdownTimer) clearInterval(countdownTimer);
  }
  if (data.type === "camera_mode") {
    cameraMode = data.mode;
  }
  if (["phase", "race_tick", "round_revealed", "racing_complete"].includes(data.type)) {
    handleRacingEvent(data);
  }
  if (data.type === "prediction_leaderboard") {
    renderPredictionLeaderboard(data.top);
  }
  if (data.type === "prediction_window") {
    lastPredictionWindow = data;
  }
  refresh();
}, "stage");
ws.addEventListener("open", () => {
  refresh();
});

refresh();
