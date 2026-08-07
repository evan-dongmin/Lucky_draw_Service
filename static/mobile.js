const TOKEN_KEY = "luckydraw_predict_token";

const onboardingViewEl = document.getElementById("onboarding-view");
const stepDepartmentEl = document.getElementById("onboarding-step-department");
const stepNameEl = document.getElementById("onboarding-step-name");
const stepCharacterEl = document.getElementById("onboarding-step-character");
const departmentListEl = document.getElementById("department-list");
const nameListEl = document.getElementById("name-list");
const characterListEl = document.getElementById("character-list");
const onboardingErrorEl = document.getElementById("onboarding-error");
const gameViewEl = document.getElementById("game-view");
const myNameEl = document.getElementById("my-name");
const myScoreEl = document.getElementById("my-score");
const raceStatusPanelEl = document.getElementById("race-status-panel");
const raceStatusRankEl = document.getElementById("race-status-rank");
const raceStatusPassEl = document.getElementById("race-status-pass");
const raceStatusProgressWrapEl = document.getElementById("race-status-progress-wrap");
const raceStatusProgressBarEl = document.getElementById("race-status-progress-bar");
const raceStatusProgressLabelEl = document.getElementById("race-status-progress-label");
const raceStatusDepartmentEl = document.getElementById("race-status-department");
const raceStatusPointEl = document.getElementById("race-status-point");
const myCharacterDisplayEl = document.getElementById("my-character-display");
const cardsEl = document.getElementById("cards");
const predictionsOffNoteEl = document.getElementById("predictions-off-note");
const leaderboardPanelEl = document.getElementById("leaderboard-panel");
const leaderboardTitleEl = document.getElementById("leaderboard-title");
const leaderboardListEl = document.getElementById("leaderboard-list");
const characterOverlayEl = document.getElementById("character-overlay");
const characterOverlayListEl = document.getElementById("character-overlay-list");
const cheerButtonsEl = document.getElementById("cheer-buttons");

const CHEER_EMOJI = ["🔥", "👏", "🎉", "💪", "😱", "⚡", "❤️", "😂"]; // app/main.py CHEER_EMOJI_ALLOWLIST와 반드시 일치시킬 것

const ROUND_LABELS = { 1: "1라운드", 2: "2라운드", 3: "3라운드" };
const STATE_LABELS = { pending: "대기 중", open: "선택 중", locked: "확정" };

let departmentsData = {};
let characterRoster = [];
let myToken = localStorage.getItem(TOKEN_KEY);
let myParticipantId = null;
let myName = "";
let myCharacterId = null;
let liveRefreshTimer = null;
let currentPhase = null; // 서버 phase WS 메시지의 phase 문자열(예: "race_r1") -- 레이스 중인지 판단용

function showOnboarding() {
  onboardingViewEl.classList.remove("hidden");
  gameViewEl.classList.add("hidden");
}

function showGame() {
  onboardingViewEl.classList.add("hidden");
  gameViewEl.classList.remove("hidden");
}

async function loadDepartments() {
  try {
    departmentsData = await fetchJSON("/api/predict/departments");
  } catch (e) {
    onboardingErrorEl.textContent = "아직 명단이 등록되지 않았거나 레이싱 세션이 아닙니다.";
    return;
  }
  departmentListEl.innerHTML = Object.keys(departmentsData)
    .map((name) => `<button class="choice-btn" data-dept="${name}">${name}</button>`)
    .join("");
  for (const btn of departmentListEl.querySelectorAll(".choice-btn")) {
    btn.addEventListener("click", () => selectDepartment(btn.dataset.dept));
  }
}

function selectDepartment(name) {
  const members = departmentsData[name] || [];
  // 동명이인 구분을 위해 사번(id) 뒷자리를 병기
  nameListEl.innerHTML = members
    .map((m) => {
      const dupe = members.filter((x) => x.name === m.name).length > 1;
      const label = dupe ? `${m.name}(${m.id.slice(-3)})` : m.name;
      return `<button class="choice-btn" data-id="${m.id}" data-name="${m.name}">${label}</button>`;
    })
    .join("");
  for (const btn of nameListEl.querySelectorAll(".choice-btn")) {
    btn.addEventListener("click", () => join(btn.dataset.id, btn.dataset.name));
  }
  stepDepartmentEl.classList.add("hidden");
  stepNameEl.classList.remove("hidden");
}

