# 数据分析实验与数据应用服务平台

面向指标口径管理、分析实验设计、OLAP查询、数据看板构建与数据API发布的数据应用平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标 | 指标编号、指标名称、计算公式 |
| 维度管理 | `dimension` | 分析维度 | 维度编号、维度名称、维度类型 |
| 数据集管理 | `dataset` | 分析数据集 | 数据集编号、数据集名称、来源类型 |
| 交付对账 | `reconcile` | 签单 / 接入批次 | 签单编号、交付编号、应到字段、行标识、承诺数量 |
| 分析实验 | `experiment` | 实验方案 | 方案编号、实验名称、分析数据集 |
| OLAP查询 | `query_builder` | 查询模板 | 模板编号、模板名称、查询语句 |
| 数据看板 | `dashboard` | 看板配置 | 看板编号、看板名称、包含图表 |
| 图表配置 | `chart` | 图表 | 图表编号、图表名称、图表类型 |
| 数据API发布 | `data_service` | 数据服务 | 服务编号、服务名称、输出数据集 |
| 智能告警 | `alert` | 告警规则 | 规则编号、告警名称、监控指标 |
| 分析用户组 | `user_group` | 用户组 | 组编号、组名称、组描述 |
| 数据快照 | `snapshot` | 快照记录 | 快照编号、数据集名称、快照时间 |
| 任务调度 | `scheduler` | 调度任务 | 任务编号、任务名称、调度类型 |
| 缓存管理 | `cache` | 缓存配置 | 缓存编号、缓存目标、缓存策略 |
| 分析管道 | `pipeline` | 分析节点 | 节点编号、输入数据集、分析算子 |
| 分析模型 | `model` | 模型配置 | 模型编号、模型名称、模型类型 |
| 数据集市 | `data_mart` | 集市表 | 表编号、表名称、主题域 |
| 数据导出 | `export` | 导出任务 | 任务编号、导出对象、导出格式 |
| 数据审计 | `audit` | 审计记录 | 记录编号、操作类型、操作人 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 交付对账口径

交付对账（`backend/app/services/reconcile.py`）用供应方签单核对接入批次到货，核心口径：

1. **承诺只抵扣一次**：同一行标识的首次到货形成唯一一次承诺抵扣，之后重送的原行只记
   「重复到货」差错，不增加已抵扣数量；补发差额看 `抵扣差额 = 承诺数量 - 已抵扣数量`。
2. **更正行取代当前版本**：更正行决定当前实际可用数量与字段校验结果，但抵扣量仍冻结在
   首单，不回补、不增加；当前版本优先级为 最近更正行 > 最近到货 > 首单。
3. **两类差错分别复核**：字段错位（应到字段缺失/多余，按凭据分别立账）与整行丢失
   （行标识从未出现）是不同差错类型，各自带复核键，人工结论按键沉淀，重送后仍保留。
4. **应收行数只认签单**：同一交付编号重送两次只计一次应收，重复交付单独标红；
   不在签单里的行标识记「超额行」，不计入任何可用量。
5. **对账依据可冻结**：批次确认时冻结当时的对账单快照，后续重送不改写历史依据；
   数据API发布可用 `/api/data_service/{id}/publish_with_basis` 按交付缺行对账单计算
   可交付编号集合并随发布固化依据。
6. **数据来源**：对账数据是嵌套结构（签单→批次→复核），存放在
   `ReconcileRepository`，与通用扁平 `store` 分开；启动时由 `seed_reconcile()`
   写入两份覆盖各口径的示例签单。
