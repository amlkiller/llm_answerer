"""
联网搜索模块 - 使用 Exa AI API 进行智能搜索
异步实现，可作为库被其他异步模块引用
"""
import os
import asyncio
import aiohttp
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# 仅在直接执行时加载环境变量，作为库引用时由主程序负责
if __name__ == "__main__":
    load_dotenv()


class SearchService:
    """搜索服务类，封装 Exa AI 搜索功能（异步版本）"""

    def __init__(self,
                 api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 verbose: bool = False,
                 session: Optional[aiohttp.ClientSession] = None):
        """
        初始化异步搜索服务

        Args:
            api_key: Exa API密钥，默认从环境变量读取
            base_url: API基础URL，默认从环境变量读取或使用官方地址
            verbose: 是否输出详细日志，默认False
            session: 可选的 aiohttp.ClientSession，如果不提供则自动创建
        """
        self.api_key = api_key or os.getenv('EXA_API_KEY')
        self.base_url = base_url or os.getenv('EXA_BASE_URL', 'https://api.exa.ai')
        self.verbose = verbose
        self._external_session = session  # 外部提供的 session
        self._internal_session = None     # 内部创建的 session

        if not self.api_key:
            raise ValueError("EXA_API_KEY 未配置。请通过参数传入或设置环境变量。")

        self.headers = {
            'Content-Type': 'application/json',
            'x-api-key': self.api_key,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp session"""
        if self._external_session:
            return self._external_session

        if self._internal_session is None or self._internal_session.closed:
            self._internal_session = aiohttp.ClientSession()

        return self._internal_session

    async def close(self):
        """关闭内部创建的 session（如果存在）"""
        if self._internal_session and not self._internal_session.closed:
            await self._internal_session.close()
            self._internal_session = None

    async def search(self,
                     query: str,
                     num_results: int = 3,
                     use_autoprompt: bool = True,
                     include_text: bool = False,
                     include_highlights: bool = True,
                     timeout: int = 30) -> Dict[str, Any]:
        """
        执行异步搜索请求

        Args:
            query: 搜索查询字符串
            num_results: 返回结果数量，默认3条
            use_autoprompt: 是否使用自动提示优化，默认True
            include_text: 是否包含完整文本，默认False
            include_highlights: 是否包含高亮片段，默认True
            timeout: 请求超时时间（秒），默认30秒

        Returns:
            搜索响应的完整JSON数据

        Raises:
            Exception: 搜索请求失败时抛出
        """
        url = f"{self.base_url}/search"

        payload = {
            "query": query,
            "useAutoprompt": use_autoprompt,
            "numResults": num_results,
            "contents": {
                "text": include_text,
                "highlights": include_highlights
            }
        }

        if self.verbose:
            print(f"[SearchService] 正在搜索: {query}")

        session = await self._get_session()
        timeout_config = aiohttp.ClientTimeout(total=timeout)

        try:
            async with session.post(url, headers=self.headers, json=payload, timeout=timeout_config) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"HTTP {response.status}: {error_text}")

                return await response.json()

        except asyncio.TimeoutError:
            error_msg = f"搜索请求超时（超过 {timeout} 秒）"
            if self.verbose:
                print(f"[SearchService] {error_msg}")
            raise Exception(error_msg)
        except aiohttp.ClientError as e:
            error_msg = f"网络请求失败: {str(e)}"
            if self.verbose:
                print(f"[SearchService] {error_msg}")
            raise Exception(error_msg)
        except Exception as e:
            if self.verbose:
                print(f"[SearchService] 搜索失败: {e}")
            raise

    def extract_context(self, search_response: Dict[str, Any], include_url: bool = False) -> str:
        """
        从搜索响应中提取有用的上下文信息

        Args:
            search_response: search() 方法返回的响应数据
            include_url: 是否包含来源URL，默认False

        Returns:
            组合后的上下文文本，格式化为可读字符串
        """
        results = search_response.get('results', [])

        if not results:
            return "未找到相关搜索结果"

        context_parts = []

        for i, result in enumerate(results, 1):
            title = result.get('title', '无标题')
            highlights = result.get('highlights', [])
            url = result.get('url', '')

            # 组合单个结果的上下文
            result_context = f"【结果 {i}】\n标题: {title}\n"

            if include_url and url:
                result_context += f"来源: {url}\n"

            if highlights:
                result_context += "相关内容:\n"
                for highlight in highlights:
                    result_context += f"  - {highlight}\n"
            else:
                result_context += "相关内容: 无高亮内容\n"

            context_parts.append(result_context)

        return "\n".join(context_parts)

    async def search_and_extract(self,
                                  query: str,
                                  num_results: int = 3,
                                  include_url: bool = False,
                                  timeout: int = 30) -> str:
        """
        执行异步搜索并直接返回提取的上下文

        Args:
            query: 搜索查询字符串
            num_results: 返回结果数量，默认3条
            include_url: 是否包含来源URL，默认False
            timeout: 请求超时时间（秒），默认30秒

        Returns:
            格式化的上下文文本，失败时返回错误信息
        """
        try:
            response = await self.search(query, num_results=num_results, timeout=timeout)
            return self.extract_context(response, include_url=include_url)
        except Exception as e:
            error_msg = f"搜索失败: {str(e)}"
            if self.verbose:
                print(f"[SearchService] {error_msg}")
            return error_msg

    async def __aenter__(self):
        """支持异步上下文管理器"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出异步上下文管理器时自动关闭 session"""
        await self.close()


async def main():
    """测试函数 - 演示异步搜索服务用法"""
    print("=" * 80)
    print("搜索服务测试 (异步版本)")
    print("=" * 80)

    try:
        # 方式1: 使用 async with 自动管理资源
        print("\n1. 测试搜索服务 (使用上下文管理器):")
        async with SearchService(verbose=True) as service:
            test_query = "量子计算机最新成果"
            print(f"正在搜索: {test_query}\n")

            context = await service.search_and_extract(test_query, num_results=3)
            print("搜索结果上下文:")
            print("-" * 80)
            print(context)
            print("-" * 80)
#
#        # 方式2: 手动管理
#        print("\n2. 测试搜索服务 (手动管理):")
#        service = SearchService(verbose=True)
#        try:
#            test_query = "人工智能最新进展"
#            print(f"正在搜索: {test_query}\n")
#
#            context = await service.search_and_extract(test_query, num_results=2, include_url=True)
#            print("搜索结果上下文:")
#            print("-" * 80)
#            print(context)
#            print("-" * 80)
#        finally:
#            await service.close()
#
#        # 方式3: 使用共享的 session
#        print("\n3. 测试搜索服务 (共享 session):")
#        async with aiohttp.ClientSession() as session:
#            service = SearchService(verbose=True, session=session)
#
#            # 可以并发执行多个搜索
#            queries = ["量子计算", "人工智能", "区块链技术"]
#            tasks = [service.search_and_extract(q, num_results=2) for q in queries]
#            results = await asyncio.gather(*tasks)
#
#            for query, result in zip(queries, results):
#                print(f"\n查询: {query}")
#                print(result[:200] + "..." if len(result) > 200 else result)

    except ValueError as e:
        print(f"\n配置错误: {e}")
        print("提示: 请确保在 .env 文件中设置 EXA_API_KEY")
    except Exception as e:
        print(f"\n发生错误: {e}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
