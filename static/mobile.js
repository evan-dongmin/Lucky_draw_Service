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
const scoreSummaryPanelEl = document.getElementById("score-summary-panel");
const scoreSummaryBodyEl = document.getElementById("score-summary-body");
const predictionsOffNoteEl = document.getElementById("predictions-off-note");
const leaderboardPanelEl = document.getElementById("leaderboard-panel");
const leaderboardTitleEl = document.getElementById("leaderboard-title");
const leaderboardListEl = document.getElementById("leaderboard-list");
const characterOverlayEl = document.getElementById("character-overlay");
const characterOverlayListEl = document.getElementById("character-overlay-list");
const cheerButtonsEl = document.getElementById("cheer-buttons");
const introBoxEl = document.getElementById("intro-box");
const prizePanelEl = document.getElementById("prize-panel");
const prizeHeadlineEl = document.getElementById("prize-headline");
const prizeDetailEl = document.getElementById("prize-detail");

const CHEER_EMOJI = ["🔥", "👏", "🎉", "💪", "😱", "⚡", "❤️", "😂"]; // app/main.py CHEER_EMOJI_ALLOWLIST와 반드시 일치시킬 것

const ROUND_LABELS = { 1: "1라운드", 2: "2라운드", 3: "3라운드 (결선)" };

// 라운드마다 "무엇을 고르는가"가 다르다(R1·R2는 부서, R3는 개인 카트).
// 카드 제목 옆에 항상 붙여서, 설명을 안 읽고 들어온 사람도 선택 창이
// 열린 순간 무엇을 고르는지 바로 알 수 있게 한다(사용자 요청).
const ROUND_TARGET_LABEL = { 1: "부서(팀) 선택", 2: "부서(팀) 선택", 3: "카트(개인) 선택" };
const ROUND_TARGET_QUESTION = {
  1: "어느 <b>부서</b>가 1라운드 통과율 1위일까요?",
  2: "살아남은 <b>부서</b> 중 2라운드 통과율 1위는?",
  3: "결선에 오른 <b>카트(개인)</b> 중 1등은 누구일까요?",
};
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
  // 아직 참여 전이면 설명이 펼쳐져 있는 게 맞다.
  if (introBoxEl) introBoxEl.open = true;
  if (prizePanelEl) prizePanelEl.classList.add("hidden");
}

