function connectWS(onMessage, role) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const roleParam = role ? `?role=${encodeURIComponent(role)}` : "";
  const ws = new WebSocket(`${proto}//${location.host}/ws${roleParam}`);
  ws.addEventListener("open", () => {
    console.log("[ws] connected", role);
  });
  ws.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    onMessage(data);
  });
  ws.addEventListener("close", () => {
    console.log("[ws] disconnected, retrying in 1s");
    setTimeout(() => connectWS(onMessage, role), 1000);
  });
  return ws;
}

function sendWS(ws, message) {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(message));
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJSON(url, options) {
  const resp = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options && options.headers) },
  });
  let body = null;
  try {
    body = await resp.json();
  } catch (e) {
    body = null;
  }
  if (!resp.ok) {
    const detail = body && body.detail ? body.detail : `요청 실패 (${resp.status})`;
    const err = new Error(detail);
    err.status = resp.status;
    err.body = body;
    throw err;
  }
  return body;
}

function participantLabel(p) {
  return p.team ? `${p.name} (${p.team})` : p.name;
}
