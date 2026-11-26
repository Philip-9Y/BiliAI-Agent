# Study-AI 代码作用与实现指南

> 面向开发者：每个文件负责什么、关键函数怎么工作、一次请求如何流动、改功能该动哪里。
> 算法质量与失效场景见 [ALGORITHMS.md](ALGORITHMS.md)；架构图见 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 1. 先看全局

```text
用户
 ↓
ui/main_window.py（界面、输入、显示）
 ↓ Qt Signal / 方法
core/controller.py（流程编排）
 ↓
core/conversation.py（Agent 循环，工作线程）
 ├─ core/model_router.py（本地/云端）
 │   ├─ core/local_llm.py（Ollama）
 │   └─ core/llm.py（DeepSeek）
 ├─ core/tools.py（工具注册与执行）
 │   └─ core/plugins.py + core/sandbox.py（插件沙箱）
 ├─ core/memory.py（记忆检索）
 │   ├─ core/embeddings.py（BGE）
 │   └─ core/ann.py（LSH）
 └─ core/store.py（SQLite）
 ↓ Signal
ui/main_window.py（工具卡片 + 最终答案）
```

核心原则：

- UI 不直接调 LLM；
- Controller 不直接拼 prompt；
- Agent 不碰 QWidget；
- Store 不依赖 UI；
- 插件不直接进入主进程（默认沙箱）。

---

## 2. 启动与自检

### `app.py`

| 函数/逻辑 | 作用 |
| --- | --- |
| `main()` | 启动入口 |
| `setup_logging()` | 文件日志；windowed 无 stderr 时只写文件 |
| `--check` 分支 | 不创建 Qt，直接跑 `core.selfcheck.run_self_check` |
| `--sandbox-worker` 分支 | 打包后作为沙箱 worker 入口的兜底 |
| 单实例 `QLocalServer` | 防止重复启动 |
| `BrowserBridge` | 启动 `127.0.0.1:8765` |
| `PluginManager.load_all()` | 加载内置/用户插件，启动沙箱 |
| `HotkeyManager` | 注册全局热键，失败自动 Shift 备用 |
| `quit_app()` | 停 Agent、热键、桥、插件沙箱、Store |

### `core/selfcheck.py`

`run_self_check(config, store)`：

- 检查 SQLite；
- 检查配置/API Key；
- 检查 BGE 模型目录；
- 检查扩展 manifest；
- 检查 8765 端口；
- 检查 Ollama 与本地模型；
- 用 `RegisterHotKey` 逐个测试热键；
- 写 `data/selfcheck.json`，核心失败返回 1。

---

## 3. 一次文字问答

```text
MainWindow 输入
→ Controller.send_text
→ Store.add_message(user)
→ ConversationAgent.run
→ _make_client 选择本地/云端
→ _build_messages 组装 system/history/截图/记忆/摘要
→ 简单问题：stream_chat 流式
→ 否则：chat_with_tools 工具循环
→ Store.add_message(assistant + meta)
→ finished Signal
→ MainWindow 渲染
```

### `core/controller.py`

| 方法 | 作用 |
| --- | --- |
| `start_capture()` | 记录当前上下文，隐藏主窗，开始框选 |
| `_capture_now()` | 抓主屏，打开框选 overlay |
| `_submit_screenshot()` | 保存截图、追加到当前会话、调用 Agent |
| `send_text()` | 新会话或当前会话追加文字 |
| `_collect_context()` | 合并窗口标题 + 浏览器扩展上下文 |
| `_maybe_auto_memory()` | 每 6 条消息后台整理记忆 |
| `_record_run()` | 写 `metrics.jsonl` |

---

## 4. 一次截图问答

```text
Ctrl+Alt+Q / 桌宠双击
→ Controller.start_capture
→ 保存前台上下文
→ 主窗隐藏
→ 抓主屏
→ CaptureOverlay 框选
→ QuestionDialog 输入问题
→ _submit_screenshot
  ├─ 有当前会话：追加到该会话
  └─ 没有：新建会话
→ Agent.run
→ 云端视觉模型（有图强制云端）
```

关键点：

- 每条用户消息可带自己的 `image_path`；
- 同一会话连续截图不会丢历史；
- 截图会话在 `ModelRouter.choose` 中 `has_image=True`，直接云端；
- 图片 base64 只用于本次请求，不写入数据库。

---

## 5. Agent 核心：`core/conversation.py`

### 关键方法

