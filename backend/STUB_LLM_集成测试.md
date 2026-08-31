# stub LLM 离线集成测试 · 验证记录

> 目标（评估方优先顺序第 3 步）：以 stub LLM（不调真实模型/网络）验证"逆推→生成→质检"闭环。

## 结果
- `backend/tests/test_offline_integration.py`：**1 passed**
- 全量 pytest：**24 passed / 4 skipped / 0 failed**
- 闭环：梗概 → spine（build_spine）→ story line → beats → 单集剧本 → run_quality → 导出结构

## 修复过程中发现的真实 bug
- `spine_storyline.storyline_from_spine` 用 `plan.get()`，但 plan 是 pydantic 对象（SpinePlan）→ 已修复为 dict/pydantic 双兼容（_g/_items）

## 补齐的资源
- `config/production/prompts/{outline,episode,quality,storyline}.md`（prompt 模板，含 {{占位符}}）
- `config/production/{character_library,material_library,quality_rubric,themes}.json`

## Stub 机制
- StubClient 实现 DeepSeekClient 接口（chat / chat_json / model）
- chat_json 按调用顺序返回固定合法 JSON（spine / beats / episode）
- 不发起任何网络请求，不读取密钥

## 意义
- 证明主链"梗概→骨架→节拍→剧本→质检"在受控输入下可端到端运行
- 为真实模型端到端（第 4 步）提供基线：同一测试替换 StubClient 为 DeepSeekClient 即可

## Git
- 4ae434e feat: stub LLM 离线集成测试通过 + 补齐 prompt 模板与配置
