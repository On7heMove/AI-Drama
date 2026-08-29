# backend 唯一权威合并说明（第 1 步：完成）

> 目标：把分散的两份 backend 合并为单份可完整导入的 `backend/`，并恢复 `config/` 与数据资源。
> 结论：**主链全部可导入（24/24），全部现有单测通过（23 passed / 0 failed / 4 skipped）。**

## 合并规则
- 权威基础：`_瀑布流_提示词模块\backend`（prompts / llm_client / schemas / parse / paths / config / 全套 storyboard）
- 补入件：`_增量包解压\backend` 中仅存在于该处的文件（api/inverse、api/works、inverse/service、services/works_store、story/foreshadow_ledger、production/{episode_writer,outline,quality,spine,storyline,storyboard_export}、prompt_factory、main、main_lite、tests）
- 同名文件保留 `_瀑布流` 权威版（8 个冲突）
- `.env` 未复制（密钥留在 `_瀑布流_提示词模块\backend\.env`）

## 自行补齐的缺失模块（8 个，评估方所列主链缺件）
| 模块 | 接口 |
|---|---|
| app/schemas/events.py | Event / EventType / TimeRef |
| app/schemas/issues.py | Severity |
| app/library/splitter.py | split_chapters(text) |
| app/engine/detector.py | DetectionEngine.process(events) |
| app/production/knowledge_inject.py | knowledge_constraints(stage) |
| app/production/skill_inject.py | skill_constraints(stage, genre) |
| app/production/bible_check.py | check_bible_constraints(episodes, bible) |
| app/quality/emotion_pacing.py | pacing_from_text(text, expected_arc, params) |
| app/production/spine_storyline.py | storyline_from_spine(plan, brief) |
| app/services/jobs.py | create/set_running/update/finish/get/job_to_dict |

## 恢复的资源（根目录）
- `config/`：从 `_瀑布流_提示词模块\config`（权威）复制（quality / skills / storyboard）
- `config/story/`：`beat_structures.json`（_增量包复制）+ 自补 conflict_types / hook_types / arc_shapes / thresholds（格式与 beat_structures 一致）
- `books/`：从 `_瀑布流_提示词模块\books`（权威）复制（audiovisual-language/skill_contracts.json）

## 依赖
- fastapi 已安装（0.141.1，api.inverse 需要）

## 验证
- 主链 import：24/24 通过（含 api.inverse / inverse.service / quality / spine_storyline / jobs）
- pytest（backend/tests）：23 passed / 4 skipped / 0 failed
  - 此前评估：6 个测试文件 5 个收集阻断；现全部收集并通过
- 冒烟：split_chapters / DetectionEngine / knowledge_constraints / skill_constraints / storyline_from_spine 均正常

## 仍待办（评估方优先顺序后续步骤）
2. 依赖清单（requirements.txt）与启动命令
3. 源码纳入 Git 首次提交
4. stub LLM 最小离线集成测试
5. 真实模型 3 次端到端 + 量化
