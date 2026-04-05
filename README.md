# Recommendation Agent Backend

文学推荐 Agent 的 FastAPI 后端。根据用户情绪与生活状态推荐作家，记录偏好演化，提供个性化书单。与 reader-agent 联动，完成"手机约书 → 实体阅读器"的跨设备体验。

---

## 系统架构

```
recommendation-agent-frontend (port 3000)
    │ /api/* → port 8000
    │ /mobile/* → reader-agent port 8001
    ▼
FastAPI 后端 (port 8000)
    │ Zhipu AI (glm-4.5 / glm-4.5-air)
    ▼
SQLite (recommendation.db)    ← 用户画像、对话记录、推荐历史
```

---

## 目录结构

```
recommendation-agent-backend/
├── main.py         # FastAPI 路由
├── agent.py        # AI 核心：推荐逻辑 + 流式对话 + 信号提取
├── database.py     # SQLite CRUD
├── models.py       # Pydantic 数据模型
├── seed_writers.py # 初始化作家数据
├── requirements.txt
└── .env.example
```

---

## 快速开始（新电脑）

### 前置条件

- Python 3.10+
- [智谱 AI](https://open.bigmodel.cn) API Key

### 安装

```bash
cd recommendation-agent-backend

python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# 编辑 .env，填入 ZHIPU_API_KEY
```

### 启动

```bash
python -m uvicorn main:app --port=8000 --reload
```

首次启动会自动：
1. 创建 `recommendation.db`
2. 运行 `seed_writers.py` 初始化作家数据（幂等，可重复运行）

访问 `http://localhost:8000/docs` 查看完整接口文档。

---

## 主要 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `POST /users` | POST | 创建用户（首次使用） |
| `GET /users/{user_id}` | GET | 获取用户信息 |
| `POST /users/{user_id}/chat/{agent_id}` | POST | 流式对话（SSE） |
| `GET /users/{user_id}/conversations/{agent_id}` | GET | 获取对话历史 |
| `GET /users/{user_id}/profile` | GET | 用户阅读画像 |
| `GET /users/{user_id}/recommendations` | GET | 推荐历史 |
| `POST /users/{user_id}/recommendations/{rec_id}/reaction` | POST | 记录对推荐的反应 |
| `GET /feed` | GET | 作家朋友圈动态 |
| `GET /writers` | GET | 作家列表 |
| `POST /users/{user_id}/reminders` | POST | 创建阅读提醒 |

`agent_id` 可以是 `recommendation`（推荐 Agent）或任意 `writer_id`（与特定作家对话）。

---

## 与 reader-agent 联动

当用户在手机端与伍尔夫确认阅读时间后，前端自动调用 reader-agent 的 `/mobile/set_book` 接口同步书目，阅读器设备启动时拉取该书目。

联动地址配置在 `recommendation-agent-frontend/vite.config.js`：
```js
'/mobile': { target: 'http://localhost:8001' }  // reader-agent 地址
```

如果 reader-agent 部署在其他机器，修改此处地址即可。

---

## 添加新作家

编辑 `seed_writers.py`，在 `WRITERS` 列表中添加：

```python
{
    "id": "writer_id",           # 唯一标识，用于路由
    "name": "作家名（中文）",
    "name_en": "Author Name",
    "avatar_initial": "W",       # 头像显示的首字母
    "bio": "一句话简介",
    "personality_tags": ["标签1", "标签2"],
    "works": ["代表作1", "代表作2"],
}
```

重启后端自动生效（`seed_writers.py` 使用 `INSERT OR REPLACE`，幂等）。

---

## 环境变量

```env
ZHIPU_API_KEY=          # 必填
ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
ZHIPU_MODEL_MAIN=glm-4.5       # 主对话
ZHIPU_MODEL_FAST=glm-4.5-air   # 信号提取、朋友圈生成
```
