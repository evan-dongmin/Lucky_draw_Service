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

let lastPredictionWindow = null;

const animatedDrawKeys = new Set();
let lastOpeningShownFor = null;
let lastFinalShownFor = null;
let racingStarted = false;
let countdownTimer = null;
let latestDepartmentRates = {};

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
    if (countdownTimer) clearInterval(countdownTimer);
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
