const TOKEN_KEY = "luckydraw_predict_token";

const onboardingViewEl = document.getElementById("onboarding-view");
const stepDepartmentEl = document.getElementById("onboarding-step-department");
const stepNameEl = document.getElementById("onboarding-step-name");
const departmentListEl = document.getElementById("department-list");
const nameListEl = document.getElementById("name-list");
const onboardingErrorEl = document.getElementById("onboarding-error");
const gameViewEl = document.getElementById("game-view");
const myNameEl = document.getElementById("my-name");
const myScoreEl = document.getElementById("my-score");
const cardsEl = document.getElementById("cards");
const leaderboardListEl = document.getElementById("leaderboard-list");

const ROUND_LABELS = { 1: "1라운드", 2: "2라운드", 3: "3라운드" };
const STATE_LABELS = { pending: "대기 중", open: "선택 중", locked: "확정" };

let departmentsData = {};
let myToken = localStorage.getItem(TOKEN_KEY);
let myParticipantId = null;
let myName = "";

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
    onboardingErrorEl.textContent = "아직 명단이 등록되지 않았거나 예측 게임이 비활성화되어 있습니다.";
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
    showGame();
    await refreshMe();
    await refreshLeaderboard();
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
    myParticipantId = me.card.participant_id;
    showGame();
    renderMe(me);
    await refreshLeaderboard();
  } catch (e) {
    localStorage.removeItem(TOKEN_KEY);
    myToken = null;
    showOnboarding();
    await loadDepartments();
  }
}

function cardStateFor(me, round) {
  if (me.card.locked[round]) return "locked";
  if (me.round_state[round] === "open") return "open";
  return "pending";
}

function renderCard(me, round) {
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
    targetHtml =
      `<p>지금 선택하세요! (미선택 시 시간 종료 후 무작위 배정)</p>` +
      `<div class="choice-list">` +
      candidates
        .map(
          (c) =>
            `<button class="choice-btn target-btn ${target === c ? "selected" : ""}" data-round="${round}" data-target="${c}">${c}</button>`
        )
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

function renderMe(me) {
  myNameEl.textContent = myName || me.card.participant_id;
  myScoreEl.textContent = `내 점수: ${me.card.score}점`;
  cardsEl.innerHTML =
    [1, 2, 3].map((r) => renderCard(me, r)).join("") +
    `<button id="btn-save-alloc">확신도 저장</button>`;

  document.getElementById("btn-save-alloc").addEventListener("click", () => saveAllocation(me));
  for (const btn of cardsEl.querySelectorAll(".target-btn")) {
    btn.addEventListener("click", () => chooseTarget(parseInt(btn.dataset.round, 10), btn.dataset.target));
  }
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

async function refreshMe() {
  if (!myToken) return;
  try {
    const me = await fetchJSON(`/api/predict/me?token=${encodeURIComponent(myToken)}`);
    renderMe(me);
  } catch (e) {
    console.error(e);
  }
}

async function refreshLeaderboard() {
  try {
    const result = await fetchJSON("/api/predict/leaderboard");
    leaderboardListEl.innerHTML = result.top
      .map((entry) => `<li>${entry.name} - ${entry.score}점</li>`)
      .join("");
  } catch (e) {
    console.error(e);
  }
}

connectWS((data) => {
  if (["phase", "prediction_window", "round_revealed"].includes(data.type)) {
    refreshMe();
  }
  if (data.type === "prediction_leaderboard" || data.type === "round_revealed") {
    refreshLeaderboard();
  }
}, "mobile");

tryRestoreSession();
