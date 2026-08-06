// 재연결 시마다 내부적으로 새 WebSocket을 만들지만, 밖으로 내주는 핸들은
// 항상 같은 객체다. 예전엔 재연결 때 만들어진 새 소켓을 아무도 참조로
// 받지 못해서, 최초 소켓이 한 번이라도 끊기면 그 이후로 sendWS(ws, ...)나
// ws.addEventListener("open", ...)가 죽은 소켓을 계속 가리키며 영원히
// 조용히 아무 동작도 안 하는 문제가 있었다(예: WiFi 순단 후 admin 화면의
// 카메라 전환 버튼이 그때부터 죽음). readyState를 getter로 두어 항상
// "지금 살아있는" 소켓의 상태를 반영하고, EventTarget이라 addEventListener
// 도 그대로 쓸 수 있으며 재연결마다 "open"이 다시 발화한다.
function connectWS(onMessage, role) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const roleParam = role ? `?role=${encodeURIComponent(role)}` : "";
  const url = `${proto}//${location.host}/ws${roleParam}`;

  const handle = new EventTarget();
  handle._socket = null;
  Object.defineProperty(handle, "readyState", {
    get() {
      return handle._socket ? handle._socket.readyState : WebSocket.CONNECTING;
    },
  });
  handle.send = (data) => {
    if (handle._socket && handle._socket.readyState === WebSocket.OPEN) {
      handle._socket.send(data);
    }
  };

  function open() {
    const socket = new WebSocket(url);
    handle._socket = socket;
    socket.addEventListener("open", () => {
      console.log("[ws] connected", role);
      handle.dispatchEvent(new Event("open"));
    });
    socket.addEventListener("message", (event) => {
      onMessage(JSON.parse(event.data));
    });
    socket.addEventListener("close", () => {
      console.log("[ws] disconnected, retrying in 1s");
      setTimeout(open, 1000);
    });
  }
  open();
  return handle;
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
