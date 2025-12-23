# LLM智能答题服务

基于大语言模型（LLM）的智能答题服务，支持单选、多选、判断、填空等多种题型，提供HTTP API接口和本地缓存功能。

## 功能特性

- **多题型支持**：单选题、多选题、判断题、填空题
- **智能缓存**：使用SQLite数据库缓存答案，避免重复调用API
- **异步架构**：基于FastAPI和AsyncOpenAI，支持高并发请求
- **自动重试**：API调用失败时自动重试，提高稳定性
- **答案验证**：自动验证LLM返回的答案格式是否规范
- **灵活配置**：支持自定义模型、API地址、请求头等

## 环境要求

- Python 3.7+
- OpenAI API密钥（或兼容的API服务）

## 安装

1. 克隆项目或下载代码

2. 安装依赖：
```bash
pip install openai fastapi uvicorn aiosqlite python-dotenv
```

## 配置

1. 复制 `.env.example` 为 `.env`：
```bash
cp .env.example .env
```

2. 编辑 `.env` 文件，配置以下参数：
```env
# OpenAI API密钥（必填）
OPENAI_API_KEY=your_api_key_here

# 模型名称（可选，默认：gpt-3.5-turbo）
OPENAI_MODEL=gpt-3.5-turbo

# API基础URL（可选，用于自定义API端点）
OPENAI_BASE_URL=https://api.openai.com/v1

# 服务端口（可选，默认：5000）
FLASK_PORT=5000
```

## 使用方法

### 启动服务

```bash
python llm_answerer.py
```

启动后会显示服务配置信息和API端点地址。

### 命令行参数

- `--skip-cache` 或 `-skipcache`：跳过缓存，所有请求直接调用LLM API

```bash
python llm_answerer.py --skip-cache
```

## API接口

### 心跳检查

**请求：**
```
GET /
```

**响应：**
```
服务已启动
```

### 答题接口

**端点：** `/search`

**方法：** `GET` 或 `POST`

#### GET请求示例

```bash
curl "http://localhost:5000/search?title=Python是一种什么类型的语言？&options=A.编译型\nB.解释型\nC.汇编型\nD.机器语言&type=single"
```

#### POST请求示例

```bash
curl -X POST http://localhost:5000/search \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Python是一种什么类型的语言？",
    "options": "A.编译型\nB.解释型\nC.汇编型\nD.机器语言",
    "type": "single"
  }'
```

#### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| title | string | 是 | 题目内容 |
| options | string | 否 | 选项内容（选择题需要） |
| type | string | 否 | 题型：single（单选）、multiple（多选）、judgement（判断）、completion（填空） |
| skip_cache | boolean | 否 | 是否跳过缓存（默认false） |

#### 响应格式

**成功响应：**
```json
{
  "code": 1,
  "question": "题目内容",
  "answer": "B"
}
```

**失败响应：**
```json
{
  "code": 0,
  "msg": "错误信息"
}
```

#### 答案格式说明

- **单选题**：返回单个字母，如 `A`、`B`、`C`、`D`
- **多选题**：返回多个字母用#分隔，如 `A#C#D`
- **判断题**：返回 `正确` 或 `错误`
- **填空题**：返回填空内容，多个空用#分隔

## 集成到AnswererWrapper

服务启动后会自动输出AnswererWrapper配置，可直接复制使用：

```json
[
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
]
```

## 数据库

服务使用SQLite数据库（默认文件：`answer_cache.db`）缓存答案，包含以下字段：

- `id`：自增主键
- `question_hash`：题目哈希值（用于快速查找）
- `title`：题目内容
- `options`：选项内容
- `question_type`：题型
- `answer`：答案
- `created_at`：创建时间

数据库优化配置：
- WAL模式：提高并发性能
- 缓存大小：64MB
- 同步模式：NORMAL（平衡性能和安全性）

## 技术架构

- **Web框架**：FastAPI（异步高性能）
- **HTTP客户端**：AsyncOpenAI（异步OpenAI客户端）
- **数据库**：aiosqlite（异步SQLite）
- **编码处理**：自动处理UTF-8编码，支持中文显示

## 注意事项

1. 确保 `.env` 文件中的 `OPENAI_API_KEY` 已正确配置
2. 首次运行会自动创建数据库文件
3. 使用缓存可以大幅降低API调用成本
4. 建议使用 `gpt-3.5-turbo` 以平衡成本和准确性
5. 如需使用其他兼容OpenAI API的服务，可配置 `OPENAI_BASE_URL`

## 故障排查

### API密钥错误
```
ValueError: 未设置OPENAI_API_KEY
```
**解决方法**：检查 `.env` 文件是否存在且包含有效的API密钥

### 中文乱码
服务已自动配置UTF-8编码，如仍有问题，请检查终端编码设置

### 答案格式不规范
服务会自动重试3次，如仍失败，可能是题目描述不清晰或模型理解有误

## 许可证

本项目仅供学习和研究使用。