function showGame() {
  onboardingViewEl.classList.add("hidden");
  gameViewEl.classList.remove("hidden");
  // 참여를 마쳤으면 설명은 접어 둔다 -- 작은 화면에서 예측 카드가 먼저
  // 보여야 한다. 제목 줄은 남아 있어 언제든 다시 펼칠 수 있다.
  if (introBoxEl) introBoxEl.open = false;
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

// 카트마다 예측 점수 특성이 다르므로(작업계획서 §12-3) 효과 설명을 버튼에
// 함께 노출한다 -- 효과를 모르면 "선택"이 아니라 그냥 아바타 고르기다.
function characterButtonsHtml(selectedId) {
  return characterRoster
    .map(
      (c) => `<button class="choice-btn character-btn ${c.id === selectedId ? "selected" : ""}" data-id="${c.id}">
        <span class="character-emoji">${c.emoji}</span>
        <span class="character-text">
          <span class="character-label">${c.label}</span>
          ${c.effect ? `<span class="character-effect">${c.effect}</span>` : ""}
          ${c.style ? `<span class="character-style">${c.style}</span>` : ""}
        </span>
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
  // 고른 카트의 효과까지 항상 보이게 한다 -- 라운드 사이에 바꿀 수 있으므로
  // "내가 지금 무슨 특성을 달고 있는지"가 계속 보여야 한다.
  myCharacterDisplayEl.textContent = found
    ? `${found.emoji} ${found.label}${found.effect ? ` · ${found.effect}` : ""}`
    : "카트 미선택(부서 기준 자동 배정 · 점수 특성 없음)";
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

document.getElementById("btn-show-intro")?.addEventListener("click", () => {
  if (!introBoxEl) return;
  introBoxEl.open = true;
  introBoxEl.scrollIntoView({ behavior: "smooth", block: "start" });
});

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

function rewardOf(me, round) {
  return (me.card.rewards && me.card.rewards[round]) || null;
}

function renderRewardBreakdown(me, round) {
  const reward = rewardOf(me, round);
  // 아직 채점 전이어도 "이번 라운드 소계" 줄은 항상 보여준다(사용자 요청).
  // 점수가 안 나온 이유가 "0점이라서"인지 "아직 안 끝나서"인지 구분되지
  // 않으면 폰을 든 사람이 불안해한다.
  if (!reward) {
    return `
      <div class="reward-box reward-box-pending">
        <div class="reward-title">이번 라운드 소계 <span class="reward-total">-</span></div>
        <p class="reward-pending-note">라운드가 끝나면 채점됩니다</p>
      </div>
    `;
  }
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
  // 카트 능력으로 더 받은(또는 덜 받은) 몫. 위 항목들에 이미 포함된 값의
  // 내역 표시라 합계를 다시 더하면 안 된다 -- 그래서 별도 줄로 뺐다.
  const abilityNote =
    reward.ability_bonus
      ? `<p class="reward-ability">🏎️ 카트 능력 효과 포함 (${reward.ability_bonus > 0 ? "+" : ""}${reward.ability_bonus}점)</p>`
      : "";
  return `
    <div class="reward-box">
      <div class="reward-title">이번 라운드 소계 <span class="reward-total">+${reward.total}</span></div>
      ${items.length ? `<ul class="reward-list">${items.join("")}</ul>` : ""}
      ${abilityNote}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// 누적 점수 내역 패널 (라운드별 소계 3줄 + 총계 1줄, 각 줄을 항목별로 분해)
//
// 서버는 card.rewards[round]에 이미 항목별 내역을 담아 내려주므로, 여기서는
// 라운드를 가로질러 더하기만 한다(백엔드 스키마 변경 불필요 -- 작업계획서
// §12-1의 결정). 총계는 표시 전용 합계이고, **불일치가 생기면 서버가 준
// card.score를 신뢰한다**(채점 로직의 단일 진실 공급원은 서버다).
// ---------------------------------------------------------------------------

const REWARD_COLUMNS = [
  { key: "predict", label: "예측" },
  { key: "finish", label: "통과" },
  { key: "team_bonus", label: "팀" },
  { key: "final", label: "결선" },
];

function renderScoreSummary(me) {
  const rounds = [1, 2, 3];
  const scoredRounds = rounds.filter((r) => rewardOf(me, r));
  if (!scoredRounds.length) {
    // 아직 한 라운드도 채점되지 않았다 -- 빈 표를 띄우느니 감춘다.
    scoreSummaryPanelEl.classList.add("hidden");
    return;
  }
  scoreSummaryPanelEl.classList.remove("hidden");

  const totals = { predict: 0, finish: 0, team_bonus: 0, final: 0, total: 0 };
  const rows = rounds.map((r) => {
    const reward = rewardOf(me, r);
    const cells = REWARD_COLUMNS.map(({ key }) => {
      if (!reward) return `<td class="score-cell muted">-</td>`;
      const v = reward[key] || 0;
      totals[key] += v;
      return `<td class="score-cell">${v ? `+${v}` : "0"}</td>`;
    }).join("");
    const subtotal = reward ? reward.total || 0 : null;
    if (reward) totals.total += subtotal;
    return `<tr>
      <th class="score-round">${ROUND_LABELS[r]}</th>
      ${cells}
      <td class="score-cell score-subtotal">${subtotal === null ? "-" : `+${subtotal}`}</td>
    </tr>`;
  });

  // 합계가 서버 점수와 어긋나면(부분 채점 중 폴링이 겹치는 등) 서버 값을
  // 신뢰하고, 화면에는 그 사실을 조용히 알린다.
  const serverScore = me.card.score;
  const mismatch = totals.total !== serverScore;

  scoreSummaryBodyEl.innerHTML = `
    <table class="score-table">
      <thead>
        <tr>
          <th>라운드</th>
          ${REWARD_COLUMNS.map((c) => `<th>${c.label}</th>`).join("")}
          <th>소계</th>
        </tr>
      </thead>
      <tbody>${rows.join("")}</tbody>
      <tfoot>
        <tr>
          <th class="score-round">총계</th>
          ${REWARD_COLUMNS.map(({ key }) => `<td class="score-cell">${totals[key] ? `+${totals[key]}` : "0"}</td>`).join("")}
          <td class="score-cell score-grandtotal">${serverScore}점</td>
        </tr>
      </tfoot>
    </table>
    ${mismatch ? `<p class="score-note">채점이 진행 중입니다 — 총계는 서버 확정 점수(${serverScore}점)를 따릅니다.</p>` : ""}
  `;
}

// 후보 버튼에 붙는 판단 지표(서버 _candidate_stats). 라운드마다 의미가
// 다르므로 있는 값만 골라 붙인다 -- R1은 팀별 카트 수뿐이고, R2부터는
// 직전 라운드 성적이 함께 붙는다.
function renderCandidateStat(stat) {
  if (!stat) return "";
  const bits = [];
  if (stat.karts !== undefined) bits.push(`🏎️ ${stat.karts}대`);
  if (stat.prev_rank) bits.push(`직전 ${stat.prev_rank}위`);
  if (stat.prev_rate !== undefined && stat.prev_rate !== null) {
    bits.push(`통과율 ${Math.round(stat.prev_rate * 100)}%`);
  }
  if (stat.department) bits.push(stat.department);
  if (!bits.length) return "";
  return `<span class="target-stat">${bits.join(" · ")}</span>`;
}

// 라운드별로 "무엇을 보고 골라야 하는지" 한 줄 힌트. 지표를 띄워놔도
// 읽는 법을 모르면 그냥 숫자다.
const CHOICE_HINT = {
  1: "팀별 참가 카트 수가 많을수록 통과자도 많아지는 경향이 있습니다.",
  2: "1라운드에서 많이 살아남은 팀일수록 2라운드 통과율도 높을 가능성이 큽니다.",
  3: "직전 등수가 높을수록 결선에서도 앞설 가능성이 큽니다. 남들이 안 고른 카트를 맞히면 소수파 보너스가 붙습니다.",
};

function renderPredictionCard(me, round) {
  const state = cardStateFor(me, round);
  const target = me.card.target[round];
  const isAuto = me.card.is_auto[round];

  let targetHtml = "";
  if (state === "pending") {
    targetHtml =
      `<p class="pred-question">${ROUND_TARGET_QUESTION[round]}</p>` +
      `<p class="hint-line">이 라운드가 시작되기 전에 선택 창이 열립니다.</p>`;
  } else if (state === "open") {
    const candidates = me.round_candidates[round] || [];
    const dist = (me.live && me.live[round] && me.live[round].distribution) || {};
    const stats = (me.candidate_stats && me.candidate_stats[round]) || {};
    // 미선택 시 자동 배정 규칙이 라운드마다 다르다 -- R1·R2는 자기 부서,
    // R3는 무작위(결선 진출자 개인이라 "자기 팀"이 없다).
    const autoNote =
      round === 3
        ? "미선택 시 무작위 배정"
        : "미선택 시 <strong>우리 부서</strong>가 자동 선택됩니다";
    targetHtml =
      `<p class="pred-question">${ROUND_TARGET_QUESTION[round]}</p>` +
      `<p>지금 선택하세요! (${autoNote})</p>` +
      `<div class="choice-list">` +
      candidates
        .map((c) => {
          const pct = dist[c] ? Math.round(dist[c] * 100) : 0;
          return `<button class="choice-btn target-btn ${target === c ? "selected" : ""}" data-round="${round}" data-target="${c}">
            <span class="target-name">${c}</span>
            ${renderCandidateStat(stats[c])}
            <span class="pct-label">${pct}%</span>
          </button>`;
        })
        .join("") +
      `</div>` +
      `<p class="hint-line choice-hint">${CHOICE_HINT[round] || ""}</p>`;
  } else {
    const autoLabel = isAuto ? (round === 3 ? " (무작위 배정)" : " (우리 부서 자동 선택)") : "";
    targetHtml = `<p>선택 결과: <strong>${target}</strong>${autoLabel}</p>`;
  }

  return `
    <div class="pred-card state-${state}">
      <div class="pred-card-header">
        <strong>${ROUND_LABELS[round]}</strong>
        <span class="pred-target-badge">${ROUND_TARGET_LABEL[round]}</span>
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
// 최종 당첨 결과. 서버가 발표(prize_winners 확정) 전에는 prize=null을
// 내려주므로 그때는 패널을 숨긴다. WS 이벤트뿐 아니라 폴링 응답에도 같은
// 값이 실려 오므로, 발표 순간 화면이 꺼져 있었거나 새로고침한 사람도
// 결과를 놓치지 않는다.
// basis 값은 app/main.py가 정하는 두 가지뿐이다("race" = 예측 게임이 꺼진
// 순수 레이싱 세션, "prediction" = 예측 리더보드가 당첨자를 정한 세션).
// stage.js의 PRIZE_BASIS_LABEL과 같은 키를 쓴다 -- 한쪽만 바꾸면 안 된다.
const PRIZE_BASIS_NOTE = {
  race: "레이스 최종 순위 기준입니다.",
  prediction: "예측 게임 최종 리더보드 기준입니다 — 레이스 순위와 다를 수 있습니다.",
};

function renderPrize(me) {
  const prize = me.prize;
  if (!prize || !prize.announced) {
    prizePanelEl.classList.add("hidden");
    return;
  }
  prizePanelEl.classList.remove("hidden");
  prizePanelEl.classList.toggle("won", prize.is_winner);

  if (prize.is_winner) {
    prizeHeadlineEl.textContent = `🏆 당첨되셨습니다! (${prize.winner_rank}위)`;
    prizeDetailEl.textContent =
      `당첨자 ${prize.winner_count}명 중 ${prize.winner_rank}위 · ` +
      (PRIZE_BASIS_NOTE[prize.basis] || "");
  } else {
    prizeHeadlineEl.textContent = "아쉽지만 이번엔 당첨되지 않았어요";
    prizeDetailEl.textContent =
      `최종 포인트 순위 ${me.point_rank}위 / ${me.point_total}명 · ` +
      `당첨자는 상위 ${prize.winner_count}명입니다. ` +
      (PRIZE_BASIS_NOTE[prize.basis] || "");
  }
}

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
  // 당첨 결과는 예측 게임 on/off와 무관하게 항상 보여준다(순수 레이싱
  // 세션이면 레이스 순위가 곧 당첨자다).
  renderPrize(me);
  renderRaceStatus(me);

  if (!me.predictions_enabled) {
    myScoreEl.textContent = "";
    cardsEl.innerHTML = "";
    scoreSummaryPanelEl.classList.add("hidden");
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
  renderScoreSummary(me);

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
  if (
    ["phase", "prediction_window", "round_revealed", "prediction_result", "prize_winners", "racing_complete"].includes(
      data.type
    )
  ) {
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
