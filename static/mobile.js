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
const myCharacterDisplayEl = document.getElementById("my-character-display");
const cardsEl = document.getElementById("cards");
const predictionsOffNoteEl = document.getElementById("predictions-off-note");
const leaderboardPanelEl = document.getElementById("leaderboard-panel");
const leaderboardTitleEl = document.getElementById("leaderboard-title");
const leaderboardListEl = document.getElementById("leaderboard-list");
const characterOverlayEl = document.getElementById("character-overlay");
const characterOverlayListEl = document.getElementById("character-overlay-list");
const cheerButtonsEl = document.getElementById("cheer-buttons");
const upgradePanelEl = document.getElementById("upgrade-panel");
const personalUpgradeStatusEl = document.getElementById("personal-upgrade-status");
const teamUpgradeStatusEl = document.getElementById("team-upgrade-status");

const CHEER_EMOJI = ["🔥", "👏", "🎉", "💪", "😱", "⚡", "❤️", "😂"]; // app/main.py CHEER_EMOJI_ALLOWLIST와 반드시 일치시킬 것

const ROUND_LABELS = { 1: "1라운드", 2: "2라운드", 3: "3라운드" };
const STATE_LABELS = { pending: "대기 중", open: "선택 중", locked: "확정" };
const BET_STATE_LABELS = { pending: "대기 중", open: "베팅 중", locked: "정산됨" };
// app/gambling.py의 PERSONAL_UPGRADE_COST와 반드시 같은 값을 유지할 것
// (다음 레벨 비용을 안내 문구에 미리 보여주기 위한 클라이언트 사본).
const PERSONAL_UPGRADE_COST = [100, 200, 300];

let departmentsData = {};
let characterRoster = [];
let myToken = localStorage.getItem(TOKEN_KEY);
let myParticipantId = null;
let myName = "";
let myMode = "confidence"; // "confidence" | "gambling" -- /api/predict/join, /api/predict/me 응답에서 갱신
let myCharacterId = null;
let liveRefreshTimer = null;
let teamUpgradeInfo = { pool: 0, level: 0 };

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

// ---------------------------------------------------------------------------
// 업그레이드 상점(코스메틱 전용 -- 순위에는 영향 없음). 갬블링 모드에서만
// 보인다. 개인 강화는 즉시 구매, 팀 업그레이드는 소속 부서 공동 풀에
// 기여하는 방식(십시일반)이라 다른 팀원의 기여도 함께 반영해 보여준다.
// ---------------------------------------------------------------------------

async function ensureDepartmentsLoaded() {
  if (Object.keys(departmentsData).length) return;
  try {
    departmentsData = await fetchJSON("/api/predict/departments");
  } catch (e) {
    /* 조회 실패해도 업그레이드 구매 자체는 가능하다 -- 표시용 정보일 뿐 */
  }
}

function myTeamName() {
  for (const [dept, members] of Object.entries(departmentsData)) {
    if (members.some((m) => m.id === myParticipantId)) return dept;
  }
  return null;
}

async function refreshTeamUpgradeInfo() {
  try {
    const data = await fetchJSON("/api/bet/upgrades");
    await ensureDepartmentsLoaded();
    const team = myTeamName();
    teamUpgradeInfo = {
      pool: (team && data.team_pool[team]) || 0,
      level: (team && data.team_level[team]) || 0,
    };
  } catch (e) {
    /* 폴링 실패는 치명적이지 않다 -- 다음 갱신에서 자연 복구 */
  }
}

function renderUpgradePanel(me) {
  if (myMode !== "gambling") {
    upgradePanelEl.classList.add("hidden");
    return;
  }
  upgradePanelEl.classList.remove("hidden");

  const level = me.card.personal_upgrade_level || 0;
  const maxed = level >= PERSONAL_UPGRADE_COST.length;
  personalUpgradeStatusEl.textContent = maxed
    ? `레벨 ${level}/${PERSONAL_UPGRADE_COST.length} (최대)`
    : `레벨 ${level}/${PERSONAL_UPGRADE_COST.length} · 다음 레벨 비용 ${PERSONAL_UPGRADE_COST[level]}`;
  document.getElementById("btn-buy-personal-upgrade").disabled = maxed;

  teamUpgradeStatusEl.textContent =
    `내 기여 ${me.card.team_upgrade_contributed || 0} · 팀 풀 ${teamUpgradeInfo.pool} (레벨 ${teamUpgradeInfo.level}/3)`;
}

