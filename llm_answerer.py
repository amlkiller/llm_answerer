import os
import sys
import hashlib
import json
import argparse
from datetime import datetime
from contextlib import asynccontextmanager
from openai import AsyncOpenAI
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import aiosqlite
import asyncio

# 设置UTF-8编码以正确显示中文
os.environ['PYTHONIOENCODING'] = 'utf-8'
if sys.platform == 'win32':
    os.system('chcp 65001 >nul 2>&1')
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

load_dotenv()

class LLMAnswerer:
    def __init__(self, api_key=None, model="gpt-3.5-turbo", db_path="answer_cache.db",
                 base_url=None, custom_headers=None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        if not self.api_key:
            raise ValueError(
                "未设置OPENAI_API_KEY。请在.env文件中设置或通过参数传入。\n"
                "请复制.env.example为.env并填入你的API密钥。"
            )

        self.model = model
        self.db_path = db_path
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self.custom_headers = custom_headers or {}
        self.db_conn = None

        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        if self.custom_headers:
            client_kwargs["default_headers"] = self.custom_headers

        self.client = AsyncOpenAI(**client_kwargs)

    async def connect_db(self):
        """建立数据库连接"""
        self.db_conn = await aiosqlite.connect(self.db_path)
        await self.db_conn.execute('PRAGMA journal_mode=WAL')
        await self.db_conn.execute('PRAGMA cache_size=-64000')
        await self.db_conn.execute('PRAGMA synchronous=NORMAL')
        await self.db_conn.commit()

    async def close_db(self):
        """关闭数据库连接"""
        if self.db_conn:
            await self.db_conn.close()
            self.db_conn = None

    async def init_database(self):
        """初始化SQLite数据库"""
        await self.db_conn.execute('''
            CREATE TABLE IF NOT EXISTS answer_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_hash TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                options TEXT,
                question_type TEXT,
                answer TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await self.db_conn.execute('CREATE INDEX IF NOT EXISTS idx_question_hash ON answer_cache(question_hash)')
        await self.db_conn.commit()

    def _get_cache_key(self, title, options):
        """生成缓存键"""
        content = f"{title}|{options or ''}"
        return hashlib.md5(content.encode()).hexdigest()

    async def _get_cached_answer(self, cache_key):
        """从数据库获取缓存答案"""
        cursor = await self.db_conn.execute('SELECT answer FROM answer_cache WHERE question_hash = ?', (cache_key,))
        result = await cursor.fetchone()
        return result[0] if result else None

    async def _save_to_cache(self, cache_key, title, options, question_type, answer):
        """保存答案到数据库"""
        try:
            await self.db_conn.execute('''
                INSERT OR REPLACE INTO answer_cache
                (question_hash, title, options, question_type, answer)
                VALUES (?, ?, ?, ?, ?)
            ''', (cache_key, title, options, question_type, answer))
            await self.db_conn.commit()
        except Exception as e:
            print(f"保存缓存失败: {e}")

    def _validate_answer(self, answer, question_type):
        """验证答案格式是否规范"""
        if not answer or len(answer.strip()) == 0:
            return False

        answer = answer.strip()

        if question_type == "single":
            return len(answer) == 1 and answer.isalpha()
        elif question_type == "multiple":
            parts = answer.split('#')
            return all(len(p) == 1 and p.isalpha() for p in parts)
        elif question_type == "judgement":
            return answer in ["正确", "错误"]
        elif question_type == "completion":
            return len(answer) > 0
        else:
            return len(answer) > 0

    def _build_prompt(self, title, options, question_type):
        """构造针对不同题型的prompt"""
        prompt = f"题目：{title}\n"

        if options:
            prompt += f"\n选项：\n{options}\n"

        if question_type == "single":
            prompt += "\n这是一道单选题，请仅返回正确答案的选项字母（如A、B、C、D），不要有其他解释。"
        elif question_type == "multiple":
            prompt += "\n这是一道多选题，请返回所有正确答案的选项字母，用#号分隔（如A#C#D），不要有其他解释。"
        elif question_type == "judgement":
            prompt += '\n这是一道判断题，请仅返回"正确"或"错误"，不要有其他解释。'
        elif question_type == "completion":
            prompt += "\n这是一道填空题，请直接给出填空答案，如果有多个空，用#号分隔。"
        else:
            prompt += "\n请直接给出答案。"

        return prompt

    async def _call_llm(self, prompt):
        """调用OpenAI API"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个专业的答题助手，请根据题目给出准确答案。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )
        answer = response.choices[0].message.content.strip()
        return answer

    async def answer_question(self, title, options=None, question_type=None, skip_cache=False):
        """
        将题目转换为LLM请求并获取答案
        返回格式: [error_msg, answer]
        """
        cache_key = self._get_cache_key(title, options)

        if not skip_cache:
            cached_answer = await self._get_cached_answer(cache_key)
            if cached_answer:
                print(f"[缓存命中] 题目: {title[:50]}... -> 答案: {cached_answer}")
                return [None, cached_answer]

        prompt = self._build_prompt(title, options, question_type)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                answer = await self._call_llm(prompt)

                if self._validate_answer(answer, question_type):
                    await self._save_to_cache(cache_key, title, options, question_type, answer)
                    print(f"[LLM回答] 题目: {title[:50]}... -> 答案: {answer}")
                    return [None, answer]
                else:
                    print(f"[答案无效] 尝试 {attempt + 1}/{max_retries}: {answer}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)
                        continue
                    else:
                        return ["LLM返回的答案格式不规范", None]

            except Exception as e:
                print(f"[请求失败] 尝试 {attempt + 1}/{max_retries}: {str(e)}")
                if attempt == max_retries - 1:
                    return [f"LLM请求失败: {str(e)}", None]
                await asyncio.sleep(2 ** attempt)
                continue

        return ["未知错误：所有重试均未返回结果", None]

    def get_config_info(self):
        """获取配置信息"""
        return {
            "model": self.model,
            "base_url": self.base_url or "https://api.openai.com/v1",
            "db_path": self.db_path,
            "api_key_set": bool(self.api_key)
        }

GLOBAL_SKIP_CACHE = False
answerer = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    await answerer.connect_db()
    await answerer.init_database()
    yield
    await answerer.close_db()

app = FastAPI(lifespan=lifespan)

def print_startup_info(answerer_obj, port):
    """打印启动信息"""
    config = answerer_obj.get_config_info()

    print("\n" + "="*60)
    print("LLM智能答题服务启动成功（异步版本）")
    print("="*60)
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"服务地址: http://localhost:{port}")
    print(f"API端点: http://localhost:{port}/search")
    print("-"*60)
    print("环境配置:")
    print(f"  模型: {config['model']}")
    print(f"  API地址: {config['base_url']}")
    print(f"  数据库: {config['db_path']}")
    print(f"  API密钥: {'已设置' if config['api_key_set'] else '未设置'}")
    print("-"*60)
    print("AnswererWrapper配置:")
    print("[")
    print(json.dumps({
        "name": "LLM智能答题",
        "url": f"http://localhost:{port}/search",
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
    }, ensure_ascii=False, indent=2))
    print("]")
    print("="*60 + "\n")

@app.get('/')
@app.head('/')
async def heartbeat():
    """心跳检查接口"""
    return "服务已启动"

@app.get('/search')
@app.post('/search')
async def search(request: Request):
    """模拟题库API接口，实际使用LLM生成答案"""
    if request.method == 'GET':
        params = dict(request.query_params)
        title = params.get('title', '')
        options = params.get('options')
        question_type = params.get('type')
        skip_cache = GLOBAL_SKIP_CACHE or params.get('skip_cache', 'false').lower() == 'true'

        if title and sys.platform == 'win32':
            try:
                title = title.encode('latin1').decode('utf-8')
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass

        if options and sys.platform == 'win32':
            try:
                options = options.encode('latin1').decode('utf-8')
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass
    else:
        data = await request.json()
        title = data.get('title', '')
        options = data.get('options')
        question_type = data.get('type')
        skip_cache = GLOBAL_SKIP_CACHE or data.get('skip_cache', False)

    print(f"\n[收到请求] {datetime.now().strftime('%H:%M:%S')} - 题型: {question_type or '未知'}")
    print(f"  题目: {title[:100]}{'...' if len(title) > 100 else ''}")
    if options:
        print(f"  选项: {options[:100]}{'...' if len(options) > 100 else ''}")
    if skip_cache:
        print(f"  跳过缓存: 是")

    if not title:
        return JSONResponse({"code": 0, "msg": "题目不能为空"})

    error_msg, answer = await answerer.answer_question(title, options, question_type, skip_cache)

    if answer:
        return JSONResponse({
            "code": 1,
            "question": title,
            "answer": answer
        })
    else:
        return JSONResponse({
            "code": 0,
            "msg": error_msg or "未知错误"
        })

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='LLM智能答题服务')
    parser.add_argument('-skipcache', '--skip-cache', action='store_true',
                        help='跳过缓存，所有请求直接调用LLM API')
    args = parser.parse_args()

    GLOBAL_SKIP_CACHE = args.skip_cache

    port = int(os.getenv("LISTEN_PORT", 5000))
    model = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
    base_url = os.getenv("OPENAI_BASE_URL")

    custom_headers = {
        "User-Agent": "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-Client-Name": "question-libraries"
    }
    answerer = LLMAnswerer(model=model, base_url=base_url, custom_headers=custom_headers)

    print_startup_info(answerer, port)

    if GLOBAL_SKIP_CACHE:
        print("⚠️  缓存已全局禁用 - 所有请求将直接调用LLM API")
        print("="*60 + "\n")

    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=port)