document.getElementById("btn-back-to-department").addEventListener("click", () => {
  stepNameEl.classList.add("hidden");
  stepDepartmentEl.classList.remove("hidden");
});

// ---------------------------------------------------------------------------
// 캐릭터/카트 선택 -- 순수 연출용(순위·통과 여부에 영향 없음). 고르지
// 않으면 무대 화면이 소속 부서 기준으로 자동 배정한다.
// ---------------------------------------------------------------------------

async function loadCharacterRoster() {
  if (characterRoster.length) return characterRoster;
  try {
    const result = await fetchJSON("/api/character/roster");
    characterRoster = result.roster || [];
  } catch (e) {
    characterRoster = [];
  }
  return characterRoster;
}

function characterButtonsHtml(selectedId) {
  return characterRoster
    .map(
      (c) => `<button class="choice-btn character-btn ${c.id === selectedId ? "selected" : ""}" data-id="${c.id}">
        <span class="character-emoji">${c.emoji}</span><span class="character-label">${c.label}</span>
      </button>`
    )
    .join("");
}

async function showCharacterStep() {
  await loadCharacterRoster();
  characterListEl.innerHTML = characterButtonsHtml(myCharacterId);
  for (const btn of characterListEl.querySelectorAll(".character-btn")) {
    btn.addEventListener("click", () => chooseCharacter(btn.dataset.id, false));
  }
  stepNameEl.classList.add("hidden");
  stepCharacterEl.classList.remove("hidden");
}

async function chooseCharacter(characterId, fromOverlay) {
  try {
    await fetchJSON("/api/character/choose", {
      method: "POST",
      body: JSON.stringify({ token: myToken, character_id: characterId }),
    });
    myCharacterId = characterId;
    renderMyCharacterDisplay();
  } catch (e) {
    alert(e.message);
    return;
  }
  if (fromOverlay) {
    closeCharacterOverlay();
  } else {
    showGame();
    await refreshMe();
    await refreshLeaderboard();
  }
}

function renderMyCharacterDisplay() {
  const found = characterRoster.find((c) => c.id === myCharacterId);
  myCharacterDisplayEl.textContent = found ? `${found.emoji} ${found.label}` : "카트 미선택(부서 기준 자동 배정)";
}

async function openCharacterOverlay() {
  await loadCharacterRoster();
  characterOverlayListEl.innerHTML = characterButtonsHtml(myCharacterId);
  for (const btn of characterOverlayListEl.querySelectorAll(".character-btn")) {
    btn.addEventListener("click", () => chooseCharacter(btn.dataset.id, true));
  }
  characterOverlayEl.classList.remove("hidden");
}

function closeCharacterOverlay() {
  characterOverlayEl.classList.add("hidden");
}

document.getElementById("btn-change-character").addEventListener("click", openCharacterOverlay);
document.getElementById("btn-close-character-overlay").addEventListener("click", closeCharacterOverlay);
document.getElementById("btn-skip-character").addEventListener("click", async () => {
  renderMyCharacterDisplay();
  showGame();
  await refreshMe();
  await refreshLeaderboard();
});

async function refreshMyCharacter() {
  if (!myToken) return;
  try {
    const result = await fetchJSON(`/api/character/me?token=${encodeURIComponent(myToken)}`);
    myCharacterId = result.character_id;
    renderMyCharacterDisplay();
  } catch (e) {
    /* 캐릭터 조회 실패는 치명적이지 않다 -- 표시만 비워둔다 */
  }
}

async function join(participantId, name) {
  onboardingErrorEl.textContent = "";
  try {
    const result = await fetchJSON("/api/predict/join", {
      method: "POST",
      body: JSON.stringify({ participant_id: participantId }),
    });
    myToken = result.token;
    myParticipantId = result.participant_id;
    myName = name;
    localStorage.setItem(TOKEN_KEY, myToken);
    await showCharacterStep();
  } catch (e) {
    onboardingErrorEl.textContent = e.message;
  }
}