| 方法 | 作用 |
| --- | --- |
| `run(sid, task)` | 创建工作线程、停止 Event |
| `_make_client(sid)` | 判断图片/问题，路由本地或云端 |
| `_build_messages(sid, client)` | system + 历史 + 截图 + 记忆 + 笔记模板 |
| `_prepare_history` | 超长历史压缩摘要，保留最近 12 条 |
| `_summarize_history` | 调 LLM 生成/合并摘要 |
| `_rewrite_query` | 模糊追问改写成完整检索问题 |
| `_stream_allowed` | 简单无图纯文字才流式 |
| `_run` | 流式分支 / 工具循环 / 截断续写 / 保存 |
| `_ask_confirmation` | 危险工具等待用户确认，最长 90 秒 |

### 工具循环

```text
for step in range(8):
    message = client.chat_with_tools(messages, tools)
    if no tool_calls:
        保存最终答案
    for call in tool_calls:
        确认危险工具
        registry.execute(name, args)
        把 tool 结果回填 messages
```

终止条件：

- 没有 tool_calls；
- 达到 8 步；
- 用户停止；
- 异常。

---

## 6. 模型路由：`core/model_router.py`

| 类 | 作用 |
| --- | --- |
| `ModelRouter` | 读取 `model_mode`，选本地/云端 |
| `FallbackClient` | 本地失败/空回答自动云端 |

`is_simple(question)`：

```text
长度 ≤ auto_local_max_chars（默认 300）
且不匹配工具/视觉关键词
```

`choose(question, has_image)`：

```text
有图 → 云端
仅云端 → 云端
本地不可用 → 云端
本地优先/自动且简单 → 本地（带 FallbackClient）
否则 → 云端
```

### `core/local_llm.py`

`OllamaClient`：

- `/api/chat` 非流式；
- `stream_chat` 流式 NDJSON；
- `list_models` 给设置页；
- 忽略 tools（本地简单问答不执行工具）。

### `core/llm.py`

`DeepSeekClient`：

- `chat`：普通问答/摘要/改写；
- `chat_with_tools`：Agent 工具轮；
- `stream_chat`：SSE 流式；
- `_post`：429/5xx/网络错误指数退避重试。

---

## 7. 记忆：`core/memory.py`

| 方法 | 作用 |
| --- | --- |
| `_grams` | 英文 token + 中文 bigram |
| `retrieve` | 关键词/字符/语义混合检索 |
| `_embedding_vectors` | 读/算/缓存 BGE 向量 |
| `_array_cosine` | 向量余弦 |
| `_cosine` | 稀疏 gram 余弦 |
| `consolidate` | LLM 整理长期记忆 |
| `_find_similar` | SequenceMatcher 去重 |
| `build_context` | 将 top-5 拼成 prompt 片段 |

### `core/embeddings.py`

`EmbeddingService`：

- 延迟加载 fastembed/BGE；
- 模型不存在/失败自动禁用；
- 只从项目 `models/` 加载，不自动联网下载。

### `core/ann.py`

`LSHIndex`：

- 随机超平面投影；
- 多表桶；
- 查询取候选；
- 候选不足时回退全部。

---

## 8. 工具与插件

### `core/tools.py`

- `ToolSpec`：name/description/parameters/func/dangerous/source；
- `ToolRegistry`：注册、注销、执行、OpenAI tools schema；
- `build_default_registry()`：注册截图、B站、Word、笔记、文件等内置工具。

### `core/plugins.py`

`PluginManager`：

- 扫描 `plugins/`（内置）与 `data/plugins/`（用户）；
- 读 `plugin.json`；
- 启停/安装 zip/文件夹/卸载；
- 默认通过 `PluginSandbox` 启动子进程；
- 给每个工具注册一个远程调用代理。

### `core/sandbox.py` + `core/sandbox_worker.py`

- 子进程加载 `tool.py`；
- stdin/stdout JSON 行 RPC；
- 超时杀进程；
- 清敏感环境变量；
- 固定 cwd 为插件目录；
- 打包版使用 `sandbox/Study-AI-Sandbox.exe`。

---

## 9. 浏览器桥

### `core/bridge.py`

- `ThreadingHTTPServer` 监听 `127.0.0.1:8765`；
- `POST /context` 接收 URL/标题/进度/字幕/选中文字；
- `GET /health` 给扩展检查；
- `latest(max_age)` 只返回 20 秒内数据；
- `context_text()` 格式化成 prompt 片段。

### `extension/`

