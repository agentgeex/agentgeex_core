---
name: "json-ops"
description: "通用 JSON 文件操作：读写、追加、合并、JSONPath 查改删、校验。通过本地脚本避免 prompt 中大段 JSON 传输。"
---

# json-ops — 通用 JSON 文件操作

## 工具位置

本 skill 自包含，所有文件在同一目录：

```
skills/json-ops/
├── SKILL.md        # 本文件
└── json_ops.py     # 可执行工具脚本
```

使用前确认脚本可执行：

```bash
chmod +x skills/json-ops/json_ops.py
```

## 基本用法

```bash
python3 skills/json-ops/json_ops.py <action> <file> [--path JSONPATH] [--data JSON]
```

`--data` 可省略，此时从 stdin 读取数据。

## 什么时候用

- 需要操作 JSON 文件（读、写、改、查），且 JSON 太大不适合放 prompt 中
- 需要分批构建 JSON（如 plan JSON 逐个添加 step）
- 需要按 JSONPath 精确读写嵌套字段
- 需要校验 JSON 文件是否合法

## Actions

### read — 读取整个文件或按 JSONPath 过滤

```bash
# 读整个文件
python3 skills/json-ops/json_ops.py read plan.json

# 按 JSONPath 读
python3 skills/json-ops/json_ops.py read plan.json --path '$.steps[0]'
```

### write — 覆盖写入

```bash
python3 skills/json-ops/json_ops.py write plan.json --data '{"steps":[],"metadata":{}}'
```

### append — 向数组追加元素

```bash
# 通过 --data
python3 skills/json-ops/json_ops.py append plan.json --path '$.steps' --data '{"name":"step1","task_id":"task_xxx"}'

# 通过 stdin（JSON 含空格/复杂结构时推荐）
echo '{"name":"step1","task_id":"task_xxx","instruction":"do something"}' | python3 skills/json-ops/json_ops.py append plan.json --path '$.steps'
```

**典型场景：分批构建 plan JSON**

```bash
# Step 1: 初始化
python3 skills/json-ops/json_ops.py write plan.json --data '{"steps":[]}'

# Step 2: 逐批追加 step
echo '{"name":"ch01_split","task_id":"task_xxx",...}' | python3 skills/json-ops/json_ops.py append plan.json --path '$.steps'
echo '{"name":"ch01_write","task_id":"task_yyy",...}' | python3 skills/json-ops/json_ops.py append plan.json --path '$.steps'

# Step 3: 校验
python3 skills/json-ops/json_ops.py validate plan.json

# Step 4: 传入 MCP
# agx_plan_deploy(plan_json=...)
```

### set — 按 JSONPath 设置字段值

```bash
python3 skills/json-ops/json_ops.py set plan.json --path '$.metadata.author' --data '"pm-agent"'
python3 skills/json-ops/json_ops.py set plan.json --path '$.metadata.version' --data '2'
```

### delete — 按 JSONPath 删除字段

```bash
python3 skills/json-ops/json_ops.py delete plan.json --path '$.tmp'
```

### merge — 浅合并（默认）或深度合并

```bash
# 浅合并：顶层 key 覆盖
python3 skills/json-ops/json_ops.py merge plan.json --data '{"owner":"agent-001"}'

# 深度合并：递归合并嵌套 dict
python3 skills/json-ops/json_ops.py merge plan.json --data '{"metadata":{"status":"running"}}' --deep
```

### validate — 校验 JSON 文件

```bash
python3 skills/json-ops/json_ops.py validate plan.json
# 输出：{"valid":true,"path":"plan.json","size":1234,"type":"dict","key_count":3,...}
```

### query — JSONPath 查询（等同 read --path）

```bash
python3 skills/json-ops/json_ops.py query plan.json --path '$.steps[*].name'
```

## JSONPath 语法

| 表达式 | 含义 |
|--------|------|
| `$.key` | 顶层 key |
| `$.a.b.c` | 嵌套字段 |
| `$.steps[0]` | 数组第 0 个元素 |
| `$.steps[0].name` | 数组元素字段 |

> 简单 JSONPath 始终可用。如有 `jsonpath_ng` 库（`pip install jsonpath-ng`），支持完整 JSONPath 语法。

## 注意事项

- **JSON 含空格/特殊字符时，优先用 stdin 传数据**：`echo '...' | python3 skills/json-ops/json_ops.py append ...`
- **脚本输出都是单行 JSON**，适合 Agent 解析
- **append 自动创建中间路径**（如 `$.steps` 不存在会自动创建空数组）
