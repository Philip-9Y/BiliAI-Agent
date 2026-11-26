// Study-AI 浏览器桥：每 2 秒上报当前页面上下文。
(function () {
  const ENDPOINT = "http://127.0.0.1:8765/context";
  const INTERVAL_MS = 2000;

  function textOf(selector) {
    const element = document.querySelector(selector);
    return element ? (element.innerText || element.textContent || "").trim() : "";
  }

  function subtitleText() {
    const selectors = [
      ".bpx-player-subtitle-panel-text",
      ".bpx-player-subtitle-panel",
      ".ytp-caption-segment",
      ".subtitle-item"
    ];
    const parts = [];
    for (const selector of selectors) {
      const value = textOf(selector);
      if (value && !parts.includes(value)) parts.push(value);
    }
    return parts.join(" / ").slice(0, 1200);
  }

  function collect() {
    const video = document.querySelector("video");
    return {
      source: "study-ai-extension",
      url: location.href,
      title: document.title,
      currentTime: video ? video.currentTime : null,
      duration: video ? video.duration : null,
      paused: video ? video.paused : null,
      subtitle: subtitleText(),
      selection: (window.getSelection() || "").toString().trim().slice(0, 1200),
      sentAt: Date.now()
    };
  }

  let lastErrorAt = 0;

  async function send() {
    try {
      const response = await fetch(ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collect())
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
    } catch (error) {
      // 本地 App 没开时不要刷屏，30 秒最多提示一次
      if (Date.now() - lastErrorAt > 30000) {
        lastErrorAt = Date.now();
        console.debug("[Study-AI] 本地桥未连接:", error);
      }
    }
  }

  send();
  setInterval(send, INTERVAL_MS);

  // 在 popup 里点“立即发送”时触发
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message && message.type === "study-ai-send-now") {
      send().then(() => sendResponse({ ok: true }));
      return true;
    }
    return false;
  });
})();