- `manifest.json`：Chrome/Edge MV3；
- `content.js`：每 2 秒上报；
- `popup.html/js`：检查连接、立即发送；
- 需手动“加载已解压的扩展程序”。

---

## 10. UI

| 文件 | 作用 |
| --- | --- |
| `ui/main_window.py` | 会话列表、聊天、输入框、标题栏、模式切换 |
| `ui/notes_pane.py` | 笔记编辑/预览/模板 |
| `ui/memory_pane.py` | 记忆整理/筛选/编辑/删除 |
| `ui/settings_dialog.py` | API Key、模型、路由、摘要、BGE、Ollama |
| `ui/answer_render.py` | Markdown/公式图片/代码高亮/LRU |
| `ui/capture_overlay.py` | 框选遮罩 |
| `ui/question_dialog.py` | 截图后提问框 |
| `ui/pet_window.py` / `pet_art.py` | 桌宠 |
| `ui/plugins_dialog.py` | 插件安装/启停/卸载 |
| `ui/theme.py` | QSS/图标/主题 |

### 流式渲染

`ConversationAgent.delta` → `MainWindow.agent_delta` → 纯文本逐段追加；  
`finished` 后用 `render_answer` 渲染 Markdown/公式。

---

## 11. 数据与存储

`core/store.py`：

- `sessions`：会话、上下文、摘要；
- `messages`：消息、截图、耗时、工具步骤 meta；
- `notes`：笔记；
- `memories`：长期记忆、tags、embedding、hash；
- SQLite WAL；
- `_ensure_columns` 做轻量迁移；
- `update_session_summary` 持久化滚动摘要。

`core/telemetry.py`：

- 每次运行写 `data/metrics.jsonl`；
- 字段：status/route/elapsed/chars/tools/streamed；
- 不写问题/答案正文。

---

## 12. 构建、评测、签名

| 文件 | 作用 |
| --- | --- |
| `scripts/build_windows.ps1` | 构建主程序 + 沙箱 worker + 复制 models/extension |
| `scripts/Study-AI.iss` | Inno Setup 安装包模板 |
| `scripts/sign_windows.ps1` | PFX/指纹/开发自签名 |
| `scripts/eval_tasks.py` | 任务级评测 |
| `scripts/install_embedding_model.py` | 下载 BGE 到 models/ |
| `eval/tasks.json` | 评测任务 |

构建顺序：

```text
PyInstaller 主程序 onedir
→ PyInstaller 沙箱 worker onedir
→ 复制到 dist/Study-AI/sandbox
→ 复制 models/extension
→ Inno Setup 编译
→ 签名 exe/worker/安装包
```

---

## 13. 开发者常用命令

```powershell
# 测试
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest discover -s tests -t . -v

# 静态检查
python -m pyflakes app.py core ui tests scripts
python -m vulture app.py core ui --min-confidence 80

# 自检
python app.py --check

# 任务评测
python scripts/eval_tasks.py --limit 3

# 构建
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1

# 签名
powershell -ExecutionPolicy Bypass -File scripts/sign_windows.ps1 -CreateSelfSigned -SkipTimestamp
```

---

## 14. 改功能该动哪里

| 需求 | 修改位置 |
| --- | --- |
| 加工具 | `core/tools.py` + 测试 + system prompt |
| 改路由 | `core/model_router.py` |
| 改记忆检索 | `core/memory.py` |
| 改 ANN | `core/ann.py` + `memory.py` |
| 改插件沙箱 | `core/sandbox.py` / `sandbox_worker.py` / `plugins.py` |
| 改流式 | `core/llm.py` / `local_llm.py` / `conversation.py` / `main_window.py` |
| 改 UI | `ui/` |
| 改自检 | `core/selfcheck.py` |
| 改热键 | `core/hotkey.py` + `app.py` |
| 改浏览器桥 | `core/bridge.py` + `extension/` |
| 改构建 | `scripts/build_windows.ps1` / `.iss` |
| 改签名 | `scripts/sign_windows.ps1` |

---

## 15. 已知边界

- 截图是单帧，不是连续视频；
- 界面流式只覆盖简单纯文字；
- 插件沙箱是进程级，不是内核级；
- LSH 是近似召回，没有 recall 指标；
- 摘要是字符阈值，不是 token 预算；
- 路由是规则，不是学习模型；
- 安装包自签名不受信任；
- 配置中的 API Key 仍是明文；
- 没有多用户/云同步/自动更新。

这些不是“藏起来的问题”，后续优化应优先按 [ALGORITHMS.md](ALGORITHMS.md) 的缺陷表逐项处理。
