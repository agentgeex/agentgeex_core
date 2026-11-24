---
reply_mode: confirm
---

# 通用任务 - General Task

## 概述
新 Agent 的默认任务模板。当 Agent 被创建但尚未分配具体任务时，使用此任务作为初始能力描述。

## 默认行为
- Agent 启动后处于空闲状态，等待调度指令
- 可通过 `agx_dispatch_task` 传入自定义 `instruction` 执行任意任务
- 适用于所有未绑定特定任务场景的新 Agent

## 能力说明
作为通用 Agent，具备以下基础能力：

### 1. 信息处理
- 基础的数据查询与检索能力
- 文本分析与信息提取
- 格式化的报告生成

### 2. 通信协作
- 接收并响应调度器的任务指令
- 向主 Agent 或调度 Agent 汇报执行结果
- 支持通过消息队列进行异步通信

### 3. 可扩展性
- 可通过更新 `payload` 动态调整行为参数
- 支持接收结构化指令集（JSON 格式）
- 可根据需求升级为专用 Agent

## 输入规范
`instruction` 参数应包含：
- `action`: 要执行的动作描述
- `params`: 动作所需的参数（可选）
- `output_format`: 期望的输出格式（可选，默认 JSON）

## 输出规范
执行结果以 JSON 格式返回，包含：
- `status`: succeeded / failed
- `result`: 执行产生的数据
- `summary`: 对人类可读的执行总结

## 适用场景
- 新 Agent 初始化后的默认状态
- 临时性、一次性的查询任务
- 尚未分配具体职责的通用 Agent
