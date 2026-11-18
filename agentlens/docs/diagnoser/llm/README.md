# LLM 诊断器

## 现役入口

| 能力 | 定义与使用 |
|---|---|
| 通用Core | [三Core四variant](general-core.md)，保留有限来源材料、首跑场景与可信请求预算 |
| 专项LLM | [相关性、追问和语言检查](../specialists.md)，有限policy，完整授权Trace，一次Trial最多三次请求 |
| CUSTOM | [开发指南](../README.md)，自定义Prompt/metrics，raw与现役注册变量，AVAILABLE模型 |

Core和专项由代码canonical定义拥有Prompt、指标和版本；用户只改各自允许的模型、生成参数或有限政策。
同名检查不代表同一UUID或算法，专项不加入Core/Quick白名单。公共变量见
[Trace契约](../../projection/trace-contract.md)，通用执行接口见[开发指南](../README.md)。
