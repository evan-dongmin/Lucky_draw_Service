const statusEl = document.getElementById("ws-status");
const rosterTextEl = document.getElementById("roster-text");
const rosterFileEl = document.getElementById("roster-file");
const rosterWarningsEl = document.getElementById("roster-warnings");
const rosterPreviewEl = document.getElementById("roster-preview");
const drawCountEl = document.getElementById("draw-count");
const totalSecondsEl = document.getElementById("total-seconds");
const totalSecondsHintEl = document.getElementById("total-seconds-hint");

// Director의 하한(app/director.py MIN_TOTAL_SECONDS_WITH_PREDICTIONS)과
// 반드시 같은 값을 유지해야 한다. 서버가 최종 검증을 하지만, 그 전에 화면에서
// 미리 알려줘야 "레이스 시작"을 눌렀다가 뒤늦게 400을 보는 일이 없다.
// 참여형 예측 게임은 운영 콘솔에서 항상 켜진 채로 세션을 만들기 때문에
// 예측 게임 켜짐 기준 하한 하나만 있으면 된다.
const MIN_SECONDS_WITH_PREDICTIONS = 150;

function updateTotalSecondsHint() {
  const floor = MIN_SECONDS_WITH_PREDICTIONS;
  totalSecondsEl.min = String(floor);
  const value = parseFloat(totalSecondsEl.value) || 0;
  totalSecondsHintEl.textContent =
    value < floor
      ? `⚠ 레이싱 모드 최소 ${floor}초 필요 -- 이 값으로는 레이스 시작이 거부됩니다.`
      : `레이싱 모드 최소 ${floor}초`;
}
totalSecondsEl.addEventListener("input", updateTotalSecondsHint);
updateTotalSecondsHint();
const racingStatusEl = document.getElementById("racing-status");
const sessionInfoEl = document.getElementById("session-info");
const excludeIdsEl = document.getElementById("exclude-ids");
const commitDisplayEl = document.getElementById("commit-display");
const drawsHistoryEl = document.getElementById("draws-history");

let previewParticipants = [];
let currentSession = null;

const ws = connectWS((data) => {
  console.log("[ws event]", data.type);
  if (data.type === "phase") {
    racingStatusEl.innerHTML = `<strong>진행 단계:</strong> ${data.phase} (${data.duration_seconds.toFixed(1)}초)`;
  } else if (data.type === "round_revealed") {
    racingStatusEl.innerHTML = `<strong>${data.round}라운드 통과자 발표:</strong> ${data.pass_ids.length}명 통과`;
  } else if (data.type === "racing_complete") {
    racingStatusEl.innerHTML = `<strong>레이스 진행 완료</strong>`;
  }
  if (["session_created", "session_updated", "commit_ready", "revealed", "reset", "prize_winners"].includes(data.type)) {
    refreshSession();
  }
}, "admin");
ws.addEventListener("open", () => {
  statusEl.textContent = "연결됨 (WS ready)";
});

function renderPreview(result) {
  previewParticipants = result.participants;
  rosterWarningsEl.innerHTML = result.warnings.length
    ? "⚠ " + result.warnings.join("<br>⚠ ")
    : "";
  const rows = result.participants
    .slice(0, 8)
    .map((p) => `<tr><td>${p.id}</td><td>${p.name}</td><td>${p.team}</td></tr>`)
    .join("");
  const more = result.count > 8 ? `<p>...외 ${result.count - 8}명 (총 ${result.count}명)</p>` : "";
  rosterPreviewEl.innerHTML = `<table><thead><tr><th>사번</th><th>이름</th><th>부서</th></tr></thead><tbody>${rows}</tbody></table>${more}`;
}

