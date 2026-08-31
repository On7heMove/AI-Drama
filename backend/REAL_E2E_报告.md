# 真实模型端到端报告（3 次固定梗概 · 量化）

> 输入：01_故事梗概_破芯人间重燃.md（固定）｜模型：deepseek-v4-flash
> 链路：run_inverse（梗概 → spine → beats → 单集剧本 → run_quality）
> 密钥：仅内存注入，不落盘/不打印；生产级不声明。

## 量化结果（3/3 轮）

| 指标 | 结果 |
|---|---|
| 结构达标率 structure_ok_rate | **1.0**（3/3 均产出 1 集剧本） |
| 每轮场景数 | 3 / 3 / 5 |
| 剧本字符数 | 894 / 854 / 1332（偏短） |
| 逻辑错误 logic_errors | **0**（3 轮均 0） |
| 硬错误 fatal_issues | **0**（3 轮均 0） |
| 质检通过率 pass_rate | 0.0（每轮 5-7 个 error 项） |
| 伏笔登记/回收 | 0（1 集内未登记，需跨集验证） |
| 单轮耗时 | 6-7 分钟 |

## 质检 error 项构成（pass_rate 0 根因）
- scene_duration：每场 ≈18s（标准 30-90s）→ 3-5 项
- episode_duration：单集总时长不足 150s → 1 项
- conflict_upgrade：1-10 集无升级型爆点 → 1 项

**根因**：episode.md 模板未要求每场完整展开（每场仅 1 句动作/对白），导致单集内容偏短、时长未达标。链路本身已跑通。

## 结论
```text
链路贯通：梗概 → 骨架 → 节拍 → 剧本 → 质检 ✅（structure 100%）
内容达标：❌（单集偏短，时长不足；pass_rate 0）
逻辑/硬错：✅（0）
伏笔回收：未验证（需多集）
生产声明：not_claimed
```

## 下一步（若继续）
1. 强化 episode.md 模板：每场 2-4 动作块 + 3-6 轮对白 + 单集 150-240s + 每场 30-90s
2. 重跑 3 次，验证 pass_rate 提升
3. 多集（eps 1-2）验证伏笔登记/回收

## 产物
- backend/_e2e_out/real_e2e_{1,2,3}.json（完整结构化结果）
- backend/_e2e_out/real_e2e_{1,2,3}_screenplay.md（剧本）
- backend/_e2e_out/real_e2e_summary.json（汇总）
