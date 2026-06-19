# Fabric Sales Agent - 开发指南

## 项目概述

纺织面料销售自动化系统，覆盖获客→跟进→报价→成交全流程。针织面料（牛奶丝、罗纹、四面弹）批发，仓库直发，现金交易。

## 目录结构

- `prompts/` - 销售 SOP 和话术文档（先读此目录理解业务逻辑）
- `crm/` - CRM 核心数据库和业务逻辑
- `agents/` - 多 Agent 智能体系统
- `automation/` - 自动化流水线和外部数据解析
- `scripts/` - 工具脚本
- `data/` - 数据模型和示例数据

## 开发规范

1. **命名**: Python 文件使用 snake_case
2. **类型提示**: 所有函数必须包含类型注解
3. **数据库**: 通过 `crm.database.Database` 单例访问 SQLite
4. **日志**: 使用 Python `logging` 模块，不要 print
5. **配置**: 统一从 `config.py` 读取，不要硬编码

## 数据流

```
WeChat Chat → wechat_parser.py → CRM Database
Douyin Comments → comment_scraper.py → CRM Database
CRM Database → Agents → 跟进/报价/分类 建议
```

## 客户分类规则

- **A类**: 明确询价 + 需求匹配 + 有明确时间计划 + 主动留下联系方式
- **B类**: 询价但模糊 + 问产品细节 + 加了微信未回复 + 提到样品需求
- **C类**: 只问价格不回应 + 无理砍价 + 质量问题质疑 + 长期不回复

## 关键词标签

- 牛奶丝、罗纹、四面弹、针织面料、汗布、摇粒绒、卫衣布、摇粒绒
- 询价、价格、多少钱、样品、起订量、现货、交期