async function tryRestoreSession() {
  if (!myToken) {
    showOnboarding();
    await loadDepartments();
    return;
  }
  try {
    const me = await fetchJSON(`/api/predict/me?token=${encodeURIComponent(myToken)}`);
    myParticipantId = me.participant_id;
    showGame();
    renderMe(me);
    await refreshMyCharacter();
    if (me.predictions_enabled) await refreshLeaderboard();
  } catch (e) {
    if (e.status === 401 || e.status === 404) {
      // 토큰이 실제로 무효(세션 리셋 등) -- 온보딩부터 다시 시작.
      resetToOnboarding();
    } else {
      // 페이지를 막 열었는데 네트워크가 불안정한 경우 -- 토큰은 지우지
      // 않고 온보딩 화면만 우선 보여준 뒤 잠시 후 복원을 재시도한다.
      onboardingErrorEl.textContent = "연결이 불안정합니다. 잠시 후 다시 시도합니다...";
      showOnboarding();
      setTimeout(tryRestoreSession, 3000);
    }
  }
}

function cardStateFor(me, round) {
  if (me.card.locked[round]) return "locked";
  if (me.round_state[round] === "open") return "open";
  return "pending";
}

// ---------------------------------------------------------------------------
// 예측 카드 렌더링 (무손실 -- 라운드당 1픽 + 순위 차등 채점)
// ---------------------------------------------------------------------------

// 라운드별 점수 내역. 서버가 card.rewards에 항목별로 남겨준 값을 그대로
// 보여준다 -- "왜 몇 점을 받았는지"가 폰에서 바로 보여야 한다는 사용자 요청.
const TEAM_RANK_LABEL = { 1: "🥇 우리 팀 1위", 2: "🥈 우리 팀 2위", 3: "🥉 우리 팀 3위" };
const HIT_RANK_LABEL = { 1: "🎯 1위 적중", 2: "2위 예측", 3: "3위 예측" };

function renderRewardBreakdown(me, round) {
  const reward = me.card.rewards ? me.card.rewards[round] : null;
  if (!reward) return "";
  const items = [];
  if (reward.predict !== undefined) {
    const label = HIT_RANK_LABEL[reward.hit_rank] || `${reward.hit_rank || "순위 밖"} 예측`;
    items.push(`<li>${label} <b>+${reward.predict}</b></li>`);
  }
  if (reward.finish) items.push(`<li>🏁 결승선 통과 <b>+${reward.finish}</b></li>`);
  if (reward.team_bonus) {
    const label = TEAM_RANK_LABEL[reward.team_rank] || "우리 팀 순위 보상";
    items.push(`<li>${label} <b>+${reward.team_bonus}</b></li>`);
  }
  if (reward.final) items.push(`<li>🏆 결선 당첨 <b>+${reward.final}</b></li>`);
  if (!items.length) return "";
  return `
    <div class="reward-box">
      <div class="reward-title">이번 라운드 점수 <span class="reward-total">+${reward.total}</span></div>
      <ul class="reward-list">${items.join("")}</ul>
    </div>
  `;
}

function renderPredictionCard(me, round) {
  const state = cardStateFor(me, round);
  const target = me.card.target[round];
  const isAuto = me.card.is_auto[round];

  let targetHtml = "";
  if (state === "pending") {
    targetHtml = `<p>대상은 이 라운드가 시작될 때 선택할 수 있습니다.</p>`;
  } else if (state === "open") {
    const candidates = me.round_candidates[round] || [];
    const dist = (me.live && me.live[round] && me.live[round].distribution) || {};
    // 미선택 시 자동 배정 규칙이 라운드마다 다르다 -- R1·R2는 자기 부서,
    // R3는 무작위(결선 진출자 개인이라 "자기 팀"이 없다).
    const autoNote =
      round === 3
        ? "미선택 시 무작위 배정"
        : "미선택 시 <strong>우리 부서</strong>가 자동 선택됩니다";
    targetHtml =
      `<p>지금 선택하세요! (${autoNote})</p>` +
      `<div class="choice-list">` +
      candidates
        .map((c) => {
          const pct = dist[c] ? Math.round(dist[c] * 100) : 0;
          return `<button class="choice-btn target-btn ${target === c ? "selected" : ""}" data-round="${round}" data-target="${c}">${c}<span class="pct-label">${pct}%</span></button>`;
        })
        .join("") +
      `</div>`;
  } else {
    const autoLabel = isAuto ? (round === 3 ? " (무작위 배정)" : " (우리 부서 자동 선택)") : "";
    targetHtml = `<p>선택 결과: <strong>${target}</strong>${autoLabel}</p>`;
  }

  return `
    <div class="pred-card state-${state}">
      <div class="pred-card-header">
        <strong>${ROUND_LABELS[round]}</strong>
        <span class="pred-card-badge">${STATE_LABELS[state]}</span>
      </div>
      ${targetHtml}
      ${renderRewardBreakdown(me, round)}
    </div>
  `;
}

