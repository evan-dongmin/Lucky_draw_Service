function connectWS(onMessage) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.addEventListener("open", () => {
    console.log("[ws] connected");
  });
  ws.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    onMessage(data);
  });
  ws.addEventListener("close", () => {
    console.log("[ws] disconnected, retrying in 1s");
    setTimeout(() => connectWS(onMessage), 1000);
  });
  return ws;
}

function sendWS(ws, message) {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(message));
  }
}
