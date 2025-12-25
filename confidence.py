"""
置信度评估模块 - 带置信度判断的智能答题
当LLM对答案的置信度较低时，会自动调用联网搜索来增强上下文
"""
import os
import asyncio
import time
from openai import AsyncOpenAI
from search import SearchService
from dotenv import load_dotenv

load_dotenv()

# 从环境变量读取置信度阈值，默认 0.7
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.7"))

# 检查是否配置了 EXA_API_KEY
EXA_API_KEY = os.getenv("EXA_API_KEY")


def validate_answer(answer: str, question_type: str) -> bool:
    """
    验证答案格式是否规范

    Args:
        answer: LLM 返回的答案
        question_type: 题目类型（single/multiple/judgement/completion）

    Returns:
        bool: 答案格式是否有效
    """
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


async def _call_llm_with_validation(
    client: AsyncOpenAI,
    model: str,
    messages: list,
    question_type: str,
    max_retries: int = 3,
    context_description: str = "LLM调用"
) -> str:
    """
    调用LLM并验证答案格式，失败则重试

    Args:
        client: AsyncOpenAI客户端
        model: 模型名称
        messages: 对话消息列表
        question_type: 题目类型（用于验证）
        max_retries: 最大重试次数
        context_description: 上下文描述（用于日志）

    Returns:
        str: 验证通过的答案（如果所有重试都失败，返回最后一次的答案）
    """
    start_time = time.time()
    answer = None

    for attempt in range(max_retries):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=500
            )
            answer = response.choices[0].message.content.strip()

            # 验证答案格式
            if validate_answer(answer, question_type):
                if attempt > 0:
                    print(f"[{context_description}] 验证重试成功，答案: {answer}")
                return answer
            else:
                print(f"[{context_description}] 答案格式不规范 (尝试 {attempt + 1}/{max_retries}: {answer}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)

        except Exception as e:
            print(f"[{context_description}] API调用失败 (尝试 {attempt + 1}/{max_retries}: {str(e)}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

    # 所有重试都失败，返回最后一次的答案（可能无效）
    total_elapsed = (time.time() - start_time) * 1000
    print(f"[{context_description}] 警告: 所有重试均未能获得有效答案，返回最后一次结果: {answer} (总耗时: {total_elapsed:.0f}ms)")
    return answer


def _build_prompt(title: str, options: str = None, question_type: str = None) -> str:
    """
    构造针对不同题型的prompt（从 llm_answerer.py 移植）

    Args:
        title: 题目文本
        options: 选项文本（可选）
        question_type: 题目类型（可选）

    Returns:
        str: 构建好的 prompt
    """
    prompt = f"题目：{title}\n"

    if options:
        prompt += f"\n选项：\n{options}\n"

    if question_type == "single":
        prompt += "\n这是一道单选题，请仅返回正确答案的选项字母（如A、B、C、D），不要有其他解释，包括答案是等描述。"
    elif question_type == "multiple":
        prompt += "\n这是一道多选题，请返回所有正确答案的选项字母，用#号分隔（如A#C#D），不要有其他解释。包括答案是等描述。"
    elif question_type == "judgement":
        prompt += '\n这是一道判断题，请仅返回"正确"或"错误"，不要有其他解释。包括答案是等描述。'
    elif question_type == "completion":
        prompt += "\n这是一道填空题，请直接给出填空答案，如果有多个空，用#号分隔。"
    else:
        prompt += "\n请直接给出答案。"

    return prompt


async def answer_with_confidence(
    client: AsyncOpenAI,
    model: str,
    title: str,
    options: str = None,
    question_type: str = None,
    confidence_threshold: float = None
):
    """
    带置信度判断的LLM回答函数，用于替代 _call_llm(self, prompt)

    流程：
    1. 根据题目和选项构建prompt
    2. 调用LLM获取初始答案
    3. 让LLM评估该答案的置信度（0-1之间的数字）
    4. 如果置信度高于阈值，直接返回答案
    5. 如果置信度低于阈值，调用联网搜索获取参考信息
    6. 将搜索结果加入上下文，重新让LLM回答

    Args:
        client: AsyncOpenAI 客户端实例
        model: 使用的模型名称
        title: 原始题目文本
        options: 选项文本（可选）
        question_type: 题目类型（可选）
        confidence_threshold: 置信度阈值，默认使用环境变量配置

    Returns:
        str: LLM生成的答案
    """
    overall_start_time = time.time()

    if confidence_threshold is None:
        confidence_threshold = CONFIDENCE_THRESHOLD

    # ============== 步骤1: 构建prompt ==============
    prompt = _build_prompt(title, options, question_type)

    # ============== 步骤2: 获取LLM初始答案（带验证） ==============
    step2_start = time.time()
    answer = await _call_llm_with_validation(
        client=client,
        model=model,
        messages=[
            {"role": "system", "content": "你是一个专业的答题助手，请根据题目给出准确答案。"},
            {"role": "user", "content": prompt}
        ],
        question_type=question_type,
        context_description="初始答案获取"
    )
    step2_elapsed = (time.time() - step2_start) * 1000
    print(f"[初始回答] 答案: {answer} (总耗时: {step2_elapsed:.0f}ms)")

    # ============== 步骤3: 评估答案置信度（带重试） ==============
    step3_start = time.time()
    confidence_prompt = f"""题目：{title}

选项：
{options if options else "无选项（判断题或填空题）"}

我给出的答案是：{answer}

请评估这个答案正确的可能性有多大，给出0到1之间的一个数字（0表示完全不可能正确，1表示完全确定正确）。只返回数字，不要有其他解释描述。"""

    confidence = None
    max_confidence_retries = 3

    for attempt in range(max_confidence_retries):
        attempt_start = time.time()
        try:
            confidence_response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是一个专业的答案评估助手，只返回0到1之间的数字。"},
                    {"role": "user", "content": confidence_prompt}
                ],
                temperature=0.3,
                max_tokens=10
            )
            attempt_elapsed = (time.time() - attempt_start) * 1000

            confidence_text = confidence_response.choices[0].message.content.strip()

            # 尝试解析置信度数字
            try:
                confidence = float(confidence_text)
                # 限制在 0-1 范围内
                confidence = max(0.0, min(1.0, confidence))
                # 解析成功，跳出循环
                if attempt > 0:
                    print(f"[置信度评估] 重试成功，置信度: {confidence:.2f} (本次耗时: {attempt_elapsed:.0f}ms)")
                break
            except ValueError:
                print(f"[置信度解析失败] 尝试 {attempt + 1}/{max_confidence_retries}, 返回值: '{confidence_text}' (耗时: {attempt_elapsed:.0f}ms)")
                if attempt < max_confidence_retries - 1:
                    await asyncio.sleep(1)
                    continue

        except Exception as e:
            attempt_elapsed = (time.time() - attempt_start) * 1000
            print(f"[置信度评估失败] 尝试 {attempt + 1}/{max_confidence_retries}: {str(e)} (耗时: {attempt_elapsed:.0f}ms)")
            if attempt < max_confidence_retries - 1:
                await asyncio.sleep(1)
                continue

    step3_elapsed = (time.time() - step3_start) * 1000

    # 如果所有重试都失败，使用默认值
    if confidence is None:
        print(f"[置信度评估] 所有重试均失败，使用默认值 0.5 (总耗时: {step3_elapsed:.0f}ms)")
        confidence = 0.5

    print(f"[置信度评估] 答案: {answer}, 置信度: {confidence:.2f}, 阈值: {confidence_threshold:.2f} (总耗时: {step3_elapsed:.0f}ms)")

    # ============== 步骤4: 根据置信度决定是否联网搜索 ==============
    if confidence >= confidence_threshold:
        overall_elapsed = (time.time() - overall_start_time) * 1000
        print(f"[置信度充足] 置信度 {confidence:.2f} >= {confidence_threshold:.2f}, 直接返回答案 (总流程耗时: {overall_elapsed:.0f}ms)")
        return answer

    # ============== 步骤5: 置信度不足，根据 EXA_API_KEY 配置决定策略 ==============
    print(f"[置信度不足] 置信度 {confidence:.2f} < {confidence_threshold:.2f}")

    if EXA_API_KEY:
        # 有 EXA_API_KEY，进行联网搜索
        print(f"[联网搜索模式] 检测到 EXA_API_KEY，开始联网搜索...")

        try:
            # 构建搜索查询
            search_query = f"{title}"
            if options and question_type in ["single", "multiple"]:
                # 对于选择题，将选项也加入搜索查询
                search_query += f" {options}"

            # 调用搜索服务
            search_start = time.time()
            async with SearchService(verbose=False) as search_service:
                search_context = await search_service.search_and_extract(
                    query=search_query,
                    num_results=3,
                    include_url=False,
                    timeout=30
                )
            search_elapsed = (time.time() - search_start) * 1000

            print(f"[搜索完成] 获取到上下文信息，长度: {len(search_context)} 字符 (搜索耗时: {search_elapsed:.0f}ms)")

            # ============== 步骤6a: 基于搜索结果和第一次答案信息重新回答（带验证） ==============
            # 复用 _build_prompt 函数构建基础 prompt
            base_prompt = _build_prompt(title, options, question_type)

            # 同时附上第一次答案、置信度信息和搜索上下文
            enhanced_prompt = f"""注意：这是第二次回答此问题。

第一次回答的答案是：{answer}，置信度评估：{confidence:.2f}（置信度较低于阈值 {confidence_threshold:.2f}）

由于置信度较低，通过联网搜索获取到以下相关参考信息：

{search_context}

---

{base_prompt}

请结合搜索信息和首次回答的答案和对应的置信度，重新仔细分析题目，给出更准确的答案。"""

            step6a_start = time.time()
            final_answer = await _call_llm_with_validation(
                client=client,
                model=model,
                messages=[
                    {"role": "system", "content": "你是一个专业的答题助手，请根据题目、第一次答案的参考和联网搜索的信息给出准确答案。"},
                    {"role": "user", "content": enhanced_prompt}
                ],
                question_type=question_type,
                context_description="基于搜索和首次答案回答"
            )
            step6a_elapsed = (time.time() - step6a_start) * 1000

            overall_elapsed = (time.time() - overall_start_time) * 1000
            print(f"[基于搜索回答] 最终答案: {final_answer} (重答耗时: {step6a_elapsed:.0f}ms, 总流程耗时: {overall_elapsed:.0f}ms)")
            return final_answer

        except Exception as e:
            overall_elapsed = (time.time() - overall_start_time) * 1000
            print(f"[搜索失败] {str(e)}, 返回原始答案 (总流程耗时: {overall_elapsed:.0f}ms)")
            return answer

    else:
        # 没有 EXA_API_KEY，使用重新回答策略
        print(f"[重新回答模式] 未配置 EXA_API_KEY，将附上第一次答案重新回答...")

        # ============== 步骤6b: 附上第一次答案和置信度信息，重新回答 ==============
        base_prompt = _build_prompt(title, options, question_type)

        retry_prompt = f"""注意：这是第二次回答此问题。

第一次回答的答案是：{answer}，置信度评估：{confidence:.2f}（置信度较低，低于阈值 {confidence_threshold:.2f}）

由于置信度较低，请重新仔细分析题目，给出更准确的答案。

{base_prompt}"""

        step6b_start = time.time()
        final_answer = await _call_llm_with_validation(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": "你是一个专业的答题助手，请根据题目给出准确答案。注意第一次答案的置信度较低，请重新仔细分析。"},
                {"role": "user", "content": retry_prompt}
            ],
            question_type=question_type,
            context_description="置信度低重新回答"
        )
        step6b_elapsed = (time.time() - step6b_start) * 1000

        overall_elapsed = (time.time() - overall_start_time) * 1000
        print(f"[重新回答完成] 最终答案: {final_answer} (重答耗时: {step6b_elapsed:.0f}ms, 总流程耗时: {overall_elapsed:.0f}ms)")
        return final_answer


