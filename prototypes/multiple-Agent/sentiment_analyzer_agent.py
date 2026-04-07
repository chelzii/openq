# sentiment_analyzer_agent.py
import os

def read_prompt():
    with open("system_prompt.md", "r", encoding="utf-8") as f:
        return f.read()

def analyze_sentiment(news_summary):
    prompt = read_prompt()
    # 模拟：根据prompt中的“情绪评估模型”来分析文本
    print("--- [市场情绪分析师] ---")
    if "Standard Sentiment Model" in prompt:
        print("使用'标准情感分析模型'进行评估...")
        if "突破性" in news_summary and "看好" in news_summary:
            return "乐观"
        else:
            return "中性"
    else:
        print("警告：未找到指定的情绪评估模型！")
        return "未知"

# 模拟从上一个Agent获取输入
news_summary = "总结：某大型科技公司发布突破性AI技术，前景看好。"
sentiment = analyze_sentiment(news_summary)
print(f"情绪评估结果: {sentiment}\n")