document.getElementById("btn-preview-text").addEventListener("click", async () => {
  try {
    const result = await fetchJSON("/api/roster/preview", {
      method: "POST",
      body: JSON.stringify({ text: rosterTextEl.value }),
    });
    renderPreview(result);
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-upload-file").addEventListener("click", async () => {
  const file = rosterFileEl.files[0];
  if (!file) {
    alert("파일을 선택하세요.");
    return;
  }
  const form = new FormData();
  form.append("file", file);
  try {
    const resp = await fetch("/api/roster/upload", { method: "POST", body: form });
    const result = await resp.json();
    if (!resp.ok) throw new Error(result.detail || "업로드 실패");
    renderPreview(result);
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-sample").addEventListener("click", async () => {
  try {
    const result = await fetchJSON("/api/roster/sample?count=250");
    renderPreview(result);
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-create-session").addEventListener("click", async () => {
  if (!previewParticipants.length) {
    alert("먼저 명단을 미리보기 해주세요.");
    return;
  }
  try {
    await fetchJSON("/api/session", {
      method: "POST",
      body: JSON.stringify({
        participants: previewParticipants,
        draw_count: parseInt(drawCountEl.value, 10),
        // 룰렛 모드는 운영 콘솔에서 제거했다(사용자 요청) -- 실제 행사는
        // 항상 레이싱 모드로 진행한다. 백엔드는 여전히 두 모드를 지원하지만
        // 콘솔에서는 선택지를 노출하지 않는다.
        mode: "racing",
        total_seconds: parseFloat(totalSecondsEl.value),
        // 참여형 예측 게임도 운영 콘솔에서는 항상 켜서 세션을 만든다
        // (사용자 요청) -- 끄는 옵션은 더 이상 화면에 노출하지 않는다.
        predictions_enabled: true,
      }),
    });
    await refreshSession();
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-apply-exclude").addEventListener("click", async () => {
  const ids = excludeIdsEl.value
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  try {
    await fetchJSON("/api/session/excluded", {
      method: "POST",
      body: JSON.stringify({ excluded_ids: ids }),
    });
    await refreshSession();
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-commit").addEventListener("click", async () => {
  try {
    await fetchJSON("/api/draw/commit", { method: "POST" });
    await refreshSession();
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-start-racing").addEventListener("click", async () => {
  try {
    const result = await fetchJSON("/api/racing/start", { method: "POST", body: JSON.stringify({}) });
    racingStatusEl.innerHTML = `레이스 시작됨 (총 ${result.total_seconds}초)`;
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-fast-forward").addEventListener("click", async () => {
  try {
    await fetchJSON("/api/racing/fast-forward", { method: "POST", body: JSON.stringify({}) });
  } catch (e) {
    alert(e.message);
  }
});

for (const btn of document.querySelectorAll(".camera-btn")) {
  btn.addEventListener("click", () => {
    sendWS(ws, { type: "camera_mode", mode: btn.dataset.mode });
    for (const b of document.querySelectorAll(".camera-btn")) b.classList.remove("selected");
    btn.classList.add("selected");
  });
}

document.getElementById("btn-bots-fill").addEventListener("click", async () => {
  try {
    const result = await fetchJSON("/api/predict/bots/fill", { method: "POST" });
    alert(`데모 봇 ${result.filled}명이 참여했습니다.`);
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-redraw").addEventListener("click", async () => {
  const excludeWinners = document.getElementById("redraw-exclude-winners").checked;
  try {
    await fetchJSON("/api/draw/redraw", {
      method: "POST",
      body: JSON.stringify({ exclude_previous_winners: excludeWinners }),
    });
    await refreshSession();
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-reset").addEventListener("click", async () => {
  if (!confirm("세션을 완전히 초기화합니다. 계속할까요?")) return;
  await fetchJSON("/api/session/reset", { method: "POST" });
  currentSession = null;
  drawsHistoryEl.innerHTML = "";
  commitDisplayEl.innerHTML = "";
  await refreshSession();
});

function renderSession(session) {
  currentSession = session;
  if (!session) {
    sessionInfoEl.textContent = "세션 없음";
    drawsHistoryEl.innerHTML = "";
    commitDisplayEl.innerHTML = "";
    return;
  }
  const predictionLabel = session.predictions_enabled ? " · 예측게임 ON" : "";
  sessionInfoEl.innerHTML = `참가자 ${session.participants.length}명 · 당첨 인원 ${session.draw_count}명 · 제외 ${session.excluded_ids.length}명 · 모드 ${session.mode}${predictionLabel}`;

  const latest = session.draws[session.draws.length - 1];
  if (!latest) {
    commitDisplayEl.innerHTML = "";
  } else if (!latest.revealed) {
    commitDisplayEl.innerHTML = `<strong>봉인 완료</strong> -- 결과는 이미 확정되었고, 레이스가 끝나면 공개됩니다.`;
  } else {
    const labelFor = (id) => {
      const p = latest.snapshot.participants.find((x) => x.id === id);
      return p ? participantLabel(p) : id;
    };
    const raceNames = latest.winners.map(labelFor).join(", ");
    // 예측 게임이 켜진 세션은 레이스 결과(winners)와 실제 경품 당첨자
    // (prize_winners, 최종 리더보드 기준)가 다를 수 있다 -- 조기에 레이스
    // 결과만 보고 상품을 잘못 나눠주지 않도록 실제 당첨자를 먼저, 더 크게
    // 보여준다. prize_winners는 라운드 3 최종 채점이 끝나야 채워지므로
    // 그 전까지는 "채점 대기 중"으로 표시한다.
    let prizeLine;
    if (!latest.prize_winners) {
      prizeLine = `<strong>실제 경품 당첨자:</strong> 예측 게임 최종 채점 대기 중...<br>`;
    } else {
      const basisLabel = latest.prize_basis === "prediction" ? "예측 게임 리더보드" : "레이스 결과";
      const prizeNames = latest.prize_winners.map(labelFor).join(", ") || "(없음 -- 예측 게임 참여자가 부족합니다)";
      prizeLine = `<strong>실제 경품 당첨자(${basisLabel} 기준, ${latest.prize_winners.length}명):</strong> ${prizeNames}<br>`;
    }
    const raceLine =
      latest.prize_winners && latest.prize_basis !== "race"
        ? `<strong>참고 -- 레이스 결과(${latest.winners.length}명):</strong> ${raceNames}<br>`
        : "";
    commitDisplayEl.innerHTML = `${prizeLine}${raceLine}`;
  }

  drawsHistoryEl.innerHTML =
    "<h3>추첨 이력</h3>" +
    session.draws
      .map((d, i) => `<div class="draw-history-item">#${i} ${d.revealed ? "리빌됨(공개)" : "봉인됨(대기 중)"}</div>`)
      .join("");
}

async function refreshSession() {
  try {
    const session = await fetchJSON("/api/session");
    renderSession(session);
  } catch (e) {
    if (e.status === 404) {
      renderSession(null);
    } else {
      console.error(e);
    }
  }
}

refreshSession();
