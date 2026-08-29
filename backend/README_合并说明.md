# backend 唯一权威合并说明（完整）

> 结论：**主链全部可导入（68/68），main 可启动（18 routes），全部现有单测通过（23 passed / 0 failed / 4 skipped）。**

## 合并
- 权威基础：_瀑布流 backend；补入 _增量包 缺失件；同名保留 _瀑布流 权威版
- 资源：config/（权威）、config/story/（beat_structures + 自补 4 规则）、books/（skill_contracts.json）

## 自行补齐的模块（15 个）
第一批（主链缺件，8+2）：
- app/schemas/events.py（Event/EventType/TimeRef，含 RISE/FALL/FIGHT/INJURY/SPEECH/UNCONSCIOUS/WIELD）
- app/schemas/issues.py（Severity）
- app/library/splitter.py · app/engine/detector.py
- app/production/{knowledge_inject, skill_inject, bible_check, spine_storyline, segment_export, verb_selector}.py
- app/quality/emotion_pacing.py · app/services/jobs.py

第二批（main/逆推入口，13）：
- app/state/{character_state, store}.py（CharacterState/BodyDamage/Consciousness/Stance/EventSourcingStore）
- app/api/{auth, license, production, settings}.py（FastAPI routers）
- app/schemas/character.py（CharacterProfile）· app/schemas/review.py（CustomsOverride）
- app/review/customs_store.py（CustomsStore）
- app/inverse/session.py（create/load/save_session）
- app/production/verb_selector.py（select_verb）· segment_export.py（long_dialogue_plan）

## 依赖
- fastapi、python-multipart（已安装）；requirements.txt 记录

## 验证
- app 模块 import：68/68，fail 0
- main（FastAPI）：import OK，18 routes；main_lite：OK
- pytest：23 passed / 0 failed / 4 skipped
- run_fresh_inverse：模块可导入；运行时需历史 session + 真实 LLM（KeyError 'input' 为缺 session 数据，非代码缺失）

## 仍待办
- stub LLM 离线集成测试（逆推→生成→质检→导出闭环）
- 真实模型 3 次端到端 + 量化指标
