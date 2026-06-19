-- 纺织面料销售自动化系统 - 数据库建表语句

-- 客户表
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                    -- 客户姓名/称呼
    company TEXT,                           -- 公司名
    phone TEXT,                             -- 电话
    wechat_id TEXT UNIQUE,                  -- 微信号
    city TEXT,                              -- 城市
    source TEXT DEFAULT 'douyin',           -- 来源: douyin/shipin/wechat/referral
    category TEXT DEFAULT 'C',             -- A/B/C
    score INTEGER DEFAULT 0,               -- 综合评分 0-100
    status TEXT DEFAULT 'new',             -- new/contacted/negotiating/sample_sent/trial_order/repeat/lost/blacklist
    total_purchased DECIMAL(10,2) DEFAULT 0, -- 累计采购额
    last_contacted_at DATETIME,            -- 最近联系时间
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    notes TEXT                              -- 备注
);

-- 产品目录表
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                     -- 产品名称
    spec TEXT,                              -- 规格 (门幅/克重)
    category TEXT,                          -- 分类: 牛奶丝/罗纹/四面弹/汗布/摇粒绒
    cost_price DECIMAL(10,2) NOT NULL,      -- 成本价(元/m)
    standard_price DECIMAL(10,2) NOT NULL,  -- 标准报价(元/m)
    min_price DECIMAL(10,2) NOT NULL,       -- 最低价(元/m)
    unit TEXT DEFAULT '米',                 -- 单位
    stock_quantity INTEGER DEFAULT 0,       -- 库存量
    is_active BOOLEAN DEFAULT 1,            -- 是否在售
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 互动记录表
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    type TEXT NOT NULL,                     -- wechat_chat/phone_call/comment/dm/visit
    content TEXT,                           -- 互动内容/摘要
    sentiment TEXT DEFAULT 'neutral',       -- positive/negative/neutral
    intent TEXT,                            -- 客户意图: inquiry/pricing/ordering/complaint/other
    followup_action TEXT,                   -- 建议的下一步行动
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

-- 报价单表
CREATE TABLE IF NOT EXISTS quotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_no TEXT UNIQUE NOT NULL,          -- 报价编号 Q-YYYYMMDD-XXXXX
    customer_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,              -- 数量
    unit_price DECIMAL(10,2) NOT NULL,      -- 单价
    total_amount DECIMAL(12,2) NOT NULL,    -- 总金额
    markup_rate DECIMAL(4,2),               -- 加价率
    status TEXT DEFAULT 'sent',             -- draft/sent/accepted/rejected/expired
    valid_until DATE,                       -- 有效期
    notes TEXT,                             -- 备注
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);

-- 订单表
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,          -- 订单编号 O-YYYYMMDD-XXXXX
    customer_id INTEGER NOT NULL,
    quotation_id INTEGER,                   -- 关联报价单
    total_amount DECIMAL(12,2) NOT NULL,
    payment_method TEXT DEFAULT 'cash',     -- cash/transfer
    payment_status TEXT DEFAULT 'pending',  -- pending/paid
    delivery_status TEXT DEFAULT 'pending', -- pending/shipped/delivered
    shipping_address TEXT,
    tracking_no TEXT,                       -- 物流单号
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id),
    FOREIGN KEY (quotation_id) REFERENCES quotations(id)
);

-- 订单明细表
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price DECIMAL(10,2) NOT NULL,
    subtotal DECIMAL(12,2) NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);

-- 跟进任务表
CREATE TABLE IF NOT EXISTS followup_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    task_type TEXT NOT NULL,                -- scheduled/event_driven/manual
    priority TEXT DEFAULT 'normal',         -- high/normal/low
    content TEXT,                           -- 跟进话术/内容
    due_at DATETIME NOT NULL,               -- 应跟进时间
    completed_at DATETIME,                  -- 实际完成时间
    status TEXT DEFAULT 'pending',          -- pending/completed/skipped
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

-- 微信聊天记录表（导入后）
CREATE TABLE IF NOT EXISTS wechat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER,
    msg_id TEXT UNIQUE,
    sender TEXT NOT NULL,                   -- me/customer
    content TEXT,
    msg_type TEXT DEFAULT 'text',          -- text/image/voice
    timestamp DATETIME NOT NULL,
    is_parsed BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

-- 关键词规则表
CREATE TABLE IF NOT EXISTS keyword_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    category TEXT NOT NULL,                 -- intent/product/sentiment
    weight INTEGER DEFAULT 5,              -- 权重 1-10
    is_active BOOLEAN DEFAULT 1
);

-- 自动回复白名单：为空时默认允许所有非黑名单单聊；一旦配置后，仅对白名单联系人自动回复
CREATE TABLE IF NOT EXISTS auto_reply_whitelist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_name TEXT UNIQUE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auto_reply_controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_name TEXT UNIQUE NOT NULL,
    paused BOOLEAN DEFAULT 0,
    manual_takeover BOOLEAN DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auto_reply_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_name TEXT NOT NULL,
    incoming_message TEXT,
    reply_text TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_customers_category ON customers(category);
CREATE INDEX IF NOT EXISTS idx_customers_status ON customers(status);
CREATE INDEX IF NOT EXISTS idx_customers_source ON customers(source);
CREATE INDEX IF NOT EXISTS idx_interactions_customer ON interactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_quotations_customer ON quotations(customer_id);
CREATE INDEX IF NOT EXISTS idx_followup_tasks_due ON followup_tasks(due_at, status);
CREATE INDEX IF NOT EXISTS idx_wechat_messages_customer ON wechat_messages(customer_id);

-- 初始化关键词规则
DELETE FROM keyword_rules;
INSERT INTO keyword_rules (keyword, category, weight) VALUES
-- 意向类
('多少钱', 'intent', 8),
('价格', 'intent', 7),
('怎么卖', 'intent', 7),
('报价', 'intent', 8),
('怎么买', 'intent', 6),
('拿货', 'intent', 8),
('订货', 'intent', 9),
('下单', 'intent', 10),
('要', 'intent', 5),
('需要', 'intent', 5),
-- 产品类
('牛奶丝', 'product', 10),
('罗纹', 'product', 10),
('四面弹', 'product', 10),
('汗布', 'product', 8),
('摇粒绒', 'product', 8),
('卫衣布', 'product', 7),
('针织面料', 'product', 6),
('面料', 'product', 5),
-- 样品类
('样品', 'sample', 8),
('打样', 'sample', 8),
('色卡', 'sample', 7),
-- 决策类
('现货', 'decision', 8),
('交期', 'decision', 7),
('发货', 'decision', 6),
('物流', 'decision', 5),
('急', 'decision', 6),
('什么时候', 'decision', 5),
-- 负面信号
('赊账', 'negative', -8),
('月结', 'negative', -9),
('便宜点', 'negative', -5),
('贵了', 'negative', -4),
('比XX便宜', 'negative', -5),
('先发货', 'negative', -8);
