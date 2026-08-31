# 分集大纲生成（outline）

故事线：
{{STORYLINE}}

生成范围：第 {{RANGE}} 集
前情：{{PREV}}
章节计划约束：
{{DESIGN}}

要求：
- 只输出 JSON，不要 Markdown、不要注释。
- 顶层结构必须是对象：{"beats": [...]}
- beats 数组长度 = 该范围集数；每个 beat 是对象：
  {"ep": 序号, "title": "标题", "hook_open": "开场钩", "hook_end": "结尾钩", "explosion": "爆点", "explosion_type": "反转/强冲突/情绪顶点/信息炸弹/身份揭露/威胁升级/关系破裂", "emotional_curve": ["起","升","钩"], "lines_advanced": ["线名"], "scenes": [{"scene_id":"s1","location":"地点","time":"时间","participants":["角色"],"summary":"这场发生什么"}]}
- scenes 必须是对象数组，禁止用字符串数组
- 每集一个明确推进 + 一个未解决问题；集尾保留钩子
