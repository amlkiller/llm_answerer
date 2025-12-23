# LLM Answerer 架构说明

## 概述

`llm_answerer.py` 是一个基于 FastAPI 的异步 Web 服务，通过调用 OpenAI API（或兼容接口）来智能回答各类题目。该服务支持单选题、多选题、判断题和填空题等多种题型，并通过 SQLite 数据库实现答案缓存以提高响应速度和降低 API 调用成本。

## 核心组件

### 1. LLMAnswerer 类

核心业务逻辑类，负责与 LLM API 交互和答案管理。

**主要职责：**
- OpenAI 客户端初始化和配置
- 数据库连接管理
- 答案缓存的读写
- LLM 请求构造和调用
- 答案格式验证

**关键方法：**

| 方法 | 功能 |
|------|------|
| `__init__()` | 初始化 API 配置、数据库路径、创建异步 OpenAI 客户端 |
| `connect_db()` | 建立 SQLite 数据库连接，配置 WAL 模式和性能参数 |
| `close_db()` | 关闭数据库连接 |
| `init_database()` | 创建缓存表和索引 |
| `answer_question()` | 主入口方法，处理题目并返回答案 |
| `_get_cache_key()` | 基于题目和选项生成 MD5 哈希作为缓存键 |
| `_get_cached_answer()` | 从数据库查询缓存答案 |
| `_save_to_cache()` | 保存答案到数据库 |
| `_build_prompt()` | 根据题型构造针对性的 prompt |
| `_call_llm()` | 调用 OpenAI API 获取答案 |
| `_validate_answer()` | 验证答案格式是否符合题型要求 |

### 2. FastAPI 应用

提供 RESTful API 接口，处理 HTTP 请求。

**生命周期管理：**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时：连接数据库并初始化
    await answerer.connect_db()
    await answerer.init_database()
    yield
    # 关闭时：断开数据库连接
    await answerer.close_db()
```

**API 端点：**

| 端点 | 方法 | 功能 |
|------|------|------|
| `/` | GET/HEAD | 心跳检查，返回服务状态 |
| `/search` | GET/POST | 接收题目请求，返回答案 |

### 3. 数据库层

使用 SQLite + aiosqlite 实现异步数据库操作。

**表结构：**
```sql
CREATE TABLE answer_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_hash TEXT UNIQUE NOT NULL,      -- MD5 哈希缓存键
    title TEXT NOT NULL,                     -- 题目内容
    options TEXT,                            -- 选项（可选）
    question_type TEXT,                      -- 题型
    answer TEXT NOT NULL,                    -- 答案
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

**性能优化：**
- WAL 模式：提高并发读写性能
- 缓存大小：64MB 内存缓存
- 同步模式：NORMAL（平衡性能和安全性）
- 索引：question_hash 字段建立索引加速查询

## 数据流程

### 请求处理流程

```
客户端请求
    ↓
FastAPI /search 端点
    ↓
解析参数（title, options, type, skip_cache）
    ↓
answerer.answer_question()
    ↓
生成缓存键（MD5）
    ↓
检查缓存？
    ├─ 命中 → 返回缓存答案
    └─ 未命中 ↓
构造 prompt（根据题型）
    ↓
调用 LLM API（最多重试3次）
    ↓
验证答案格式
    ├─ 有效 → 保存到缓存 → 返回答案
    └─ 无效 → 重试或返回错误
```

### 答案验证规则

| 题型 | 验证规则 |
|------|----------|
| single（单选） | 单个字母（A-Z） |
| multiple（多选） | 多个字母用 # 分隔（如 A#C#D） |
| judgement（判断） | "正确" 或 "错误" |
| completion（填空） | 非空字符串，多个答案用 # 分隔 |
| 其他 | 非空字符串 |

## 配置管理

### 环境变量

通过 `.env` 文件配置：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | OpenAI API 密钥 | 必填 |
| `OPENAI_BASE_URL` | API 基础 URL | https://api.openai.com/v1 |
| `OPENAI_MODEL` | 使用的模型 | gpt-3.5-turbo |
| `FLASK_PORT` | 服务端口 | 5000 |

### 命令行参数

```bash
python llm_answerer.py --skip-cache  # 禁用缓存，所有请求直接调用 API
```

## 容错机制

### 1. 重试策略
- 最多重试 3 次
- 指数退避：第 n 次重试等待 2^(n-1) 秒
- 适用于 API 调用失败和答案格式无效的情况

### 2. 编码处理
- Windows 平台特殊处理：UTF-8 编码配置
- GET 请求参数编码转换（latin1 → utf-8）
- 确保中文正确显示

### 3. 错误响应
```json
{
    "code": 0,
    "msg": "错误信息"
}
```

成功响应：
```json
{
    "code": 1,
    "question": "题目内容",
    "answer": "答案"
}
```

## 性能特性

### 异步架构
- 使用 `AsyncOpenAI` 客户端
- `aiosqlite` 异步数据库操作
- FastAPI 原生异步支持
- 高并发处理能力

### 缓存策略
- 基于题目内容的 MD5 哈希
- 持久化存储（SQLite）
- 可选的缓存跳过功能
- 显著降低 API 调用成本

### LLM 参数优化
- `temperature=0.3`：较低温度保证答案稳定性
- `max_tokens=500`：限制响应长度
- 系统提示词：明确角色定位

## 集成方式

### AnswererWrapper 配置

服务启动时会输出 AnswererWrapper 的 JSON 配置，可直接用于前端集成：

```json
{
  "name": "LLM智能答题",
  "url": "http://localhost:5000/search",
  "method": "post",
  "contentType": "json",
  "type": "GM_xmlhttpRequest",
  "headers": {
    "Content-Type": "application/json"
  },
  "data": {
    "title": "${title}",
    "options": "${options}",
    "type": "${type}"
  },
  "handler": "return (res) => res.code === 1 ? [undefined, res.answer] : [res.msg, undefined]"
}
```

## 扩展性

### 支持自定义 LLM 服务
- 可配置 `base_url` 使用兼容 OpenAI API 的服务
- 自定义请求头（User-Agent、X-Client-Name 等）
- 灵活的模型选择

### 题型扩展
通过修改 `_build_prompt()` 和 `_validate_answer()` 方法可轻松添加新题型。

## 安全考虑

- API 密钥通过环境变量管理，不硬编码
- 数据库使用参数化查询防止 SQL 注入
- 输入验证：题目不能为空
- 答案格式严格验证

## 监控与日志

服务提供详细的控制台日志：
- 请求接收时间和题型
- 缓存命中/未命中状态
- LLM 调用结果
- 错误和重试信息
- 启动配置信息

## 部署建议

1. **生产环境**：
   - 使用更强大的数据库（PostgreSQL）
   - 添加认证机制
   - 配置反向代理（Nginx）
   - 启用 HTTPS

2. **性能优化**：
   - 增加数据库连接池
   - 实现分布式缓存（Redis）
   - 添加请求限流
   - 监控 API 调用配额

3. **可靠性**：
   - 容器化部署（Docker）
   - 健康检查和自动重启
   - 日志持久化和分析
   - 备份数据库
