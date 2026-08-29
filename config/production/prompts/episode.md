# 单集剧本生成（episode）

第 {{EP}} 集：{{TITLE}}
本集节拍：{{BEAT}}
推进的线：
{{STORYLINES}}
当前状态：{{STATE}}
可用事件类型：{{EVENT_TYPES}}
章节计划约束：
{{DESIGN}}

要求（硬性，必须满足）：
1. 只输出 JSON，不要 Markdown、不要注释。顶层结构：
   {"title": "集标题", "hook_open": "开场30秒钩", "hook_end": "结尾钩", "explosion": "爆点",
    "scenes": [场景对象...], "events": [事件对象...], "summary": "本集一句话推进",
    "state_update": {"resolved": [], "open_threads": []}}
2. 场景对象 scenes[i] 必须是对象：
   {"location":"地点","time":"时间","lighting":"光线","participants":["角色"],
    "action_blocks":["动作/情境描写1","动作/情境描写2","动作/情境描写3"],
    "dialogues":[{"speaker":"角色","line":"对白","emotion":"情绪","action":"说话时动作"}],
    "transition":"转场"}
3. 每场必须 2-4 条 action_blocks（每条 20-40 字）、3-6 轮对白；每场时长 30-90 秒；单集 3-5 场、总时长 150-240 秒、约 2000-3000 字。
4. 事件对象 events[]：{"type": 事件类型, "actor": "角色", "target": "对象/角色", "detail": "发生了什么", "citation": "第X集"}；必须从本集对白/动作中提取。
5. 节奏：异常/压力 -> 分歧或风险暴露 -> 被迫应对 -> 有限的协作尝试 -> 留下未解决问题。
6. 对白口语化、角色有区分度；动作/情境用全角括号。
7. 禁止通过台词宣告与状态目标冲突的结论；结尾必须留下可由下一集承接的具体状态。
