const statusEl = document.getElementById("status");

fetch("http://127.0.0.1:8765/health")
  .then((response) => response.json())
  .then(() => {
    statusEl.textContent = "本地 App 已连接";
    statusEl.className = "ok";
  })
  .catch(() => {
    statusEl.textContent = "本地 App 未连接（请先启动 Study-AI）";
    statusEl.className = "bad";
  });

document.getElementById("send").addEventListener("click", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) throw new Error("没有活动标签页");
    await chrome.tabs.sendMessage(tab.id, { type: "study-ai-send-now" });
    statusEl.textContent = "已发送当前页面上下文";
    statusEl.className = "ok";
  } catch (error) {
    statusEl.textContent = "发送失败：请打开 B 站/YouTube 页面后重试";
    statusEl.className = "bad";
  }
});
