# 纺织面料销售自动化 Agent

## 业务概述

面向针织面料（牛奶丝、罗纹、四面弹等）批发销售的自动化系统。无需门店，仓库直发，只做现金交易。

### 业务流
```
抖音/视频号评论 → 私信/电话 → 微信跟进 → 报价 → 成交/分类
```

### 核心指标
- **获客**: 抓取抖音/视频号评论及私信
- **跟进**: 电话 + 微信标准化跟进
- **报价**: 基于产品成本的动态报价
- **成交**: 只做现金，不赊账
- **发货**: 仓库直接发货，无门店

---

## 项目结构

```
fabric-sales-agent/
├── README.md                 # 项目说明
├── CLAUDE.md                 # AI 开发指南
├── requirements.txt          # Python 依赖
├── config.py                 # 全局配置
│
├── prompts/                  # 销售 SOP & 话术
│   ├── sales_sop.md          # 销售标准作业流程
│   ├── classification_rules.md # 客户分类规则
│   ├── standard_scripts.md   # 标准话术
│   ├── pricing_rules.md      # 报价规则
│   └── followup_rules.md     # 跟进规则
│
├── data/                     # 数据层
│   ├── schema.sql            # 数据库建表语句
│   ├── product_catalog.csv   # 产品目录
│   ├── sample_customers.csv  # 客户示例数据
│   ├── sample_chat.csv       # 聊天记录示例
│   └── import_data.py        # 数据导入脚本
│
├── crm/                      # CRM 核心
│   ├── __init__.py
│   ├── database.py           # 数据库操作
│   ├── customer.py           # 客户管理
│   ├── interaction.py        # 互动记录
│   └── report.py             # 报表统计
│
├── agents/                   # 智能体
│   ├── __init__.py
│   ├── base_agent.py         # Agent 基类
│   ├── acquisition_agent.py  # 获客 Agent
│   ├── sales_agent.py        # 销售 Agent
│   ├── followup_agent.py     # 跟进 Agent
│   ├── classification_agent.py # 分类 Agent
│   └── orchestrator.py       # 编排器
│
├── automation/               # 自动化
│   ├── __init__.py
│   ├── wechat_parser.py      # 微信聊天解析
│   ├── comment_scraper.py    # 评论抓取
│   ├── scheduler.py          # 任务调度
│   └── pipeline.py           # 自动化流水线
│
└── scripts/                  # 工具脚本
    ├── classify_customer.py  # 客户分类
    ├── extract_intent.py     # 意图提取
    ├── generate_quote.py     # 生成报价
    ├── generate_followup.py  # 生成跟进建议
    └── analyze_chat.py       # 聊天分析
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 初始化数据库
python -c "from crm.database import Database; db = Database(); db.initialize()"

# 3. 导入产品目录
python data/import_data.py

# 4. 运行自动化流水线
python automation/pipeline.py
```

## 系统架构

### 多 Agent 协作流程

```
[评论/私信] → AcquisitionAgent → [原始线索]
                                      ↓
                              ClassificationAgent → [A/B/C 分类]
                                      ↓
                                SalesAgent → [报价/跟进]
                                      ↓
                              FollowupAgent → [定时跟进]
                                      ↓
                                [成交 / 沉睡 / 放弃]
```

### 客户分类体系

| 类别 | 定义 | 跟进策略 |
|------|------|---------|
| A类 | 意向明确 + 需求匹配 + 决策快 | 24h内电话+微信，重点跟进 |
| B类 | 有意向但需跟进 | 3天内跟进，提供样品/报价 |
| C类 | 无意向/价格敏感/质量存疑 | 7-15天跟进，培养信任 |

## 技术栈

- **Python 3.10+**: 核心开发语言
- **SQLite**: 轻量数据库
- **Pandas**: 数据处理
- **jieba**: 中文分词 & 意图识别
- **schedule**: 定时任务
- **openpyxl**: Excel 导入导出

## 数据安全

- 客户数据本地存储，不上传到第三方
- 微信聊天记录只解析不传播
- 报价信息分级权限访问