async function chooseTarget(round, target) {
  try {
    await fetchJSON("/api/predict/choose", {
      method: "POST",
      body: JSON.stringify({ token: myToken, round, target }),
    });
    await refreshMe();
  } catch (e) {
    alert(e.message);
  }
}

// ---------------------------------------------------------------------------
// 공용 렌더/새로고침
// ---------------------------------------------------------------------------

// 레이스 진행 중 "내 카트 현황"(등수/통과 여부/통과선까지 진행률) +
// 우리 팀 순위 + 포인트 순위를 그린다(사용자 요청). 서버가 250명 전체
// 위치를 모바일에 뿌리지 않는다는 원칙은 그대로라, /api/predict/me가
// 폴링 시점에 이 참가자 한 명분만 계산해 내려준 값을 그대로 표시할
// 뿐이다(app/main.py의 _my_race_status/_my_department_rank 참고).
function renderRaceStatus(me) {
  const rs = me.race_status;
  const dept = me.department_rank;
  const hasPoint = me.point_rank != null;
  if (!rs && !dept && !hasPoint) {
    raceStatusPanelEl.classList.add("hidden");
    return;
  }
  raceStatusPanelEl.classList.remove("hidden");

  if (rs) {
    raceStatusRankEl.textContent = `내 카트 등수: ${rs.rank}위 / ${rs.total}명 (${ROUND_LABELS[rs.round] || `R${rs.round}`})`;
    if (rs.passed) {
      raceStatusPassEl.textContent = "✅ 통과선 통과!";
      raceStatusPassEl.className = "race-status-line race-status-passed";
      raceStatusProgressWrapEl.classList.add("hidden");
    } else {
      raceStatusPassEl.textContent = "⏳ 진행 중";
      raceStatusPassEl.className = "race-status-line race-status-pending";
      raceStatusProgressWrapEl.classList.remove("hidden");
      raceStatusProgressBarEl.style.width = `${rs.progress_to_pass_pct}%`;
      raceStatusProgressLabelEl.textContent = `통과선까지 ${rs.progress_to_pass_pct}% 진행`;
    }
  } else {
    raceStatusRankEl.textContent = "";
    raceStatusPassEl.textContent = "";
    raceStatusProgressWrapEl.classList.add("hidden");
  }

  raceStatusDepartmentEl.textContent = dept
    ? `우리 팀(${dept.department}) 순위: ${dept.rank}위 / ${dept.total} (통과율 ${Math.round(dept.rate * 100)}%)`
    : "";
  raceStatusPointEl.textContent = hasPoint ? `포인트 순위: ${me.point_rank}위 / ${me.point_total}명` : "";
}

function renderMe(me) {
  myNameEl.textContent = myName || me.participant_id || (me.card && me.card.participant_id) || "";
  renderRaceStatus(me);

  if (!me.predictions_enabled) {
    myScoreEl.textContent = "";
    cardsEl.innerHTML = "";
    predictionsOffNoteEl.classList.remove("hidden");
    leaderboardPanelEl.classList.add("hidden");
    if (liveRefreshTimer) {
      clearInterval(liveRefreshTimer);
      liveRefreshTimer = null;
    }
    return;
  }
  predictionsOffNoteEl.classList.add("hidden");
  leaderboardPanelEl.classList.remove("hidden");

  myScoreEl.textContent = `내 점수: ${me.card.score}점`;

  cardsEl.innerHTML = [1, 2, 3].map((r) => renderPredictionCard(me, r)).join("");
  for (const btn of cardsEl.querySelectorAll(".target-btn")) {
    btn.addEventListener("click", () => chooseTarget(parseInt(btn.dataset.round, 10), btn.dataset.target));
  }

  updateLiveRefreshTimer(me);
}

// 선택 창이 열려 있는 동안에는 다른 참가자의 선택으로 분포가
// 계속 바뀐다. 서버가 실시간으로 밀어주지 않으므로(브로드캐스트 폭주 방지),
// 창이 열려 있을 때만 짧은 주기로 폴링해 "표가 몰립니다" 감각을 살린다.
function anyRoundOpen(me) {
  return Object.values(me.round_state).some((s) => s === "open");
}

