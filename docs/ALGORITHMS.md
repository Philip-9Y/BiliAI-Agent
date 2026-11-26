# Study-AI 使用的算法与数据结构

> 口径说明：这里讨论的是计算机科学意义上的算法范式（贪心、动态规划、分治、回溯、图算法、随机化算法、哈希、相似度等），不是“动态分析/运行时分析”这一测试概念。

## 1. 实际使用的经典算法范式

### 1.1 贪心（Greedy）

项目里没有“求全局最优”的贪心，但有两处明确的贪心启发式：

1. **本地优先模型路由**
   - 先判断简单纯文字问题；
   - 本地 Ollama 可用就优先本地；
   - 不可用/超时/空回答再回退云端。
   - 局部最优：优先省 token/低延迟；不保证每次都是“质量最优”。

2. **上下文窗口保留**
   - 历史超过阈值时，保留最近 12 条原文，其余压缩成摘要。
   - 每一步都保留“最近/最相关”，不是全局最优对话压缩。

位置：`core/model_router.py`、`core/conversation.py`。

### 1.2 随机化算法（Randomized Algorithm）

**随机超平面 LSH（Locality-Sensitive Hashing）**：

- 生成 `tables × bits × dim` 的随机高斯矩阵；
- 每个向量投影后取符号位，得到二进制桶 key；
- 查询时取多个表的桶并集作为候选；
- 候选再做精确余弦。

作用：记忆量超过 `ann_threshold`（默认 128）时，避免 O(M×D) 全量比较。  
位置：`core/ann.py`。

这是 **ANN（近似最近邻）**，不是精确最近邻；候选可能漏掉，所以关键词命中仍独立保留。

### 1.3 分治（Divide and Conquer）

当前没有正式的分治算法。  
截图、对话、工具调用都是流水线/循环，不是“拆分-递归求解-合并”。

### 1.4 动态规划（Dynamic Programming）

当前没有经典 DP。  
长对话摘要使用“滚动摘要 + 固定窗口”，是贪心压缩，不是 DP；没有最优子结构/状态转移表。

### 1.5 回溯（Backtracking）

当前没有回溯搜索。  
Agent 是**有界迭代式工具循环**（最多 8 步），不是 DFS/回溯，也不会撤销分支。

### 1.6 图算法

当前没有 BFS/DFS/Dijkstra/最小生成树等图算法。  
工具调用链是有向执行序列，但没有建图，也没有最短路径优化。

## 2. 实际使用的非经典算法

| 算法/技术 | 位置 | 作用 |
| --- | --- | --- |
| 字符 bigram + 正则 token | `core/memory.py` | 中文短文本特征 |
| Bag-of-grams 余弦 | `core/memory.py` | 关键词相似度 |
| BGE 向量余弦 | `core/embeddings.py` | 语义相似度 |
| 混合打分 | `core/memory.py` | 关键词 + 字符 + 语义 |
| SHA1 哈希 | `memory.py` / `answer_render.py` | 向量/公式缓存 key |
| LRU 缓存 | `ui/answer_render.py` | 最近 64 条回答 HTML |
| SequenceMatcher | `core/memory.py` | 记忆去重（0.72 阈值） |
| 指数退避 | `core/llm.py` | 429/5xx/网络错误重试 |
| SSE / NDJSON 解析 | `llm.py` / `local_llm.py` | 流式回答 |
| 有界工具循环 | `core/conversation.py` | Agent 执行 |
| 进程隔离 + 超时 | `core/sandbox.py` | 插件沙箱 |

## 3. 数据结构

| 结构 | 位置 | 用途 |
| --- | --- | --- |
| `set[str]` | memory | bigram 去重、集合交并 |
| `Counter` | memory | gram 频次 |
| `dict[str, np.ndarray]` | memory | memory_id → 向量 |
| `list[dict]` | conversation | messages/tool_calls |
| `dataclass` | context/tools/plugins | 结构化对象 |
| SQLite `Row` | store | 会话/消息/笔记/记忆 |
| BLOB float32 | memories.embedding | 紧凑向量 |
| JSON 文本 | messages.meta | 工具步骤回放 |
| `threading.Event` | conversation | 停止/确认同步 |
| `threading.RLock` | store | SQLite 并发保护 |
| `queue.Queue` | app | Win32 热键线程 → Qt |
| `ThreadingHTTPServer` | bridge | 本地 HTTP 并发 |
| Qt Signal/Slot | UI | 跨线程事件 |
| `lru_cache` | answer_render | 渲染缓存 |

## 4. 复杂度

- bigram：O(L)
- 稀疏余弦：O(G)
- BGE 余弦：O(D)，D=512
- 线性记忆检索：O(M×D)
- LSH 召回：O(T×B×D + C×D)，C 为候选数
- Agent 工具循环：O(S×T)，S≤8
- 公式缓存：O(1) 哈希 + 文件读取
- 记忆去重：O(n×m)，n/m 为文本长度

## 5. 为什么不强行上 DP/图算法

当前任务的瓶颈是：

1. 视频上下文获取；
2. 模型路由与工具可靠性；
3. 记忆召回质量；
4. 插件安全/评测；
5. 打包与签名。

这些不是 DP/最短路能直接解决的。  
记忆规模真正上来后，优先做 ANN + rerank，而不是引入一个不匹配的 DP。
