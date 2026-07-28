const statusEl = document.getElementById("ws-status");

const ws = connectWS((data) => {
  statusEl.textContent = `수신: ${JSON.stringify(data)}`;
});

ws.addEventListener("open", () => {
  statusEl.textContent = "연결됨 (WS ready)";
});
