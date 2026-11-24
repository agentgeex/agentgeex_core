---
reply_mode: result
---
# 任务：企业级 Flask Web 管理系统开发 (task_flask_dev)

## 概述
作为 Python 专家，本任务要求你使用 Flask 框架为 AgentGeeX 开发官方的 Web 管理后台系统。整个开发必须严格遵循企业级标准，保证高内聚低耦合。

## 核心架构约束
1. **蓝图架构 (Blueprint)**: 禁止将所有路由写在 `app.py` 中。必须按业务模块（如 `auth`, `agents`, `tasks`, `metrics`）划分为独立的 Blueprints。
2. **数据持久化 (ORM)**: 统一采用 `Flask-SQLAlchemy` 进行数据库操作，严禁在业务层拼接原生 SQL。
3. **序列化与校验**: 必须使用 `Marshmallow` 或 `Pydantic` 进行入参校验和出参序列化，严禁直接手写 `request.form.get()` 后不做类型转换。
4. **统一响应契约**: 所有 API 返回格式必须统一为：
   ```json
   {
     "code": 200, 
     "success": true,
     "data": {},
     "message": "success"
   }