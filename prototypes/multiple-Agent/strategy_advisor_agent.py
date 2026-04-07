# strategy_advisor_agent.py
import os

def read_prompt():
    with open("system_prompt.md", "r", encoding="utf-8") as f:
        return f.read()

def advise_strategy(sentiment, news_summary):
    prompt = read_prompt()
    # 模拟：根据prompt中的“核心投资策略”来决策
    print("--- [投资策略师] ---")
    if "稳健增长" in prompt:
        print("遵循'稳健增长'策略...")
        if sentiment == "乐观" and ("科技创新" in prompt or "企业财报" in prompt):
             # 实际应用中，news_summary会带有标签，这里简化为检查prompt
            print("决策逻辑：市场情绪乐观，且领域符合，建议买入。")
            return "买入"
        elif sentiment == "悲观":
            return "卖出"
        else:
            return "持有"
    else:
        print("警告：未找到核心投资策略！")
        return "无建议"

# 模拟从上一个Agent获取输入
sentiment = "乐观"
news_summary = "总结：某大型科技公司发布突破性AI技术，前景看好。"
advice = advise_strategy(sentiment, news_summary)
print(f"最终投资建议: {advice}\n")
