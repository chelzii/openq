import os, pathlib

PROMPT_FILE = pathlib.Path(__file__).parent / 'system_prompt.md'

HEALTHY_PROMPT = '# system_prompt.md\n\n## 系统核心指令 (Version 1.0)\n\n---\n\n### 1. 关键分析领域\n- 科技创新\n- 宏观经济政策\n- 企业财报\n\n### 2. 情绪评估模型\n- 使用标准情感分析模型 (Standard Sentiment Model)\n- 评估维度：乐观 (Positive), 悲观 (Negative), 中性 (Neutral)\n\n### 3. 核心投资策略\n- **策略名称**: 稳健增长 (Stable Growth)\n- **原则**:\n    - 当市场情绪"乐观"且新闻领域为"科技创新"或"企业财报"时，建议"买入"。\n    - 当市场情绪"悲观"时，建议"卖出"。\n    - 其他情况，建议"持有"。\n'

PROMPT_FILE.write_text(HEALTHY_PROMPT, encoding='utf-8')
print('[恢复] system_prompt.md 已恢复为健康状态 (Version 1.0)')
