# AI 短剧生产线

梗概 -> 主线骨架(8-15节点) -> 分集大纲(beats) -> 逐集剧本 -> 本地质检 的离线/在线生成产线。

## 目录
- backend/    唯一权威源码（FastAPI 服务 + 逆推/生成/质检）
- config/     配置（prompt 模板 / cangjie 蒸馏规则 / storyboard 参数）
- books/      数据资源（skill_contracts.json）

## 安装依赖
```
cd backend
pip install -r requirements.txt
```

## 配置密钥（真实生成必需；密钥不入库）
在 `backend/.env` 写入（替换为你自己的 key）：
```
DEEPSEEK_API_KEY=sk-xxxx
```
> `.env` 已被 .gitignore 排除，不会提交。未配置 key 时：模块可导入、单测可跑、stub 可跑；真实 LLM 生成会报"未配置 DEEPSEEK_API_KEY"。

## 启动服务
```
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```
- 首页：http://127.0.0.1:8080/
- 骨架页（最近一轮）：http://127.0.0.1:8080/spine
- 接口文档：http://127.0.0.1:8080/docs

## 运行测试
```
cd backend
python -m pytest tests -q
# 预期：24 passed, 4 skipped
```

## 端到端生成（真实 LLM，需 DEEPSEEK_API_KEY）
```
cd backend
python run_real_e2e.py 1        # 跑 1 轮（约 7 分钟），产物落 _e2e_out/
python run_real_e2e.py 3        # 跑 3 轮量化
```

## 说明
- 方法论注入源：config/story/skills_v2.json（8-28 cangjie 蒸馏，save-the-cat + mckee 33 条）
- 结构达标率：梗概->骨架->beats->剧本->质检 链路已验证可跑通（真实模型）
- 生产资格：not_claimed（未达生产级声明）
