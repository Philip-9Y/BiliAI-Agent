# Study-AI Browser Bridge

把当前浏览器页面信息发送给本地 Study-AI：

- 页面标题
- URL
- 视频播放进度
- B 站/YouTube 当前字幕
- 当前选中文字

只发送到 `http://127.0.0.1:8765`，不经过公网。

## 安装（Chrome / Edge）

1. 启动 Study-AI。
2. 打开 `chrome://extensions` 或 `edge://extensions`。
3. 打开右上角「开发者模式」。
4. 点击「加载已解压的扩展程序」，选择本目录 `Study-AI/extension`。
5. 打开 B 站视频页面，扩展图标里应显示「本地 App 已连接」。

## 验证本地桥

浏览器访问：

```text
http://127.0.0.1:8765/health
```

返回 `{"ok": true, ...}` 即正常。

## 说明

- 扩展每 2 秒上报一次；App 只采用最近 20 秒内的数据。
- 如果 App 没启动，扩展不会持续报错。
- 端口可在 `config.json` 的 `bridge_port` 修改，同时要改 `extension/content.js` 和 `extension/popup.js` 里的地址。
