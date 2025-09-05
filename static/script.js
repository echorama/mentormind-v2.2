// --- helpers ---
function sanitizeHTML(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function formatResponse(text) {
  return sanitizeHTML(text).replace(/\n/g, "<br>");
}

// --- stable session id in browser (so backend can thread memory) ---
const SID_KEY = "mentormind_sid";
let sid = localStorage.getItem(SID_KEY);
if (!sid) {
  sid = (crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now());
  localStorage.setItem(SID_KEY, sid);
}

// --- DOM ---
const sendBtn  = document.getElementById("send-btn");
const inputEl  = document.getElementById("user-input");
const chatBox  = document.getElementById("chat-box");

// --- core call ---
async function sendMessage(text) {
  const res = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // include session_id so backend can keep context
    body: JSON.stringify({ message: text, session_id: sid }),
  });

  // try to parse JSON even on non-200
  let data = null;
  try { data = await res.json(); } catch (_) {}

  if (!res.ok) {
    const msg = (data && data.error) ? data.error : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return (data && data.response) ? data.response : "";
}

function addUserBubble(text) {
  chatBox.innerHTML += `<div class="user-message">${sanitizeHTML(text)}</div>`;
  chatBox.scrollTo({ top: chatBox.scrollHeight, behavior: "smooth" });
}

function addBotBubble(text, isError = false) {
  const cls = isError ? "bot-message error" : "bot-message";
  chatBox.innerHTML += `<div class="${cls}">${formatResponse(text)}</div>`;
  chatBox.scrollTo({ top: chatBox.scrollHeight, behavior: "smooth" });
}

async function handleSend() {
  const text = (inputEl.value || "").trim();
  if (!text) return;

  addUserBubble(text);
  inputEl.value = "";
  sendBtn.disabled = true;

  try {
    const reply = await sendMessage(text);
    addBotBubble(reply);
  } catch (err) {
    console.error("Chat error:", err);
    addBotBubble("Error contacting the AI Advisor: " + (err.message || "request failed"), true);
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

// click + Enter-to-send
sendBtn?.addEventListener("click", handleSend);
inputEl?.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    handleSend();
  }
});

// optional: greet on first load
window.addEventListener("load", () => {
  if (!chatBox.querySelector(".bot-message, .user-message")) {
    addBotBubble("Merhaba! Türk finansal mevzuatla ilgili sorunu yaz, yardımcı olayım.");
  }
});