async function buyPersonalUpgrade() {
  try {
    await fetchJSON("/api/bet/upgrade/personal", {
      method: "POST",
      body: JSON.stringify({ token: myToken }),
    });
    await refreshMe();
  } catch (e) {
    alert(e.message);
  }
}

async function contributeTeamUpgrade() {
  const amount = parseInt(document.getElementById("team-upgrade-amount").value, 10) || 0;
  try {
    await fetchJSON("/api/bet/upgrade/team", {
      method: "POST",
      body: JSON.stringify({ token: myToken, amount }),
    });
    await refreshTeamUpgradeInfo();
    await refreshMe();
  } catch (e) {
    alert(e.message);
  }
}

document.getElementById("btn-buy-personal-upgrade").addEventListener("click", buyPersonalUpgrade);
document.getElementById("btn-contribute-team-upgrade").addEventListener("click", contributeTeamUpgrade);

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
    myMode = result.mode || "confidence";
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
    myMode = me.mode || "confidence";
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
// 확신도 배분(무손실) 카드 렌더링
// ---------------------------------------------------------------------------

function renderConfidenceCard(me, round) {
  const state = cardStateFor(me, round);
  const alloc = me.card.alloc[round];
  const locked = me.card.locked[round];
  const target = me.card.target[round];
  const isAuto = me.card.is_auto[round];
  const gain = me.card.gain[round];

  let allocHtml = locked
    ? `<div class="alloc-row"><label>확신도</label><span class="alloc-value">${alloc} (고정)</span></div>`
    : `<div class="alloc-row">
         <label>확신도</label>
         <input type="number" min="10" max="80" step="1" value="${alloc}" class="alloc-input" data-round="${round}" />
       </div>`;

  let targetHtml = "";
  if (state === "pending") {
    targetHtml = `<p>대상은 이 라운드가 시작될 때 선택할 수 있습니다.</p>`;
  } else if (state === "open") {
    const candidates = me.round_candidates[round] || [];
    const dist = (me.live && me.live[round] && me.live[round].distribution) || {};
    targetHtml =
      `<p>지금 선택하세요! (미선택 시 시간 종료 후 무작위 배정)</p>` +
      `<div class="choice-list">` +
      candidates
        .map((c) => {
          const pct = dist[c] ? Math.round(dist[c] * 100) : 0;
          return `<button class="choice-btn target-btn ${target === c ? "selected" : ""}" data-round="${round}" data-target="${c}">${c}<span class="pct-label">${pct}%</span></button>`;
        })
        .join("") +
      `</div>`;
  } else {
    targetHtml = `<p>선택 결과: <strong>${target}</strong>${isAuto ? " (무작위 배정)" : ""}${
      gain !== undefined ? ` -- 획득 점수 ${gain}` : ""
    }</p>`;
  }

  return `
    <div class="pred-card state-${state}">
      <div class="pred-card-header">
        <strong>${ROUND_LABELS[round]}</strong>
        <span class="pred-card-badge">${STATE_LABELS[state]}</span>
      </div>
      ${allocHtml}
      ${targetHtml}
    </div>
  `;
}

