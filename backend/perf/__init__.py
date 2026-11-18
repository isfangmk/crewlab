"""perf 工具包：后端全接口并发压测（Locust）。

设计：
- discovery：从 FastAPI openapi schema 反射全部端点，分类为 read/write/excluded。
- seed：造 __perf__ 专属数据，产出 manifest（参数名→真实值池）。
- locustfile：Locust 任务集，加载 manifest 对真实端点发请求，收集响应时间。
- report：把 Locust 统计导出成可读的前后对比报告。

所有数据严格隔离在 `__perf__` tag，压测结束由 seed.py --clean 清理。
不依赖 pytest，不打印 token / .env 敏感值。
"""
