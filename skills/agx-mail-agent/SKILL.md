---
name: mail-agent
description: 多邮箱收发邮件的工具。支持 SMTP 发送和 IMAP 接收，可配置多个邮箱账号（QQ邮箱、163邮箱、公司邮箱等）。适用于邮件收发、邮件查询、邮件归档等场景。
---

# Mail Agent - 多邮箱邮件工具

通过本地 Python 脚本实现多邮箱的收发邮件功能。

## 前置条件

- Python 3（系统自带）
- 各邮箱需开启 SMTP/IMAP 服务并获取**授权码**

## 配置管理

### 添加邮箱账号

```bash
python3 scripts/mail_agent.py add_account '{"name":"qq", "email":"your@qq.com", "smtp_host":"smtp.qq.com", "smtp_port":465, "smtp_pass":"授权码", "imap_host":"imap.qq.com", "imap_port":993, "imap_pass":"授权码", "display_name":"我的QQ邮箱"}'
```

参数说明：
- `name` - 账号别名（用于后续操作指代）
- `email` - 邮箱地址
- `smtp_host` / `smtp_port` - SMTP 服务器（发邮件）
- `smtp_user` / `smtp_pass` - SMTP 用户名和密码（密码填授权码）
- `imap_host` / `imap_port` - IMAP 服务器（收邮件），可选
- `imap_user` / `imap_pass` - IMAP 用户名和密码，可选

### 列出已配置账号

```bash
python3 scripts/mail_agent.py list_accounts
```

### 删除账号

```bash
python3 scripts/mail_agent.py remove_account '{"name":"qq"}'
```

## 发送邮件

### 方式一：使用预设账号
```bash
python3 scripts/mail_agent.py send '{"account":"qq", "to":"friend@example.com", "subject":"你好", "body":"邮件正文内容"}'
```

### 方式二：临时传入邮箱配置（无需预设）
```bash
python3 scripts/mail_agent.py send '{
  "account":"同事小李",
  "to":"boss@company.com",
  "subject":"周报",
  "body":"本周工作内容...",
  "mail_config": {
    "email":"xiaoli@company.com",
    "name":"小李",
    "smtp_host":"smtp.company.com",
    "smtp_port":465,
    "smtp_user":"xiaoli@company.com",
    "smtp_pass":"授权码"
  }
}'
```

可选参数：
- `cc` - 抄送
- `bcc` - 密送
- `is_html` - 是否 HTML 格式（true/false）
- `mail_config` - 临时邮箱配置对象（覆盖预设账号）

## 接收邮件

### 方式一：使用预设账号
```bash
python3 scripts/mail_agent.py inbox '{"account":"qq", "limit":10, "folder":"INBOX", "since_days":7}'
```

### 方式二：临时传入邮箱配置
```bash
python3 scripts/mail_agent.py inbox '{
  "account":"同事小李的邮箱",
  "limit":5,
  "mail_config": {
    "email":"xiaoli@company.com",
    "pop_host":"pop.company.com",
    "pop_port":995,
    "pop_user":"xiaoli@company.com",
    "pop_pass":"授权码"
  }
}'
```

参数说明：
- `limit` - 返回邮件数量上限
- `folder` - 邮件夹（默认 INBOX）
- `search` - IMAP 搜索条件（如 UNSEEN 查未读，FROM someone@example.com）
- `since_days` - 最近 N 天的邮件
- `mail_config` - 临时邮箱配置对象

### 常用搜索条件
- 未读邮件：`{"account":"qq", "search":"UNSEEN"}`
- 指定发件人：`{"account":"qq", "search":"FROM someone@example.com"}`
- 指定主题：`{"account":"qq", "search":"SUBJECT 通知"}`
- 最近3天：`{"account":"qq", "since_days":3}`

## 查看邮件详情

```bash
python3 scripts/mail_agent.py view '{"account":"qq", "id":1}'
```

也可临时传入邮箱配置：
```bash
python3 scripts/mail_agent.py view '{"account":"temp", "id":1, "mail_config":{...}}'
```

## 常见邮箱 SMTP/IMAP 配置

| 邮箱 | SMTP | SMTP端口 | IMAP | IMAP端口 |
|:---:|:---:|:---:|:---:|:---:|
| QQ邮箱 | smtp.qq.com | 465 | imap.qq.com | 993 |
| 163邮箱 | smtp.163.com | 25/465 | imap.163.com | 993 |
| **126邮箱** | smtp.126.com | **25** | imap.126.com | 993 |
| Gmail | smtp.gmail.com | 587 | imap.gmail.com | 993 |
| Outlook | smtp.office365.com | 587 | outlook.office365.com | 993 |

## 已知问题

### 126 邮箱 IMAP 不安全登录
126 邮箱的 IMAP 可能会提示 "Unsafe Login" 并拒绝连接。需要在 126 网页邮箱的「设置→账号安全→客户端密码」中开启授权，或使用客户端专用密码。

### 端口选择
- 端口 **25** 或 **587**：使用 STARTTLS 加密
- 端口 **465**：使用 SSL 直连加密

## 提示

- 授权码请在邮箱设置中生成，非登录密码
- 配置文件 `scripts/mail_config.json` 存储账号信息（含密码），注意保护
