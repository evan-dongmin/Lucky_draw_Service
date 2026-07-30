const statusEl = document.getElementById("ws-status");
const rosterTextEl = document.getElementById("roster-text");
const rosterFileEl = document.getElementById("roster-file");
const rosterWarningsEl = document.getElementById("roster-warnings");
const rosterPreviewEl = document.getElementById("roster-preview");
const drawCountEl = document.getElementById("draw-count");
const modeSelectEl = document.getElementById("mode-select");
const sessionInfoEl = document.getElementById("session-info");
const excludeIdsEl = document.getElementById("exclude-ids");
const commitDisplayEl = document.getElementById("commit-display");
const drawsHistoryEl = document.getElementById("draws-history");

let previewParticipants = [];
let currentSession = null;

const ws = connectWS((data) => {
  console.log("[ws event]", data.type);
  refreshSession();
});
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
  const result = await fetchJSON("/api/roster/sample?count=250");
  renderPreview(result);
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
        mode: modeSelectEl.value,
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

document.getElementById("btn-reveal").addEventListener("click", async () => {
  try {
    await fetchJSON("/api/draw/reveal", { method: "POST", body: JSON.stringify({}) });
    await refreshSession();
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
  sessionInfoEl.innerHTML = `참가자 ${session.participants.length}명 · 당첨 인원 ${session.draw_count}명 · 제외 ${session.excluded_ids.length}명 · 모드 ${session.mode}`;

  const latest = session.draws[session.draws.length - 1];
  if (!latest) {
    commitDisplayEl.innerHTML = "";
  } else if (!latest.revealed) {
    commitDisplayEl.innerHTML = `<strong>커밋 해시(공개됨):</strong><br><code>${latest.commit}</code>`;
  } else {
    const names = latest.winners
      .map((id) => {
        const p = latest.snapshot.participants.find((x) => x.id === id);
        return p ? participantLabel(p) : id;
      })
      .join(", ");
    commitDisplayEl.innerHTML = `<strong>커밋 해시:</strong> <code>${latest.commit}</code><br><strong>당첨자(${latest.winners.length}명):</strong> ${names}<br><strong>시드(리빌됨):</strong> <code>${latest.seed}</code>`;
  }

  drawsHistoryEl.innerHTML =
    "<h3>추첨 이력</h3>" +
    session.draws
      .map((d, i) => `<div class="draw-history-item">#${i} ${d.revealed ? "리빌됨" : "커밋만"} - ${d.commit.slice(0, 16)}...</div>`)
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
