// Real-time conversation collaboration (issue #50).
//
// Joins the active conversation room over Socket.IO, renders messages sent by
// other participants, broadcasts typing indicators, and shows connection and
// presence state. The browser reconnects automatically; the `connect` handler
// rejoins the room so a dropped connection recovers without a page reload.
(function () {
  "use strict";

  var layout = document.querySelector(".chat-layout");
  var bridge = window.AIChatBridge;
  if (!layout || !bridge || typeof window.io !== "function") return;

  var statusEl = document.getElementById("realtime-status");
  var presenceEl = document.getElementById("realtime-presence");
  var typingEl = document.getElementById("remote-typing");
  var inputEl = document.getElementById("chat-input");
  var userId = Number(layout.dataset.userId || 0);
  var username = layout.dataset.username || "";

  var socket = window.io({ transports: ["websocket", "polling"] });
  var currentRoomId = null;
  var remoteTypingTimer = null;
  var typingStopTimer = null;
  var lastTypingSent = 0;
  var TYPING_INTERVAL_MS = 900;
  var TYPING_STOP_MS = 1400;

  function setStatus(state, text) {
    if (!statusEl) return;
    statusEl.dataset.state = state;
    statusEl.textContent = text;
  }

  function join(id) {
    id = id === null || id === undefined ? null : Number(id);
    if (id === currentRoomId) return;
    if (currentRoomId !== null && socket.connected) {
      socket.emit("leave_conversation", { conversation_id: currentRoomId });
    }
    currentRoomId = id;
    if (id !== null && socket.connected) {
      socket.emit("join_conversation", { conversation_id: id });
    }
    hideRemoteTyping();
  }

  function leave() {
    join(null);
  }

  function hideRemoteTyping() {
    window.clearTimeout(remoteTypingTimer);
    if (typingEl) {
      typingEl.hidden = true;
      typingEl.textContent = "";
    }
  }

  function showRemoteTyping(name) {
    if (!typingEl) return;
    typingEl.textContent = (name || "Someone") + " is typing…";
    typingEl.hidden = false;
    window.clearTimeout(remoteTypingTimer);
    remoteTypingTimer = window.setTimeout(hideRemoteTyping, 2500);
  }

  function renderPresence(participants) {
    if (!presenceEl) return;
    var others = (participants || []).filter(function (name) {
      return name !== username;
    });
    presenceEl.textContent = others.length
      ? others.length + (others.length === 1 ? " collaborator online" : " collaborators online")
      : "";
  }

  socket.on("connect", function () {
    setStatus("online", "Live");
    if (bridge.getCurrentId() !== null) {
      var id = Number(bridge.getCurrentId());
      currentRoomId = null;
      join(id);
    }
  });

  socket.on("disconnect", function (reason) {
    setStatus(
      "offline",
      reason === "io server disconnect" ? "Disconnected" : "Reconnecting…"
    );
  });

  socket.on("connect_error", function () {
    setStatus("offline", "Reconnecting…");
  });

  socket.on("joined", function (data) {
    setStatus("online", "Live");
    if (data && data.conversation_id !== undefined) {
      currentRoomId = Number(data.conversation_id);
    }
  });

  socket.on("left", function () {
    currentRoomId = null;
  });

  socket.on("presence", function (data) {
    if (!data || Number(data.conversation_id) !== Number(bridge.getCurrentId())) return;
    renderPresence(data.participants);
  });

  socket.on("typing", function (data) {
    if (!data || Number(data.conversation_id) !== Number(bridge.getCurrentId())) return;
    if (Number(data.user_id) === userId) return;
    if (data.typing) showRemoteTyping(data.username);
    else hideRemoteTyping();
  });

  socket.on("message", function (data) {
    if (!data || Number(data.conversation_id) !== Number(bridge.getCurrentId())) return;
    // The sender already rendered its own message optimistically.
    if (data.sender_id !== undefined && Number(data.sender_id) === userId) return;
    // An in-flight SSE stream already owns the assistant bubble; the done
    // frame will attach the persisted message id.
    if (data.message && data.message.role === "assistant" && bridge.isStreaming()) return;
    bridge.renderRemoteMessage(data.message);
  });

  document.addEventListener("conversation:changed", function (event) {
    join(event.detail ? event.detail.id : null);
  });

  if (inputEl) {
    inputEl.addEventListener("input", function () {
      if (currentRoomId === null || !socket.connected) return;
      var now = Date.now();
      if (now - lastTypingSent >= TYPING_INTERVAL_MS) {
        lastTypingSent = now;
        socket.emit("typing", { conversation_id: currentRoomId, typing: true });
      }
      window.clearTimeout(typingStopTimer);
      typingStopTimer = window.setTimeout(function () {
        socket.emit("typing", { conversation_id: currentRoomId, typing: false });
        lastTypingSent = 0;
      }, TYPING_STOP_MS);
    });
  }

  window.addEventListener("offline", function () {
    setStatus("offline", "Offline");
  });
  window.addEventListener("online", function () {
    setStatus("connecting", "Reconnecting…");
  });

  // Initial room (the template may render a conversation on first load).
  if (bridge.getCurrentId() !== null) join(bridge.getCurrentId());
  else setStatus(socket.connected ? "online" : "connecting", socket.connected ? "Live" : "Connecting…");

  // Test seam for the frontend unit tests.
  window.AICARealtime = {
    setStatus: setStatus,
    renderPresence: renderPresence,
    currentRoomId: function () {
      return currentRoomId;
    },
  };
})();