# ============== 测试函数 ==============
async def test_confidence():
    """测试置信度评估功能"""
    import asyncio

    print("=" * 80)
    print("置信度评估模块测试")
    print("=" * 80)

    # 初始化客户端
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
    base_url = os.getenv("OPENAI_BASE_URL")

    if not api_key:
        print("错误: 未设置 OPENAI_API_KEY")
        return

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = AsyncOpenAI(**client_kwargs)

    # 测试题目
    test_cases = [
        {
            "title": "Python中，哪个函数用于获取列表的长度？",
            "options": "A. size()\nB. length()\nC. len()\nD. count()",
            "type": "single"
        },
        {
            "title": "量子计算机使用量子位进行计算。",
            "options": None,
            "type": "judgement"
        }
    ]

    for i, test_case in enumerate(test_cases, 1):
        print(f"\n{'='*80}")
        print(f"测试案例 {i}:")
        print(f"题目: {test_case['title']}")
        if test_case['options']:
            print(f"选项: {test_case['options']}")
        print(f"{'='*80}")

        answer = await answer_with_confidence(
            client=client,
            model=model,
            title=test_case['title'],
            options=test_case['options'],
            question_type=test_case['type']
        )

        print(f"\n最终返回答案: {answer}")
        print(f"{'='*80}\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_confidence())