async function saveAllocation(me) {
  const alloc = { 1: me.card.alloc[1], 2: me.card.alloc[2], 3: me.card.alloc[3] };
  for (const input of cardsEl.querySelectorAll(".alloc-input")) {
    alloc[parseInt(input.dataset.round, 10)] = parseInt(input.value, 10) || 0;
  }
  const total = alloc[1] + alloc[2] + alloc[3];
  if (total !== 100) {
    alert(`확신도 합계는 100이어야 합니다 (현재 ${total}). 예: 20/30/50`);
    return;
  }
  try {
    await fetchJSON("/api/predict/allocate", {
      method: "POST",
      body: JSON.stringify({ token: myToken, alloc }),
    });
    await refreshMe();
  } catch (e) {
    alert(e.message);
  }
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
// 사이버머니 갬블링(승인됨) 카드 렌더링 -- 패리뮤추얼 베팅
// ---------------------------------------------------------------------------

function renderBetCard(me, round) {
  const state = cardStateFor(me, round);
  const bet = me.card.bets[round]; // {target, amount} | null
  const net = me.card.net ? me.card.net[round] : undefined;

  let bodyHtml = "";
  if (state === "pending") {
    bodyHtml = `<p>이 라운드가 시작되면 베팅할 수 있습니다.</p>`;
  } else if (state === "open") {
    const candidates = me.round_candidates[round] || [];
    const live = (me.live && me.live[round]) || { odds: {} };
    const maxBet = me.card.balance + (bet ? bet.amount : 0);
    const defaultAmount = bet ? bet.amount : Math.min(50, me.card.balance);
    const oddsHtml = candidates
      .map((c) => {
        const odds = live.odds ? live.odds[c] : null;
        const label = odds ? `${odds}배` : "최초 베팅";
        const isMine = bet && bet.target === c;
        return `<button class="choice-btn bet-target-btn ${isMine ? "selected" : ""}" data-round="${round}" data-target="${c}">
          <span class="bet-target-name">${c}</span><span class="odds-label">${label}</span>
        </button>`;
      })
      .join("");
    bodyHtml = `
      <p class="balance-line">보유 사이버머니: <strong>${me.card.balance}</strong></p>
      ${bet ? `<p class="hint-line">현재 베팅: <strong>${bet.target}</strong>에 ${bet.amount} -- 0으로 다시 걸면 취소됩니다</p>` : `<p class="hint-line">지금 베팅하세요! 몰리는 쪽은 배당이 낮아집니다.</p>`}
      <div class="choice-list odds-list">${oddsHtml}</div>
      <div class="bet-amount-row">
        <input type="number" min="0" max="${maxBet}" step="10" value="${defaultAmount}" id="bet-amount-${round}" />
        <button class="btn-bet-submit" data-round="${round}">베팅하기</button>
      </div>
    `;
  } else {
    if (bet) {
      const resultLabel =
        net === undefined
          ? "정산 대기 중..."
          : net >= 0
          ? `<span class="net-win">+${net} 획득</span>`
          : `<span class="net-lose">${net} 손실</span>`;
      bodyHtml = `<p>베팅: <strong>${bet.target}</strong>에 ${bet.amount} -- ${resultLabel}</p>`;
    } else {
      bodyHtml = `<p>이번 라운드는 베팅하지 않았습니다(구경만 했어요).</p>`;
    }
  }

  return `
    <div class="pred-card bet-card state-${state}">
      <div class="pred-card-header">
        <strong>${ROUND_LABELS[round]}</strong>
        <span class="pred-card-badge">${BET_STATE_LABELS[state]}</span>
      </div>
      ${bodyHtml}
    </div>
  `;
}

async function placeBet(round, target, amount) {
  try {
    await fetchJSON("/api/bet/place", {
      method: "POST",
      body: JSON.stringify({ token: myToken, round, target, amount }),
    });
    await refreshMe();
  } catch (e) {
    alert(e.message);
  }
}

// ---------------------------------------------------------------------------
// 공용 렌더/새로고침
// ---------------------------------------------------------------------------

function renderMe(me) {
  myNameEl.textContent = myName || me.participant_id || (me.card && me.card.participant_id) || "";

  if (!me.predictions_enabled) {
    myScoreEl.textContent = "";
    cardsEl.innerHTML = "";
    predictionsOffNoteEl.classList.remove("hidden");
    leaderboardPanelEl.classList.add("hidden");
    upgradePanelEl.classList.add("hidden");
    if (liveRefreshTimer) {
      clearInterval(liveRefreshTimer);
      liveRefreshTimer = null;
    }
    return;
  }
  predictionsOffNoteEl.classList.add("hidden");
  leaderboardPanelEl.classList.remove("hidden");

  myMode = me.mode || myMode;
  myScoreEl.textContent =
    myMode === "gambling" ? `보유 사이버머니: ${me.card.balance}` : `내 점수: ${me.card.score}점`;

  if (myMode === "gambling") {
    cardsEl.innerHTML = [1, 2, 3].map((r) => renderBetCard(me, r)).join("");
    for (const btn of cardsEl.querySelectorAll(".bet-target-btn")) {
      btn.addEventListener("click", () => {
        const round = parseInt(btn.dataset.round, 10);
        for (const b of cardsEl.querySelectorAll(`.bet-target-btn[data-round="${round}"]`)) {
          b.classList.remove("selected");
        }
        btn.classList.add("selected");
        const input = document.getElementById(`bet-amount-${round}`);
        if (input) input.dataset.target = btn.dataset.target;
      });
    }
    for (const btn of cardsEl.querySelectorAll(".btn-bet-submit")) {
      btn.addEventListener("click", () => {
        const round = parseInt(btn.dataset.round, 10);
        const input = document.getElementById(`bet-amount-${round}`);
        const amount = parseInt(input.value, 10) || 0;
        const selected = cardsEl.querySelector(`.bet-target-btn.selected[data-round="${round}"]`);
        const target = selected ? selected.dataset.target : input.dataset.target;
        if (!target) {
          alert("먼저 베팅할 대상을 선택하세요.");
          return;
        }
        placeBet(round, target, amount);
      });
    }
    renderUpgradePanel(me);
    refreshTeamUpgradeInfo().then(() => renderUpgradePanel(me));
  } else {
    upgradePanelEl.classList.add("hidden");
    cardsEl.innerHTML =
      [1, 2, 3].map((r) => renderConfidenceCard(me, r)).join("") +
      `<button id="btn-save-alloc">확신도 저장</button>`;
    document.getElementById("btn-save-alloc").addEventListener("click", () => saveAllocation(me));
    for (const btn of cardsEl.querySelectorAll(".target-btn")) {
      btn.addEventListener("click", () => chooseTarget(parseInt(btn.dataset.round, 10), btn.dataset.target));
    }
  }

  updateLiveRefreshTimer(me);
}

// 베팅/선택 창이 열려 있는 동안에는 다른 참가자의 선택으로 배당률·분포가
// 계속 바뀐다. 서버가 실시간으로 밀어주지 않으므로(브로드캐스트 폭주 방지),
// 창이 열려 있을 때만 짧은 주기로 폴링해 "표가 몰립니다" 감각을 살린다.
function anyRoundOpen(me) {
  return Object.values(me.round_state).some((s) => s === "open");
}

function updateLiveRefreshTimer(me) {
  const shouldPoll = anyRoundOpen(me);
  if (shouldPoll && !liveRefreshTimer) {
    liveRefreshTimer = setInterval(refreshMe, 2500);
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
    const endpoint = myMode === "gambling" ? "/api/bet/leaderboard" : "/api/predict/leaderboard";
    const result = await fetchJSON(endpoint);
    leaderboardTitleEl.textContent = myMode === "gambling" ? "사이버머니 리더보드" : "리더보드";
    leaderboardListEl.innerHTML = result.top
      .map((entry) =>
        myMode === "gambling"
          ? `<li>${entry.name} - ${entry.balance}</li>`
          : `<li>${entry.name} - ${entry.score}점</li>`
      )
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
    // 되돌린다 -- 안 그러면 화면이 리셋 전 점수/베팅카드를 계속 보여준다.
    resetToOnboarding();
    return;
  }
  if (["phase", "prediction_window", "round_revealed", "gambling_result"].includes(data.type)) {
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