// 레이스가 실제로 달리는 동안(phase가 "race_r1"/"race_r2"/"race_r3")에도
// "내 카트 등수" 카드가 실시간처럼 보이도록 같은 폴링을 켠다(사용자 요청).
function isRacingPhase() {
  return typeof currentPhase === "string" && currentPhase.startsWith("race_");
}

function updateLiveRefreshTimer(me) {
  const shouldPoll = anyRoundOpen(me) || isRacingPhase();
  if (shouldPoll && !liveRefreshTimer) {
    liveRefreshTimer = setInterval(refreshMe, 2000);
  } else if (!shouldPoll && liveRefreshTimer) {
    clearInterval(liveRefreshTimer);
    liveRefreshTimer = null;
  }
}

// 세션이 리셋되거나(관리자가 /api/session/reset) 토큰이 실제로 무효화된
// 경우에만 부른다 -- 네트워크 순단 등 일시적 오류에는 절대 쓰지 않는다
// (여기서 토큰을 지우면, 서버는 여전히 이 참가자를 "참여 중"으로 알고
// 있는데 클라이언트만 잊어버려서 재참여 시도가 409 "이미 다른 기기에서
// 참여 중"으로 영구 잠기는 문제가 있었다).
function resetToOnboarding() {
  localStorage.removeItem(TOKEN_KEY);
  myToken = null;
  myParticipantId = null;
  myName = "";
  myCharacterId = null;
  if (liveRefreshTimer) {
    clearInterval(liveRefreshTimer);
    liveRefreshTimer = null;
  }
  showOnboarding();
  loadDepartments();
}

async function refreshMe() {
  if (!myToken) return;
  try {
    const me = await fetchJSON(`/api/predict/me?token=${encodeURIComponent(myToken)}`);
    renderMe(me);
  } catch (e) {
    if (e.status === 401) {
      // 토큰이 서버에서 사라졌다(세션 리셋 등) -- 온보딩으로 되돌린다.
      // 그 외(네트워크 오류 등)는 토큰을 지우지 않고 다음 폴링에서 재시도.
      resetToOnboarding();
    } else {
      console.error(e);
    }
  }
}

async function refreshLeaderboard() {
  try {
    const result = await fetchJSON("/api/predict/leaderboard");
    leaderboardTitleEl.textContent = "리더보드";
    leaderboardListEl.innerHTML = result.top
      .map((entry) => `<li>${entry.name} - ${entry.score}점</li>`)
      .join("");
  } catch (e) {
    console.error(e);
  }
}

const ws = connectWS((data) => {
  if (data.type === "reset") {
    // 관리자가 세션을 초기화하면 서버는 predict_tokens를 전부 지운다.
    // 폴링이 다음 401을 잡을 때까지(참여 창이 닫혀 있으면 폴링 자체가
    // 없어서 영영 안 잡힐 수도 있음) 기다리지 않고 즉시 온보딩으로
    // 되돌린다 -- 안 그러면 화면이 리셋 전 점수/예측카드를 계속 보여준다.
    resetToOnboarding();
    return;
  }
  if (data.type === "phase") {
    // race_r1/race_r2/race_r3 등 -- "내 카트 등수" 폴링을 켤지 여기서 판단한다.
    currentPhase = data.phase;
  }
  if (["phase", "prediction_window", "round_revealed", "prediction_result"].includes(data.type)) {
    refreshMe();
  }
  if (data.type === "prediction_leaderboard" || data.type === "round_revealed") {
    refreshLeaderboard();
  }
}, "mobile");

// 응원 이모지 버튼 -- 무대 화면에 가볍게 이모지를 날린다. 서버가 허용
// 목록/쿨다운을 검증하므로 여기서는 그냥 보내기만 하면 된다.
cheerButtonsEl.innerHTML = CHEER_EMOJI.map((e) => `<button class="cheer-btn" data-emoji="${e}">${e}</button>`).join("");
for (const btn of cheerButtonsEl.querySelectorAll(".cheer-btn")) {
  btn.addEventListener("click", () => sendWS(ws, { type: "cheer", emoji: btn.dataset.emoji }));
}

tryRestoreSession();
