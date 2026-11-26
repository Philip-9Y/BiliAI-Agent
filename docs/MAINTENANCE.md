# Study-AI 维护与收尾手册

> 用途：每次改完功能后，用这份清单确认“能跑、能测、能交付、文档没漏”。
> 最后更新：2026-11-26

> GitHub 仓库名：`BiliAI-Agent`；本地目录/EXE 仍为 `Study-AI`（兼容旧路径）。

---

## 1. 今日收尾状态

| 项目 | 状态 |
| --- | --- |
| 源码 | `main` 分支，最新提交见 `git log -1` |
| 测试 | 64 项 unittest |
| 静态检查 | `pyflakes` 无输出；`vulture 80%` 无输出 |
| 源码 App | 正常运行，浏览器桥 `127.0.0.1:8765` |
| 安装包 | `dist/Study-AI-Setup.exe`（见第 7 节） |
| 本地模型 | `models/`，BGE-small-zh，约 90.8MB |
| 数据 | `data/` 私有，不上传 GitHub |
| 项目边界 | Agent 只保留在 `Study-AI/` 一个目录 |

---

## 2. 日常健康检查

```powershell
cd "D:\deepseek harness workplace\usually\Study-AI"

# 1. 源码能否导入
python -c "import app; print('ok')"

# 2. 无 GUI 自检
python app.py --check
# 看 data/selfcheck.json

# 3. 全量测试
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest discover -s tests -t .

# 4. 静态检查
python -m pyflakes app.py core ui tests scripts
python -m vulture app.py core ui --min-confidence 80

# 5. 浏览器桥
Invoke-RestMethod http://127.0.0.1:8765/health
```

期望：

- 测试 64 项 OK
- `selfcheck.json` 的 `ok=true`
- 热键被占用只算 warning
- 浏览器桥返回 `ok=true`

---

## 3. 启动与退出

```powershell
# 源码启动（快捷方式实际使用）
Start-Process "D:\Anaconda3\pythonw.exe" -ArgumentList "app.py" -WorkingDirectory (Get-Location)

# 停止
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*app.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

主窗口隐藏到托盘后：

- 双击桌宠
- `Ctrl+Alt+H`
- 托盘菜单「打开主界面」
- 桌面快捷方式（已修复单实例唤醒竞态）

---

## 4. 每次修改后的固定流程

1. 改代码；
2. 补/改测试；
3. 跑 64 项测试；
4. 跑 `pyflakes`、`vulture`；
5. 重启源码 App 做一次手工冒烟：
   - 截图提问
   - 文字追问
   - 笔记保存
   - 记忆检索
   - 浏览器桥
6. 更新 `docs/IMPROVEMENTS.md`；
7. 更新对应开发文档；
8. `git add/commit/push`；
9. 如需交付，重建安装包并签名。

---

## 5. 文档地图

| 文档 | 给谁看 | 作用 |
| --- | --- | --- |
| `README.md` | 用户/第一次接触 | 功能、安装、热键、常见问题 |
| `docs/CODE_GUIDE.md` | 开发者 | **代码逐模块作用、请求流程、改功能入口** |
| `docs/ALGORITHMS.md` | 开发者/算法评审 | 用了哪些经典算法、哪里不行、复杂度 |
| `docs/IMPROVEMENTS.md` | 用户/维护者 | 每次改动“场景→以前→现在→限制” |
| `docs/ARCHITECTURE.md` | 开发者 | 线程、数据流、模块边界 |
| `docs/CODE_FRAMEWORK.md` | 开发者 | 10 分钟全局框架 |
| `docs/PROJECT_INVENTORY.md` | 维护者 | 文件清单、私有/公开边界 |
| `docs/MASTER_GUIDE.md` | 学习者 | 文件地图 + 学习路线 |
| `docs/READING_GUIDE.md` | 学习者 | 阅读顺序 |
| `docs/LEARNING_PATH.md` | 学习者 | 8 周学习计划 |
| `docs/STUDY_PLAN.md` | 学习者 | 执行计划 |
| `docs/MAINTENANCE.md` | 维护者 | 本文件：收尾/发布/健康检查 |

### 新开发者文档的作用

- **CODE_GUIDE**：告诉你“哪个文件负责什么、一次请求怎么流动、改功能该改哪里”，不用从 800 行 `main_window.py` 开始猜。
- **ALGORITHMS**：告诉你“算法真实水平”，包括贪心/DP/分治/回溯/图算法哪些用了、哪些没用、哪些场景会失效。
- **IMPROVEMENTS**：给用户看的改进记录，不是 commit log；每条都有场景和限制。
- **MAINTENANCE**：本文件，负责把“开发完成”变成“可运行、可测试、可交付”。

---

## 6. 发布构建

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
```

构建内容：

1. `Study-AI.exe`（windowed onedir）
2. `Study-AI-Sandbox.exe`（console worker，插件沙箱）
3. 复制 `models/`、`extension/`
4. 生成 `dist/Study-AI/`

安装包：

```powershell
& "C:\Users\张敞\AppData\Local\Programs\Inno Setup 6\ISCC.exe" scripts\Study-AI.iss
```

签名：

```powershell
# 开发自签名
powershell -ExecutionPolicy Bypass -File scripts/sign_windows.ps1 -CreateSelfSigned -SkipTimestamp

# 生产（需受信任 PFX）
powershell -ExecutionPolicy Bypass -File scripts/sign_windows.ps1 `
  -PfxPath "C:\keys\study-ai.pfx" -PfxPassword "..."
```

---

## 7. 发布物信息

构建完成后填写：

```text
安装包：dist/Study-AI-Setup.exe
大小：303.7 MB
SHA256：91D0A738EF185FBEA125C6867B322985684A25E0416399FD0E04357CD3950D9B
签名状态：开发自签名（不受 Windows 信任）
```

> 自签名只验证流程，不消除 SmartScreen。正式分发必须用受信任 OV/EV 证书。

---

## 8. 当前已知限制

1. **B站桌面客户端不能自动拿 URL/字幕/进度**
   - 浏览器扩展只对网页有效；
   - 桌面客户端先复制链接，走剪贴板兜底；
   - 完全自动化需要 UI Automation，是否可行取决于客户端控件树。
2. **浏览器桥无鉴权**
   - 只监听 `127.0.0.1`，但本机网页理论上可 POST。
3. **插件沙箱是进程级**
   - 能隔离崩溃/超时/环境变量；
   - 不能阻止插件读文件/联网。
4. **安装包自签名不受信任**
5. **API Key 明文在 `config.json`**
6. **没有自动更新**
7. **没有多用户/云同步**
8. **ANN 是 LSH 近似召回**，没有 recall 指标
9. **长对话摘要按字符，不完全按 token**
10. **工具循环最多 8 步，无计划/环检测**

---

## 9. 收尾清理清单

每次收尾删除：

```text
build/
dist/Study-AI/
dist/Study-AI-Sandbox/
*.spec
__pycache__/
.pytest_cache/
data/math_cache/      # 可再生，可删
```

保留：

```text
dist/Study-AI-Setup.exe
models/
data/bili_ask.db
data/captures/
docs/
extension/
eval/
```

---

## 10. 下次维护入口

先看：

1. `git log --oneline -5`
2. `data/selfcheck.json`
3. `docs/IMPROVEMENTS.md` 最上方
4. `docs/MAINTENANCE.md` 本文件

如果用户说“打不开”：

- 先查进程和日志；
- 再查主窗口是否隐藏；
- 再查单实例唤醒；
- 最后查构建/快捷方式路径。
