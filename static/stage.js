const views = {
  idle: document.getElementById("idle-view"),
  waiting: document.getElementById("waiting-view"),
  committed: document.getElementById("committed-view"),
  drawing: document.getElementById("drawing-view"),
};
const statusEl = document.getElementById("ws-status");
const participantCountEl = document.getElementById("participant-count");
const departmentCountEl = document.getElementById("department-count");
const commitBadgeEl = document.getElementById("commit-badge");
const reelEl = document.getElementById("reel");
const winnerListEl = document.getElementById("winner-list");
const mcCaptionEl = document.getElementById("mc-caption");

const animatedDrawKeys = new Set();
let lastOpeningShownFor = null;
let lastFinalShownFor = null;

async function showMcLine(tag) {
  try {
    const result = await fetchJSON(`/api/mc/line/${tag}`);
    mcCaptionEl.textContent = result.text ? `"${result.text}"` : "";
  } catch (e) {
    mcCaptionEl.textContent = "";
  }
}

function showView(name) {
  for (const key of Object.keys(views)) {
    views[key].classList.toggle("hidden", key !== name);
  }
}

async function spinReel(pool, finalText, totalMs) {
  const start = Date.now();
  while (Date.now() - start < totalMs) {
    reelEl.textContent = pool[Math.floor(Math.random() * pool.length)];
    const elapsed = Date.now() - start;
    const interval = 40 + Math.pow(elapsed / totalMs, 2) * 260;
    await delay(interval);
  }
  reelEl.textContent = finalText;
}

async function playRouletteSequence(draw) {
  const pool = draw.snapshot.participants.map((p) => participantLabel(p));
  const nameById = Object.fromEntries(
    draw.snapshot.participants.map((p) => [p.id, participantLabel(p)])
  );
  showView("drawing");
  winnerListEl.innerHTML = "";
  for (const winnerId of draw.winners) {
    await spinReel(pool, nameById[winnerId] || winnerId, 1800);
    const li = document.createElement("li");
    li.textContent = nameById[winnerId] || winnerId;
    winnerListEl.appendChild(li);
    await delay(500);
  }
  reelEl.textContent = "🎊 추첨 완료!";
}

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

const ws = connectWS(() => {
  refresh();
});
ws.addEventListener("open", () => {
  refresh();
});

refresh();
