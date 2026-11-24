/*
Navicat Premium Dump SQL

Source Server         : localhost_phpstudy
Source Server Type    : MySQL
Source Server Version : 50728 (5.7.28)
Source Host           : 127.0.0.1:3306
Source Schema         : agx

Target Server Type    : MySQL
Target Server Version : 50728 (5.7.28)
File Encoding         : 65001

Date: 25/05/2026 18:37:48
 */
SET
  NAMES utf8mb4;

SET
  FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for agx_agent_group
-- ----------------------------
DROP TABLE IF EXISTS `agx_agent_group`;

CREATE TABLE
  `agx_agent_group` (
    `pkid` int (11) NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `network_id` varchar(64) NOT NULL COMMENT '所属网络',
    `group_id` varchar(64) NOT NULL COMMENT '团队ID',
    `group_name` varchar(128) NOT NULL COMMENT '团队名称',
    `x_desc` longtext COMMENT '团队描述',
    `active_status` tinyint (1) DEFAULT '1' COMMENT '启禁用 0-停用 1-启用',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `uk_group_id` (`group_id`) USING BTREE,
    KEY `idx_network_id` (`network_id`) USING BTREE
  ) ENGINE = InnoDB AUTO_INCREMENT = 34 DEFAULT CHARSET = utf8mb4 COMMENT = 'Agent 团队分组\n';

-- ----------------------------
-- Table structure for agx_agent_group_member
-- ----------------------------
DROP TABLE IF EXISTS `agx_agent_group_member`;

CREATE TABLE
  `agx_agent_group_member` (
    `pkid` int (11) NOT NULL AUTO_INCREMENT,
    `network_id` varchar(64) NOT NULL COMMENT '网络编号',
    `group_id` varchar(64) NOT NULL COMMENT 'Agent组 ID',
    `agent_id` varchar(64) NOT NULL COMMENT 'Agent ID',
    `active_status` tinyint (1) DEFAULT '1' COMMENT '激活状态',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`pkid`),
    UNIQUE KEY `uk_group_agent` (`group_id`, `agent_id`),
    KEY `idx_network` (`network_id`),
    KEY `idx_agent` (`agent_id`)
  ) ENGINE = InnoDB AUTO_INCREMENT = 72 DEFAULT CHARSET = utf8mb4 COMMENT = 'Agent-Group对应关系表';

-- ----------------------------
-- Table structure for agx_agent_notice
-- ----------------------------
DROP TABLE IF EXISTS `agx_agent_notice`;

CREATE TABLE
  `agx_agent_notice` (
    `pkid` int (11) NOT NULL AUTO_INCREMENT,
    `notice_id` varchar(64) NOT NULL COMMENT '公告/日志唯一ID',
    `network_id` varchar(64) NOT NULL COMMENT '所属网络',
    `project_id` varchar(64) NOT NULL COMMENT '所属项目',
    `agent_id` varchar(64) NOT NULL COMMENT '发布者 Agent ID',
    `task_id` varchar(64) DEFAULT NULL COMMENT '关联任务ID（可选）',
    `run_uuid` varchar(64) DEFAULT NULL COMMENT '关联执行实例ID（可选）',
    `notice_type` enum (
      'log',
      'decision',
      'error',
      'handoff',
      'config',
      'other'
    ) NOT NULL DEFAULT 'log' COMMENT '类型: log=工作日志, decision=决策记录, error=踩坑记录, handoff=交接记录, config=配置变更',
    `title` varchar(256) DEFAULT NULL COMMENT '标题',
    `content` longtext NOT NULL COMMENT '正文',
    `visibility` enum ('private', 'project', 'network') NOT NULL DEFAULT 'project' COMMENT '可见范围: private=仅自己, project=同项目, network=全网',
    `active_status` tinyint (1) DEFAULT '1' COMMENT '生效状态: 0=已归档/无效, 1=有效',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `idx_notice_id` (`notice_id`) USING BTREE,
    KEY `idx_agent_time` (`agent_id`, `created_at`) USING BTREE,
    KEY `idx_project_type` (`project_id`, `notice_type`) USING BTREE,
    KEY `idx_task` (`task_id`) USING BTREE,
    KEY `idx_run` (`run_uuid`) USING BTREE,
    KEY `idx_network` (`network_id`) USING BTREE
  ) ENGINE = InnoDB AUTO_INCREMENT = 10 DEFAULT CHARSET = utf8mb4 COMMENT = 'Agent 公告/日志集中管理表';

-- ----------------------------
-- Table structure for agx_agent_registry
-- ----------------------------
DROP TABLE IF EXISTS `agx_agent_registry`;

CREATE TABLE
  `agx_agent_registry` (
    `pkid` int (11) NOT NULL AUTO_INCREMENT,
    `agent_labs` varchar(128) DEFAULT NULL COMMENT 'Agent 标签，对应 agents/ 目录中的文件夹名称（如 "agx_core"、"agents_host"）\n\n',
    `agent_type` varchar(32) DEFAULT 'openclaw' COMMENT 'Agetnt类型: openclaw | claude_code | custom',
    `project_id` varchar(64) NOT NULL DEFAULT 'system_project' COMMENT '所属项目ID，system_project=系统级，proj_xxx=项目级',
    `peer_agent_id` varchar(64) DEFAULT NULL COMMENT '来源Agent ID（peer），NULL表示原生Agent，非NULL表示由agx_agentfather克隆而来',
    `network_id` varchar(64) NOT NULL COMMENT '网络编号，由环境变量或配置文件统一提供，当前表内业务隔离标识',
    `host_id` varchar(64) DEFAULT NULL COMMENT '所属主机ID，与表agx_host_inventory的字段host_id关联',
    `agent_id` varchar(64) NOT NULL COMMENT '智能体逻辑ID',
    `agent_name` varchar(128) NOT NULL COMMENT '智能体名称',
    `description` text COMMENT '智能体简洁描述（对其它 Agent 公开的身份名片，区别于 x_desc 的完整语义描述）',
    `x_desc` longtext COMMENT '语义描述',
    `agent_role` enum (
      'workshop_manager',
      'host_manager',
      'agent_manager',
      'project_manager',
      'worker'
    ) NOT NULL COMMENT '角色',
    `scope` enum ('system', 'project') NOT NULL DEFAULT 'project' COMMENT '权限范围: system=全局, project=所属项目内',
    `is_project_admin` tinyint (1) NOT NULL DEFAULT '0' COMMENT '是否为项目管理员(scope=project时有效)ˆ)',
    `agent_email` varchar(64) DEFAULT NULL COMMENT '邮箱名称',
    `agent_email_config` json DEFAULT NULL COMMENT 'SMTP配置',
    `agent_telegram_token` varchar(128) DEFAULT NULL COMMENT 'TG Token',
    `agent_feishu_token` varchar(128) DEFAULT NULL COMMENT '飞书 Token',
    `agent_avatar` varchar(512) DEFAULT NULL COMMENT 'Agent 头像（URL 或本地路径，不存二进制）',
    `default_session_id` varchar(128) DEFAULT NULL COMMENT '默认会话ID，格式agent:{agent_id}:main',
    `base_model` varchar(64) DEFAULT NULL COMMENT '底层模型',
    `system_prompt` longtext COMMENT 'SOUL 配置',
    `mcp_config` json DEFAULT NULL COMMENT '工具链配置',
    `active_status` tinyint (1) DEFAULT '1' COMMENT '管理：0-禁用, 1-启用',
    `deploy_status` enum (
      'pending',
      'deploying',
      'deployed',
      'failed',
      'template'
    ) DEFAULT 'pending' COMMENT '部署状态: pending=待部署, deploying=部署中, deployed=已部署, failed=失败',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间（由 MySQL 自动维护，任何 UPDATE 都会刷新）',
    `agent_root_dir` varchar(256) DEFAULT NULL COMMENT 'Agent 跟目录',
    `agent_workspace_dir` varchar(256) DEFAULT NULL COMMENT 'Agent工作目录',
    `agents_md` longtext COMMENT 'AGENTS.md  agent的社交网络',
    `heartbeat_md` longtext COMMENT 'HEARTBEAT.md agent的动态记忆',
    `identity_md` longtext COMMENT 'IDENTITY.md agent的身份信息',
    `soul_md` longtext COMMENT 'SOUL.md agent的价值观和动机',
    `tools_md` longtext COMMENT 'TOOLS.md agent的技能清单',
    `notebook_md` longtext COMMENT 'NOTEBOOK.md Agent 笔记索引与记笔记规则',
    `self_md` longtext COMMENT 'notices/self.md Agent 私有笔记',
    `user_md` longtext COMMENT 'USER.md agent的用户画像',
    `skills_allowlist` json DEFAULT NULL COMMENT 'Agent 自选 skill 白名单(JSON数组, agent 名字): 该 agent 主动挑选并确认可用的 skill 名列表; NULL=未配置(继承全局)',
    `dispatch_exempt_until` datetime DEFAULT NULL COMMENT '调度豁免到期时间: NULL=无豁免; 晚于当前时间=该 Agent 可调用调度类工具(agx_dispatch_task/agx_schedule_task/agx_quick_cmd_send), 到期自动失效。仅放开调度, 不放开建项目/管 Agent',
    `is_quick` tinyint (1) NOT NULL DEFAULT '0' COMMENT '快捷入口标记 0-否 1-是',
    `quick_order` int NOT NULL DEFAULT '0' COMMENT '快捷入口排序（越小越前）',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `idx_agent_id` (`agent_id`) USING BTREE,
    KEY `idx_role_lookup` (`network_id`, `agent_role`) USING BTREE
  ) ENGINE = InnoDB AUTO_INCREMENT = 83 DEFAULT CHARSET = utf8mb4 COMMENT = '智能体能力注册表';

-- ----------------------------
-- Table structure for agx_host_inventory
-- ----------------------------
DROP TABLE IF EXISTS `agx_host_inventory`;

CREATE TABLE
  `agx_host_inventory` (
    `pkid` int (11) NOT NULL AUTO_INCREMENT,
    `network_id` varchar(64) NOT NULL COMMENT '网络编号，由环境变量或配置文件统一提供，当前表内业务隔离标识',
    `host_id` varchar(64) NOT NULL COMMENT '推理引擎逻辑ID（列名沿用历史命名，保留不改以兼容存量引用）',
    `host_alias` varchar(128) DEFAULT NULL COMMENT '推理引擎显示名',
    `endpoint_ip` varchar(45) NOT NULL COMMENT '引擎入口地址',
    `ssh_port` int (11) DEFAULT '22' COMMENT 'SSH 端口（运维可达时填写，不参与调度）',
    `os_platform` varchar(32) DEFAULT NULL COMMENT '宿主操作系统：macOS / Linux / WSL2 / Windows',
    `platforms` json DEFAULT NULL COMMENT '本引擎可用的 agent 平台及访问方式(JSON数组)。每项必填 {platform,name,mode,port,base_path}；条件键 {addr,profile,note}。platform=平台标识(openclaw)；name=显示名；mode=process|container；port=整数端口；base_path=HTTP路径(如/chat)；addr=仅 mode=container 必填(容器自持地址,不要拿主机IP拼), mode=process 禁止出现(地址由 endpoint_ip+port 拼出)；profile=OpenClaw profile名,决定读写 ~/.openclaw-{profile}/,多实例必填,主实例为空串；note=自由注释,不得承载机器需读的信息。示例=[{"platform":"openclaw","name":"OpenClaw #2","mode":"process","port":19789,"base_path":"/chat","profile":"openclaw2"}]',
    `task_slots_limit` int (11) DEFAULT '16' COMMENT '引擎容量：最大并行 agent 实例数',
    `active_task_count` int (11) DEFAULT '0' COMMENT '当前运行中的 agent 实例数',
    `active_status` tinyint (1) DEFAULT '1' COMMENT '准入状态：0-禁入（不参与调度与保活检查）, 1-准入',
    `presence_status` tinyint (1) DEFAULT '0' COMMENT '在线状态：0-离线, 1-在线',
    `last_heartbeat` datetime DEFAULT NULL COMMENT '最近心跳时间（daemon 每 30s 更新；看门狗据此判定引擎失联）',
    `x_desc` longtext COMMENT '语义描述',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `idx_host_id` (`host_id`) USING BTREE,
    KEY `idx_network_host` (`network_id`, `presence_status`) USING BTREE
  ) ENGINE = InnoDB AUTO_INCREMENT = 41 DEFAULT CHARSET = utf8mb4 COMMENT = 'Agent 推理引擎注册表：一行 = 一个可运行 agent 的推理引擎（地址/平台/端口/槽位/心跳）。表名与 host_id 为历史命名，勿按"物理主机"理解 —— 一台机器可登记多个引擎，一个引擎也可不对应独占的机器';

-- ----------------------------
-- Table structure for agx_project
-- ----------------------------
DROP TABLE IF EXISTS `agx_project`;

CREATE TABLE
  `agx_project` (
    `pkid` int (11) NOT NULL AUTO_INCREMENT,
    `network_id` varchar(64) NOT NULL COMMENT '网络编号，用于物理隔离标识',
    `project_id` varchar(64) NOT NULL COMMENT '项目唯一ID',
    `project_name` varchar(128) NOT NULL COMMENT '项目显示名称',
    `project_abbr` varchar(32) DEFAULT NULL COMMENT '项目简称/缩写，用于Agent命名前缀，便于分类识别所属项目',
    `project_logo` varchar(512) DEFAULT NULL COMMENT '项目 Logo（URL 或本地路径，不存二进制）',
    `platform` varchar(32) DEFAULT 'openclaw' COMMENT '项目目标平台类型，如 "openclaw" | "claude_code" | "custom"',
    `x_desc` longtext COMMENT '语义描述',
    `source_dir` varchar(512) DEFAULT NULL COMMENT '项目资源目录，存放项目源码等资源',
    `admin_agent_id` varchar(64) DEFAULT NULL COMMENT '管理员的 Agent ID',
    `active_status` tinyint (1) DEFAULT '1' COMMENT '项目状态：0-停用, 1-启用',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `is_quick` tinyint (1) NOT NULL DEFAULT '0' COMMENT '快捷入口标记 0-否 1-是',
    `quick_order` int NOT NULL DEFAULT '0' COMMENT '快捷入口排序（越小越前）',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `idx_project_id` (`project_id`) USING BTREE,
    KEY `idx_network` (`network_id`) USING BTREE
  ) ENGINE = InnoDB AUTO_INCREMENT = 34 DEFAULT CHARSET = utf8mb4 COMMENT = '项目定义与管理表';

-- ----------------------------
-- Table structure for agx_task_base
-- ----------------------------
DROP TABLE IF EXISTS `agx_task_base`;

CREATE TABLE
  `agx_task_base` (
    `pkid` int (11) NOT NULL AUTO_INCREMENT COMMENT '自动唯一编号',
    `network_id` varchar(64) NOT NULL COMMENT '网络编号，由环境变量或配置文件统一提供，当前表内业务隔离标识',
    `task_name` varchar(128) DEFAULT NULL COMMENT '任务名称',
    `task_labs` varchar(128) DEFAULT NULL COMMENT '任务标签，对应 tasks/ 目录下的子目录名',
    `parent_task_id` varchar(64) DEFAULT NULL COMMENT '父任务ID，与表agx_task_base的字段task_id关联（自引用）',
    `agent_id` varchar(64) NOT NULL COMMENT '指定执行的Agent，与表agx_agent_registry的字段agent_id关联（宿主）',
    `owner_group` varchar(64) DEFAULT NULL COMMENT '宿主条件：非空=仅当宿主agent属于该小组时才具备此task（随小组绑定注入，不走克隆继承）',
    `task_id` varchar(64) NOT NULL COMMENT '任务逻辑ID',
    `description` text COMMENT '任务简洁描述（对外公开的身份名片，区别于 x_desc 的完整说明书）',
    `task_logo` varchar(512) DEFAULT NULL COMMENT '任务 Logo（URL 或本地路径，不存二进制）',
    `x_desc` longtext COMMENT '任务需求与判定标准',
    `target_object` varchar(64) DEFAULT NULL COMMENT '操作对象',
    `payload` json DEFAULT NULL COMMENT '输入参数',
    `priority` tinyint (4) DEFAULT '1' COMMENT '优先级',
    `reply_mode` varchar(20) DEFAULT 'result' COMMENT '回复模式: result=返回结果, confirm=确认执行, silent=静默执行、不回复结果',
    `active_status` tinyint (1) DEFAULT '1' COMMENT '管理：0-禁用, 1-启用',
    `is_private` tinyint (1) DEFAULT '0' COMMENT '可见性：0=公开, 1=私有(仅owner/workshop_manager/本机host可见)',
    `dedicated_session` tinyint (1) DEFAULT '0' COMMENT '独立会话标记(0-无 1-是)，agent:{id}:{task_labs}',
    `task_type` varchar(32) DEFAULT 'normal' COMMENT '任务类型: normal=普通任务, plan=计划任务(默认超时3600s)',
    `session_done_wake` tinyint (1) DEFAULT '1' COMMENT 'session done时是否立即唤醒看门狗: 1=唤醒(执行型task), 0=不唤醒(编排/调度型task)',
    `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `update_dt` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最后更新时间（首次安装=created_at，内容更新=当前时间）',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `idx_task_id` (`task_id`) USING BTREE,
    KEY `idx_parent_task` (`parent_task_id`) USING BTREE
  ) ENGINE = InnoDB AUTO_INCREMENT = 132 DEFAULT CHARSET = utf8mb4 COMMENT = '通用任务定义主表';

-- ----------------------------
-- Table structure for agx_task_execution
-- ----------------------------
DROP TABLE IF EXISTS `agx_task_execution`;

CREATE TABLE
  `agx_task_execution` (
    `pkid` int NOT NULL AUTO_INCREMENT,
    `network_id` varchar(64) NOT NULL COMMENT '网络编号，由环境变量或配置文件统一提供，当前表内业务隔离标识',
    `host_id` varchar(64) NULL COMMENT '执行主机，与表agx_host_inventory的字段host_id关联',
    `project_id` varchar(64) NOT NULL COMMENT '项目ID，与表agx_project的字段project_id关联',
    `plan_id` varchar(64) NULL COMMENT '关联计划 ID，非计划任务为 NULL',
    `activate_plan_id` varchar(64) NULL DEFAULT '' COMMENT '被激活的子计划ID，仅在plan_execute类task时有值，非计划任务为空字符串',
    `agent_id` varchar(64) NOT NULL COMMENT 'Agent编号，与表agx_agent_registry的字段agent_id关联（接收者/执行方）',
    `send_agent_id` varchar(64) NULL COMMENT '任务发送者Agent ID，标识谁分派了本次任务（对应agx_dispatch_task的caller_agent_id）',
    `send_host_id` varchar(64) NULL COMMENT '发送方主机ID，记录分派任务的Agent所在的主机（caller_agent_id对应的host_id）',
    `task_id` varchar(64) NOT NULL COMMENT '任务逻辑ID，与表agx_task_base的字段task_id关联',
    `parent_task_id` varchar(64) NULL COMMENT '父任务ID，关联agx_task_base.task_id，用于记录计划阶段产生的pending任务',
    `task_run_uuid` varchar(64) NOT NULL COMMENT '本次运行逻辑UUID，全局唯一标识',
    `created_at` timestamp NULL COMMENT '创建时间（plan 预建行为 NULL；首次调起/读取时补齐）',
    `dispatched_at` timestamp NULL COMMENT '真正发往 agent 的时刻（daemon 推 WS 队列时写；重发判定基线）',
    `parent_run_uuid` varchar(64) NULL COMMENT '父执行实例ID，自引用agx_task_execution.task_run_uuid，用于子任务追踪',
    `scheduled_at` datetime NULL COMMENT '期待运行时间，看门狗到达该时间后自动激活 pending 任务',
    `prev_task` varchar(64) NULL COMMENT '前置任务执行实例ID，关联agx_task_execution.task_run_uuid',
    `next_task` varchar(64) NULL COMMENT '后置任务执行实例ID，关联agx_task_execution.task_run_uuid',
    `session_id` varchar(128) NULL COMMENT '物理SessionID',
    `send_session_id` varchar(128) NULL COMMENT '调用方当前running execution的session_id，用于Agent间回执路由。取值:1)caller的running execution.session_id(优先dedicated_session) 2)fallback:default_session_id',
    `ws_delivery_status` varchar(20) NULL DEFAULT 'pending' COMMENT 'WS送达状态: pending(待投递)/delivered(已送达)/timeout(超时)/error(异常)',
    `run_status` varchar(20) NULL DEFAULT 'draft' COMMENT '状态流转: draft(未发布) → pending(待激活) → running(执行中) → succeeded/failed/cancelled； 特殊状态: awaiting_confirm(已交付待人放行，needs_confirm=1 时由 agx_finish_task 直接落此态)',
    `started_at` timestamp NULL COMMENT '开始时间，pending状态时为NULL',
    `finished_at` timestamp NULL COMMENT '完成时间',
    `exec_instruction` longtext NULL COMMENT '本次运行部署指令',
    `result_data` longtext NULL COMMENT '输出结果',
    `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `x_desc` longtext NULL COMMENT '执行备注',
    `diag_info` text NULL COMMENT '诊断信息（看门狗快照 / 主动诊断结果，JSON 格式）',
    `summary` longtext NULL COMMENT '执行总结，存储agx_finish_task的summary参数',
    `tokens_consumed` int NULL COMMENT '消耗token',
    `context_window_limit` int NULL COMMENT '上下文限制',
    `input_tokens` int NULL COMMENT '输入消耗token',
    `output_tokens` int NULL COMMENT '输出消耗token',
    `total_tokens` int NULL COMMENT '总消耗token',
    `context_tokens` int NULL COMMENT '当前会话上下文限制',
    `timeout_seconds` int NULL DEFAULT '3600' COMMENT '看门狗超时时间（秒），由 dispatch 方指定，为空则默认1800。当前任务执行超时的时间，这里监控的tmux任务执行的时间。这个值不同Agent平台处理模式不一样,openclaw命令只发出不等待。CC的命令会等待执行完成。具体设置什么只需根据不同平台的特点考虑',
    `continue_count` int NOT NULL DEFAULT '0' COMMENT '续接次数，每次continue_task +1',
    `needs_reply` tinyint (1) NULL DEFAULT '0' COMMENT '回执需求：0-不需要 1-需要回复 2-回复中 3-已回复',
    `is_reply_to` varchar(64) NULL COMMENT '此消息是回执，指向原消息的task_run_uuid',
    `reply_sent_at` datetime NULL COMMENT '签条(回执)发出时刻：MCP dispatch 入队成功时写，仅 is_reply_to 非空的行有值',
    `reply_read` tinyint (1) NOT NULL DEFAULT '0' COMMENT '回执签收状态：0-未签收(默认) 1-已签收。★仅对回执行(is_reply_to非空)有意义；签收由 agx_ack_receipt 写入',
    `reply_read_at` datetime NULL COMMENT '回执签收时间：NULL=未签收。★与 reply_read 同写，供统计「回执多久才被读」',
    `reply_read_last_at` datetime NULL COMMENT '最近一次签收时刻：agx_ack_receipt 每次签收刷新；reply_read_at 只保留首次签收时间（供统计回执多久才被读）',
    `reply_delivered_at` datetime NULL COMMENT '签条投递确认时刻：daemon 收到 ws_client delivered 结果时写，仅回执行有值',
    `needs_confirm` tinyint (1) NULL DEFAULT '0' COMMENT '是否需人类确认：0-不需要(默认) 1-待放行(成功不落 succeeded，落 awaiting_confirm) 2-已放行 3-已驳回',
    `exec_name` varchar(255) NULL COMMENT '★执行实例短标题: 一句话说明"这一次"在做什么(实例级, 区别于 task_base.description 的任务类型级描述)',
    `quick_cmd_id` varchar(64) NULL COMMENT '本 execution 由哪条快捷指令派发(cmd_id)；空=非快捷指令触发',
    `quick_cmd_params` text NULL COMMENT '本次派发用的快捷指令参数(JSON, 补全 default 后的真值)；空=非快捷指令触发',
    `client_request_id` varchar(64) NULL,
    `alert_level` tinyint NOT NULL DEFAULT '0' COMMENT '提醒等级：0无 1唤醒中 2已告警 3已升级人类 9已收口',
    `alert_count` int NOT NULL DEFAULT '0' COMMENT '已发告警次数（看门狗写入，替代 Redis alert_count）',
    `wake_count` int NOT NULL DEFAULT '0' COMMENT '已发唤醒次数（看门狗写入，按 70/80/90 百分比档位，每档一次）',
    `alerted_at` datetime NULL COMMENT '最近一次提醒(唤醒或告警)发出时刻，兼作告警宽限期基准',
    `alert_reason` varchar(255) NULL COMMENT '最近一次提醒原因（如：超时（已运行3600s，限制1800s））',
    `alert_target` varchar(64) NULL COMMENT '最近一次提醒发给了哪个 agent（写明收件人，便于溯源）',
    `alert_note` text NULL COMMENT '发起方填写的影响总结/处置结论，替代原 alert execution.summary',
    `ack_state` varchar(16) NOT NULL DEFAULT 'none' COMMENT '签收聚合状态：none/sent/delivered/read/failed（由三时间戳派生，供筛选）',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `idx_task_run_uuid` (`task_run_uuid`) USING BTREE,
    UNIQUE KEY `uk_client_req` (`send_agent_id`, `client_request_id`) USING BTREE,
    KEY `idx_task_host` (`task_id`, `host_id`) USING BTREE,
    KEY `idx_session_match` (`task_run_uuid`, `session_id`) USING BTREE,
    KEY `idx_parent_run` (`parent_run_uuid`) USING BTREE,
    KEY `idx_run_status` (`run_status`) USING BTREE,
    KEY `idx_parent_status` (`parent_run_uuid`, `run_status`) USING BTREE,
    KEY `idx_plan_status` (`plan_id`, `run_status`) USING BTREE,
    KEY `idx_exec_name` (`exec_name`) USING BTREE,
    KEY `idx_alert_level` (`alert_level`, `alerted_at`) USING BTREE
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '任务执行实例追踪表';

-- ----------------------------
-- Table structure for agx_task_execution_event
-- ----------------------------
DROP TABLE IF EXISTS `agx_task_execution_event`;

CREATE TABLE
  IF NOT EXISTS `agx_task_execution_event` (
    `event_id` varchar(64) NOT NULL COMMENT '事件ID evt_xxx',
    `task_run_uuid` varchar(64) NOT NULL COMMENT '被关注的任务',
    `event_type` varchar(24) NOT NULL COMMENT 'dispatch/receipt/wake/alert/escalate/ack/revived',
    `level` tinyint NOT NULL DEFAULT 0 COMMENT '0提示 1警告 2严重',
    `from_agent` varchar(64) DEFAULT NULL COMMENT '发出方（看门狗=agx_core）',
    `to_agent` varchar(64) DEFAULT NULL COMMENT '接收方',
    `to_session` varchar(128) DEFAULT NULL COMMENT '发进哪个会话',
    `channel` varchar(16) DEFAULT NULL COMMENT 'queue/ws/tmux',
    `reason` varchar(255) DEFAULT NULL COMMENT '本次事件原因',
    `payload` text COMMENT '原始信封 JSON',
    `delivery_state` varchar(16) NOT NULL DEFAULT 'pending' COMMENT 'pending/sent/delivered/failed',
    `delivered_at` datetime DEFAULT NULL COMMENT '投递确认时刻',
    `ack_state` varchar(16) NOT NULL DEFAULT 'none' COMMENT 'none/read',
    `ack_at` datetime DEFAULT NULL COMMENT '签收时刻',
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '事件发生时刻',
    PRIMARY KEY (`event_id`),
    KEY `idx_run` (`task_run_uuid`, `created_at`),
    KEY `idx_type` (`event_type`, `created_at`)
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '任务事件流：唤醒/告警/回执/签收逐条留痕';

-- ----------------------------
-- Table structure for agx_session
-- ----------------------------
DROP TABLE IF EXISTS `agx_session`;

CREATE TABLE
  `agx_session` (
    `pkid` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `network_id` varchar(64) NOT NULL COMMENT '所属网络, 业务隔离标识',
    `session_id` varchar(128) NOT NULL COMMENT '会话寻址 ID (= sessions.send 的 key / agx_task_execution.session_id), 全局唯一',
    `session_name` varchar(255) NOT NULL COMMENT '★核心: 会话名字, 说明这个会话是做什么用的',
    `x_desc` text NULL COMMENT '会话说明/备注(可选, 更详细的用途描述)',
    `agent_id` varchar(64) NOT NULL COMMENT '会话归属 Agent',
    `project_id` varchar(64) NULL COMMENT '所属项目',
    `host_id` varchar(64) NULL COMMENT '会话实体所在主机：该 session_id 已在这台 host 的 gateway 物化；NULL=历史未回填。漂移时须先在新 host 建会话成功才改写此列',
    `task_labs` varchar(64) NULL COMMENT '任务族(若由任务会话而来)',
    `session_type` varchar(32) NOT NULL DEFAULT 'oneshot' COMMENT '会话类型: main-主会话(长久对话通道) / case-事项(长期会话,人为声明的长期事项) / task-任务会话(挂任务族的稳定会话) / oneshot-一次性临时会话',
    `is_assistant` tinyint (1) NOT NULL DEFAULT '0' COMMENT '是否助理会话: 1-是(case 常驻助理, 每次执行注入身份提示) / 0-否(普通会话)',
    `assistant_slug` varchar(64) NULL COMMENT '助理命名标识(slug): case 会话从 session_id 尾段 asst_{slug} 派生, 如 ui_design/pm_ops/docs/agx_maint; 非助理会话为 NULL。与 is_assistant 并存(此列管命名, 彼列管程序判定)',
    `avatar` varchar(512) NULL COMMENT '助理头像（URL 或本地路径，不存二进制）',
    `creator_agent_id` varchar(64) NULL COMMENT '创建者(谁第一次发起该会话)',
    `session_model` varchar(64) NULL COMMENT '★会话级模型: 该会话当前使用的模型(如 deepseek-v4-flash)。模型跟会话走不跟 agent 走(产品负责人 2026-10-15 定, 取代 agx_agent_registry.base_model)',
    `model_provider` varchar(32) NULL COMMENT '模型 provider(如 deepseek/openai/anthropic), 与 session_model 配对',
    `model_source` varchar(16) NULL COMMENT '模型来源: user-用户显式覆盖(sessions.patch) / auto-自动回填 / default-框架默认回落',
    `run_count` int NOT NULL DEFAULT '1' COMMENT '承载过的执行次数',
    `last_run_uuid` varchar(64) NULL COMMENT '最近一次执行的 task_run_uuid',
    `last_active_at` datetime NULL COMMENT '最近活跃时间',
    `active_status` tinyint (1) NOT NULL DEFAULT '1' COMMENT '生效状态: 1-有效 0-归档',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `is_quick` tinyint (1) NOT NULL DEFAULT '0' COMMENT '快捷入口标记 0-否 1-是',
    `quick_order` int NOT NULL DEFAULT '0' COMMENT '快捷入口排序（越小越前）',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `uk_session_id` (`session_id`) USING BTREE,
    KEY `idx_agent` (`agent_id`, `active_status`) USING BTREE,
    KEY `idx_project` (`project_id`) USING BTREE,
    KEY `idx_last_active` (`last_active_at`) USING BTREE,
    KEY `idx_type` (`session_type`, `active_status`) USING BTREE,
    KEY `idx_assistant` (`is_assistant`, `active_status`) USING BTREE,
    KEY `idx_assistant_slug` (`assistant_slug`) USING BTREE,
    KEY `idx_session_model` (`session_model`) USING BTREE
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'AgentGeeX 会话表——会话的身份证, 核心是名字(用途)';

-- ----------------------------
-- Table structure for agx_execution_link
-- ----------------------------
-- execution 引用关系表: 一条原始 execution 可被多个 session 引用(多路复用), 只加边不搬移。
-- 设计依据(2026-10-05 与产品负责人确认):
--   · 挂的是【指针】不是搬家 —— 原始 execution 一行不动(session_id/summary 永不修改)
--   · 粒度 = execution 级(不是 session 级) —— 精确到"哪一次执行", 可挑着挂
--   · 一条 execution 可挂 N 个 session —— 同一份历史被多个上下文复用
--   · 可回溯 —— linked_at/linked_by/reason 留下"何时挂、谁挂、为何挂"
DROP TABLE IF EXISTS `agx_execution_link`;

CREATE TABLE
  `agx_execution_link` (
    `pkid` bigint (20) NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `execution_id` varchar(64) NOT NULL COMMENT '被引用的原始 execution (task_run_uuid, 原地不动永不修改)',
    `case_session_id` varchar(128) NOT NULL COMMENT '引用它的目标 session (case 事项会话或任意 session)',
    `link_role` varchar(32) NOT NULL DEFAULT 'reference' COMMENT '引用角色: reference-普通引用 / source-发起来源 / merged-被合并',
    `linked_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '何时挂靠',
    `linked_by` varchar(64) NOT NULL DEFAULT '' COMMENT '谁挂的 (agent_id / system)',
    `reason` varchar(512) NOT NULL DEFAULT '' COMMENT '为什么挂 (归类/复用理由)',
    `active_status` tinyint (1) NOT NULL DEFAULT 1 COMMENT '生效状态: 1-有效 0-已解除引用',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `uk_exec_case` (`execution_id`, `case_session_id`) USING BTREE,
    KEY `idx_case` (`case_session_id`, `active_status`) USING BTREE,
    KEY `idx_exec` (`execution_id`, `active_status`) USING BTREE
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'execution 引用关系表: 一条原始 execution 可被多个 session 引用(多路复用), 只加边不搬移';

-- ----------------------------
-- Table structure for agx_plan
-- ----------------------------
DROP TABLE IF EXISTS `agx_plan`;

CREATE TABLE
  `agx_plan` (
    `pkid` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `plan_id` varchar(64) NOT NULL COMMENT '计划唯一 ID，plan_ 前缀',
    `parent_plan_id` varchar(64) NULL COMMENT 'parent plan id',
    `project_id` varchar(64) NOT NULL COMMENT '所属项目',
    `created_by` varchar(64) NOT NULL COMMENT '创建者 agent_id',
    `host_id` varchar(64) NULL COMMENT '计划归属主机：谁创建谁负责（看门狗按此做计划隔离）',
    `task_run_uuid` varchar(64) NULL COMMENT '激活该 plan 的 execution 的 task_run_uuid。谁激活，谁负责到底。非 activate_plan_id 模式填 NULL',
    `description` text NULL COMMENT '人类可读描述',
    `git_branch` varchar(128) NULL COMMENT '本次计划使用的 Git 分支',
    `plan_status` varchar(16) NOT NULL DEFAULT 'draft' COMMENT 'draft(未激活) / activated(已激活,步骤放行中) / cancelling(取消中) / cancelled(已取消) / completed(全部步骤收口) / archived(归档,历史遗留) / failed(预留,当前无写入路径)',
    `steps_json` longtext NULL COMMENT '原始 JSON plan（完整保留）',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `completed_at` datetime NULL COMMENT '完成时间',
    `notified_at` datetime NULL DEFAULT NULL COMMENT 'plan_completed 已通知时间(去重真相源; NULL=未通知). Redis 的 agx:notified:* 只是快路径',
    `activate_mode` varchar(16) NOT NULL DEFAULT 'watchdog' COMMENT '激活方式: chain(链式) / watchdog(看门狗)',
    `start_task_run_uuid` varchar(64) NULL COMMENT '链式启动的入口 execution',
    `is_quick` tinyint (1) NOT NULL DEFAULT '0' COMMENT '快捷入口标记 0-否 1-是',
    `quick_order` int NOT NULL DEFAULT '0' COMMENT '快捷入口排序（越小越前）',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `uk_plan_id` (`plan_id`) USING BTREE,
    KEY `idx_project` (`project_id`) USING BTREE,
    KEY `idx_created_by` (`created_by`) USING BTREE
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '任务计划管理表';

-- ----------------------------
-- Table structure for agx_skill
-- ----------------------------
-- 技能共享表: Agent 把工作中研究的可复用技能(怎么做)沉淀于此, 全员公开可查。
-- 设计依据(2026-10-05/2026-10-06 与产品负责人确认):
--   · 纪律一 与公告边界切死 —— 技能 = 可复用有操作性的"怎么做"; "我干了啥"的流水一律不许进
--     (判据: 读了能照做 → 技能库; 读了只知道"谁干了啥" → 公告 agx_agent_notice)
--   · 纪律二 不人工审用信号筛 —— 开放写入(任何 Agent 可贡献) ≠ 无门槛沉淀,
--     靠 use_count/helpful_count 让好技能凭信号浮上来, 不设人工审核流
--   · 纪律三 必带低摩擦 MCP 工具面 —— 只建表没用(Agent 不会去 SQL 查),
--     工具面 agx_skill_contribute / agx_skill_query / agx_skill_get / agx_skill_feedback 与表同批上线
DROP TABLE IF EXISTS `agx_skill`;

CREATE TABLE
  `agx_skill` (
    `pkid` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `skill_id` varchar(64) NOT NULL COMMENT '技能唯一 ID (skill_ 前缀)',
    `network_id` varchar(64) NOT NULL DEFAULT '' COMMENT '所属网络(公开性随网络), 空=全局',
    `skill_name` varchar(256) NOT NULL COMMENT '技能名 (如 "playwright 无头浏览器")',
    `category` varchar(64) NOT NULL DEFAULT 'other' COMMENT '分类: browser / github / huggingface / search / cli / mcp / other (自由文本, 不硬约束)',
    `description` varchar(1024) NOT NULL DEFAULT '' COMMENT '一句话说明: 这技能解决什么问题',
    `content` longtext NOT NULL COMMENT '技能正文: 命令/用法/踩坑/示例 (可复用可照做)',
    `tags` varchar(512) NOT NULL DEFAULT '' COMMENT '标签(逗号分隔自由文本), 便于检索',
    `source_agent_id` varchar(64) NOT NULL DEFAULT '' COMMENT '贡献者 agent_id',
    `use_count` int NOT NULL DEFAULT '0' COMMENT '被取用次数(信号: 读单条 agx_skill_get 时累加)',
    `helpful_count` int NOT NULL DEFAULT '0' COMMENT '有用票数(信号: 同侪 agx_skill_feedback 认可)',
    `active_status` tinyint (1) NOT NULL DEFAULT '1' COMMENT '生效状态: 1-有效 0-已归档',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `appkey` varchar(128) NULL COMMENT '服务器需要的appkey',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `uk_skill_id` (`skill_id`) USING BTREE,
    KEY `idx_name` (`skill_name`) USING BTREE,
    KEY `idx_category` (`category`, `active_status`) USING BTREE,
    KEY `idx_source` (`source_agent_id`) USING BTREE,
    KEY `idx_status` (`active_status`, `updated_at`) USING BTREE
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '技能共享表: Agent 沉淀可复用技能(怎么做), 全员公开可查, 靠信号浮优';

-- ----------------------------
-- Table structure for agx_radar
-- 技术雷达: 一个 Agent 研究过的技术条目 + 过程 + 结论 (产品负责人 2026-10-21 定: MCP 不做任何绑定)
-- ----------------------------
DROP TABLE IF EXISTS `agx_radar`;

CREATE TABLE
  `agx_radar` (
    `pkid` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `radar_id` varchar(64) NOT NULL COMMENT '条目 ID (radar_ 前缀)',
    `topic_id` varchar(64) NOT NULL DEFAULT '' COMMENT '归属专题 → agx_radar_topic.topic_id；空=未归属',
    `project_id` varchar(64) NOT NULL DEFAULT '' COMMENT '归属项目(仅归类, 不做权限绑定)',
    `network_id` varchar(64) NOT NULL DEFAULT '' COMMENT '归属网络',
    `title` varchar(512) NOT NULL COMMENT '技术名/项目名(第一眼看的那个)',
    `object_type` varchar(16) NOT NULL COMMENT '★对象类型: repo-仓库/product-产品/article-文章/topic-专题/concept-概念/model-模型/dataset-数据集/other-其他',
    `category` varchar(64) NOT NULL DEFAULT 'other' COMMENT '赛道: agent/llm/t2i/skill/protocol/devtool/infra/paper/model/other',
    `ring` varchar(16) NOT NULL DEFAULT '' COMMENT '★技术雷达四环: adopt-采用/trial-试用/assess-评估/hold-暂缓, 空=尚未评级',
    `research_status` varchar(16) NOT NULL DEFAULT 'done' COMMENT '★研究进度: pending-已登记待研究/done-已完成/failed-未跑通',
    `dedup_key` varchar(512) NOT NULL COMMENT '★去重键: gh:owner/repo | wx:<文章ID> | arxiv:xxxx.xxxxx | url:<sha1> | kw:<关键词>',
    `source_type` varchar(32) NOT NULL DEFAULT 'other' COMMENT '【已废弃·改由 agx_radar_doc.source_type】旧单来源字段, 仅存量兼容',
    `source_url` varchar(1024) NOT NULL DEFAULT '' COMMENT '【已废弃·改由 agx_radar_doc.source_url】旧单来源字段, 仅存量兼容',
    `source_meta` varchar(1024) NOT NULL DEFAULT '' COMMENT '【已废弃·改由 agx_radar_doc.source_meta】旧单来源字段, 仅存量兼容',
    `asked_by` varchar(64) NOT NULL DEFAULT '' COMMENT '★谁点的题: owner(负责人) / aifr_pm(我自己发现) / agent_xxx',
    `summary` varchar(1024) NOT NULL DEFAULT '' COMMENT '一句话: 这是什么',
    `findings` mediumtext NULL COMMENT '★关键发现(事实层): 一行一条, 星数/License/实测数据/API 返回',
    `conclusion` mediumtext NULL COMMENT '★我的结论(判断层): 行不行、值不值得、什么条件下值得',
    `limits` mediumtext NULL COMMENT '★限制与坑(最值钱): 已知缺陷/前置条件/踩过的雷',
    `maturity` varchar(16) NOT NULL DEFAULT 'unknown' COMMENT '成熟度: stable/beta/experimental/abandoned/unknown',
    `evidence_level` varchar(16) NOT NULL DEFAULT 'read_only' COMMENT '★证据强度: hands_on-亲手跑过/api_verified-查到一手数据/read_only-只读二手资料',
    `process` mediumtext NULL COMMENT '研究过程摘要: 查了什么、比了什么、跑了什么(便于日后复核)',
    `artifacts` text NULL COMMENT '【已废弃·改由 agx_radar_doc + agx_radar_link(role=artifact)】旧产物路径串, 仅存量兼容',
    `researched_by` varchar(64) NOT NULL DEFAULT '' COMMENT '★哪个助理做的',
    `next_action` varchar(1024) NOT NULL DEFAULT '' COMMENT '下一步动作（纯文本：接下来要做什么/等什么/找谁要）',
    `owner_agent` varchar(64) NOT NULL DEFAULT '' COMMENT '跟进负责人 agent_id（仅字符安全校验，不校验存在性）',
    `run_uuid` varchar(64) NOT NULL DEFAULT '' COMMENT '对应 agx_task_execution 执行编号',
    `tags` varchar(512) NOT NULL DEFAULT '' COMMENT '标签, 逗号分隔',
    `search_text` mediumtext NULL COMMENT '★检索汇总: 程序自动拼接 title+summary+findings+conclusion+limits+tags+source_meta, 供全文索引',
    `first_studied_at` datetime NULL COMMENT '首次研究时间',
    `last_checked_at` datetime NULL COMMENT '最后一次复核时间',
    `check_count` int NOT NULL DEFAULT '0' COMMENT '复核次数',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `active_status` tinyint (1) NOT NULL DEFAULT '1' COMMENT '1-有效 0-归档',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `uk_radar_id` (`radar_id`) USING BTREE,
    UNIQUE KEY `uk_dedup` (`project_id`, `dedup_key`) USING BTREE,
    KEY `idx_ring` (`ring`, `active_status`) USING BTREE,
    KEY `idx_cat_ring` (`category`, `ring`) USING BTREE,
    KEY `idx_asked` (`asked_by`, `first_studied_at`) USING BTREE,
    KEY `idx_status` (`research_status`, `asked_by`) USING BTREE,
    KEY `idx_agent` (`researched_by`) USING BTREE,
    KEY `idx_time` (`first_studied_at`) USING BTREE,
    KEY `idx_objtype` (`object_type`, `research_status`, `active_status`) USING BTREE,
    KEY `idx_topic` (`topic_id`, `active_status`) USING BTREE,
    FULLTEXT KEY `ft_search` (`title`, `search_text`),
    FULLTEXT KEY `ft_full` (
      `title`,
      `summary`,
      `findings`,
      `conclusion`,
      `limits`,
      `tags`,
      `source_meta`
    )
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '技术雷达: 一个 Agent 研究过的技术条目 + 过程 + 结论, 供检索与分级';


-- ----------------------------
-- Table structure for agx_release
-- 研究成果对外发布池 (2026-10-23 产品负责人定: aifr_pm 维护研究过程, 成果经此表对外只读发布)
-- 一行 = 一版; 只增不改; 对外只出 visibility='network' AND active_status=1 的最新版
-- 消费方: agx_mcp/mcp_server_release.py (server 名 aifr-research)
-- ----------------------------
DROP TABLE IF EXISTS `agx_release`;

CREATE TABLE
  `agx_release` (
    `pkid` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `release_id` varchar(32) NOT NULL COMMENT '发布实体ID(rel_xxx)；一个slug可有多版',
    `slug` varchar(96) NOT NULL COMMENT '对外稳定主键，永不变；引用锚点',
    `version` int NOT NULL DEFAULT '1' COMMENT '版本号；改结论则+1',
    `title` varchar(255) NOT NULL COMMENT '成果标题',
    `claim` varchar(1000) NOT NULL COMMENT '一句话结论(判断句)',
    `kind` varchar(16) NOT NULL DEFAULT 'capability' COMMENT 'capability/selection/probe/survey/caution',
    `category` varchar(16) NOT NULL DEFAULT 'other' COMMENT '赛道',
    `verdict` varchar(16) NOT NULL DEFAULT 'assess' COMMENT 'adopt/trial/assess/avoid(对外语义)',
    `evidence_level` varchar(16) NOT NULL DEFAULT 'read_only' COMMENT 'hands_on/api_verified/read_only',
    `applies_when` varchar(1000) NOT NULL COMMENT '适用条件(必填)',
    `not_applies_when` varchar(1000) NOT NULL COMMENT '不适用条件(必填，最值钱)',
    `details` mediumtext NULL COMMENT '展开说明：结论+边界+关键数据要点；不含完整步骤/源码',
    `refs` json NULL COMMENT '可公开引用[{"title","url"}]；禁本地路径',
    `tags` varchar(255) NOT NULL DEFAULT '' COMMENT '检索标签，逗号分隔',
    `content_hash` char(40) NOT NULL COMMENT '规范化正文sha1，供引用锚定',
    `source_radar_id` varchar(32) NULL COMMENT '内部溯源；对外永不返回',
    `topic_id` varchar(64) NOT NULL DEFAULT '' COMMENT '自 source_radar_id 推导的专题，供外人按领域捞成果',
    `project_id` varchar(40) NOT NULL COMMENT '归属项目',
    `network_id` varchar(40) NOT NULL DEFAULT '' COMMENT '归属网络',
    `visibility` varchar(16) NOT NULL DEFAULT 'network' COMMENT 'network/project/private',
    `published_by` varchar(40) NOT NULL DEFAULT '' COMMENT '发布人',
    `published_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次发布时间(本版)',
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `supersedes` varchar(32) NULL COMMENT '上一版release_id',
    `active_status` tinyint NOT NULL DEFAULT '1' COMMENT '1=有效；0=下线(仅限结论错了)',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `uk_release_id` (`release_id`) USING BTREE,
    UNIQUE KEY `uk_slug_version` (`slug`, `version`) USING BTREE,
    KEY `idx_verdict_kind` (`verdict`, `kind`, `active_status`) USING BTREE,
    KEY `idx_category` (`category`, `verdict`) USING BTREE,
    KEY `idx_updated` (`updated_at`) USING BTREE,
    KEY `idx_vis` (`visibility`, `active_status`, `updated_at`) USING BTREE,
    KEY `idx_topic` (`topic_id`, `active_status`) USING BTREE,
    FULLTEXT KEY `ft_rel` (`title`, `claim`, `details`, `tags`)
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '研究成果对外发布池：一行=一版；只增不改；对外只出network+active最新版';

-- ── 快捷指令表（2026-10-26 补入 truth-source：此前仅存在于线上库）
CREATE TABLE
  `agx_quick_cmd` (
    `pkid` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `cmd_id` varchar(64) NOT NULL COMMENT '快捷指令逻辑ID',
    `network_id` varchar(64) NOT NULL COMMENT '网络编号',
    `cmd_name` varchar(128) NOT NULL COMMENT '指令名（如 创建Agent）',
    `cmd_desc` varchar(512) DEFAULT NULL COMMENT '一句话说明（UI提示）',
    `category` varchar(32) DEFAULT 'other' COMMENT '分类: agent/project/log/...',
    `target_agent_id` varchar(64) DEFAULT NULL COMMENT '【私有专用】固定发给谁。is_public=1 时必须为 NULL',
    `is_public` tinyint (1) NOT NULL DEFAULT '0' COMMENT '0=私有(必须绑 target_agent_id，发给指定Agent) 1=公有(不绑agent，由调用方自己执行 task_labs)',
    `task_labs` varchar(64) DEFAULT NULL COMMENT '走哪个task（存 task_labs 稳定名，非 task_id UUID）。★ 注意：此值与 cmd_id 未必同名（如 system_usage_analysis→network_analysis），对照表见 docs/agent_docs/2026-10-14-快捷指令-cmd_id与task_labs映射表.md',
    `run_session_id` varchar(128) DEFAULT NULL COMMENT '执行落点会话，助理也是会话（如 agent:x:case_agx_dev）；空=默认主会话',
    `template` longtext COMMENT '报文模板，含{占位符}',
    `params_schema` json DEFAULT NULL COMMENT '参数定义（JSON数组）',
    `active_status` tinyint (1) DEFAULT '1' COMMENT '启禁用 0-停用 1-启用',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `update_dt` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    `send_mode` varchar(8) NOT NULL DEFAULT 'task' COMMENT '发送方式：task=带参数派任务(原快捷指令) / text=纯文本片段(直接填入输入框)',
    `sort_order` int NOT NULL DEFAULT '100' COMMENT '排序权重，数值小的在前',
    `use_count` int NOT NULL DEFAULT '0' COMMENT '累计使用次数',
    `last_used_at` datetime DEFAULT NULL COMMENT '最后使用时间',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `idx_cmd_id` (`cmd_id`) USING BTREE,
    KEY `idx_category` (`category`) USING BTREE,
    KEY `idx_target_agent` (`target_agent_id`) USING BTREE,
    CONSTRAINT `chk_qc_private_has_agent` CHECK (
      (
        (`is_public` = 1)
        or (`target_agent_id` is not null)
      )
    ),
    CONSTRAINT `chk_qc_public_no_agent` CHECK (
      (
        (`is_public` = 0)
        or (`target_agent_id` is null)
      )
    )
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '快捷指令表（人机交互·选择式操作固件）';

SET
  FOREIGN_KEY_CHECKS = 1;

-- ----------------------------
-- 2026-11-02 补录：库中存在但 sql 缺失的 3 张 radar 表（以库为准）
-- ----------------------------
-- ----------------------------
-- Table structure for agx_radar_topic
-- ----------------------------
DROP TABLE IF EXISTS `agx_radar_topic`;

CREATE TABLE
  `agx_radar_topic` (
    `pkid` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `topic_id` varchar(64) NOT NULL COMMENT '专题ID(tp_ 前缀)',
    `project_id` varchar(64) NOT NULL DEFAULT '' COMMENT '归属项目(仅归类)',
    `network_id` varchar(64) NOT NULL DEFAULT '' COMMENT '归属网络',
    `topic_key` varchar(128) NOT NULL COMMENT '稳定键: tp:jev | tp:t2i | tp:a320（对外引用用它，不用 topic_id）',
    `title` varchar(255) NOT NULL COMMENT '专题名(第一眼看)',
    `summary` varchar(1024) NOT NULL DEFAULT '' COMMENT '一句话: 这个专题要解决什么',
    `owner_agent_id` varchar(64) NOT NULL DEFAULT '' COMMENT '负责这个专题的助理 slug(如 jev_research)',
    `status` varchar(16) NOT NULL DEFAULT 'active' COMMENT '专题进度: active-在跑/paused-暂停/done-已收尾/abandoned-放弃',
    `sort_order` int NOT NULL DEFAULT '100' COMMENT '展示排序(小的在前)',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `active_status` tinyint (1) NOT NULL DEFAULT '1' COMMENT '1-有效 0-归档',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `uk_topic_id` (`topic_id`) USING BTREE,
    UNIQUE KEY `uk_topic_key` (`project_id`, `topic_key`) USING BTREE,
    KEY `idx_status` (`status`, `active_status`) USING BTREE,
    KEY `idx_owner` (`owner_agent_id`) USING BTREE,
    KEY `idx_sort` (`sort_order`) USING BTREE
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '研究专题（课题容器）：专题1→对象N；只做分组不存研究内容';

-- ----------------------------
-- Table structure for agx_radar_doc
-- ----------------------------
DROP TABLE IF EXISTS `agx_radar_doc`;

CREATE TABLE
  `agx_radar_doc` (
    `pkid` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `doc_id` varchar(64) NOT NULL COMMENT '文档 ID(doc_ 前缀)',
    `topic_id` varchar(64) NOT NULL DEFAULT '' COMMENT '主专题(快捷索引)→ agx_radar_topic.topic_id；跨专题文档以 link 为准',
    `project_id` varchar(64) NOT NULL DEFAULT '' COMMENT '归属项目(仅归类, 不做权限绑定)',
    `network_id` varchar(64) NOT NULL DEFAULT '' COMMENT '归属网络',
    `dedup_key` varchar(512) NOT NULL COMMENT '★去重键: wx:<文章ID> | url:<sha1> | file:<sha1> | gh:owner/repo',
    `kind` varchar(24) NOT NULL DEFAULT 'note' COMMENT 'article-外部文章/report-报告/deep-深度研究/note-笔记/script-脚本/data-数据集/paper-论文/video-视频/readme-仓库说明',
    `title` varchar(512) NOT NULL COMMENT '文档标题(第一眼看)',
    `source_type` varchar(32) NOT NULL DEFAULT 'local' COMMENT 'wx_article/github/web/arxiv/video/local',
    `source_url` varchar(1024) NOT NULL DEFAULT '' COMMENT '原始地址',
    `source_meta` varchar(1024) NOT NULL DEFAULT '' COMMENT '一手元信息: 作者/公众号/发布时间/星数/License',
    `file_path` varchar(1024) NOT NULL DEFAULT '' COMMENT '相对 workspace 的路径',
    `file_sha1` char(40) NOT NULL DEFAULT '' COMMENT '★文件内容 SHA1(可复算/可查文档漂移)',
    `file_bytes` bigint NOT NULL DEFAULT '0' COMMENT '文件字节数',
    `doc_date` date NULL COMMENT '★文档自己说的时间(发布/成文), 非文件 mtime',
    `collected_at` datetime NULL COMMENT '★我拿到它的时间(与 doc_date 分开, 不许混)',
    `summary` varchar(1024) NOT NULL DEFAULT '' COMMENT '这份文档说了什么(一句话)',
    `findings` mediumtext NULL COMMENT '★文档内事实层: 一行一条',
    `active_status` tinyint (1) NOT NULL DEFAULT '1' COMMENT '1-有效 0-归档',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `uk_doc_id` (`doc_id`) USING BTREE,
    UNIQUE KEY `uk_dedup` (`project_id`, `dedup_key`) USING BTREE,
    KEY `idx_kind_date` (`kind`, `doc_date`) USING BTREE,
    KEY `idx_path` (`file_path` (191)) USING BTREE,
    KEY `idx_sha1` (`file_sha1`) USING BTREE,
    KEY `idx_active` (`active_status`) USING BTREE,
    KEY `idx_topic` (`topic_id`, `active_status`) USING BTREE,
    FULLTEXT KEY `ft_doc` (`title`, `summary`, `findings`, `source_meta`)
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '雷达-文档/产物(原子资料单位: 一篇文章/一份报告/一篇深研/一个脚本 = 一行)';

-- ----------------------------
-- Table structure for agx_radar_link
-- ----------------------------
DROP TABLE IF EXISTS `agx_radar_link`;

CREATE TABLE
  `agx_radar_link` (
    `pkid` bigint NOT NULL AUTO_INCREMENT,
    `link_id` varchar(64) NOT NULL COMMENT '关系 ID(link_ 前缀)',
    `doc_id` varchar(64) NOT NULL COMMENT '文档 ID → agx_radar_doc.doc_id',
    `radar_id` varchar(64) NOT NULL COMMENT '对象 ID → agx_radar.radar_id',
    `topic_id` varchar(64) NOT NULL DEFAULT '' COMMENT '冗余自对象的专题，供直接按专题捞关系',
    `role` varchar(16) NOT NULL COMMENT '★证据角色(必填): source-该对象来源 / deep-对该对象的深研 / probe-实测记录 / artifact-产物(脚本数据图) / mention-仅提及',
    `note` varchar(512) NOT NULL DEFAULT '' COMMENT '这份文档在这个对象里具体干了啥',
    `active_status` tinyint (1) NOT NULL DEFAULT '1' COMMENT '1-有效 0-归档',
    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `uk_link` (`doc_id`, `radar_id`, `role`) USING BTREE,
    KEY `idx_radar` (`radar_id`, `role`) USING BTREE,
    KEY `idx_doc` (`doc_id`) USING BTREE,
    KEY `idx_role` (`role`, `active_status`) USING BTREE,
    KEY `idx_topic` (`topic_id`, `active_status`) USING BTREE
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '雷达-文档与对象的关系(多对多): 一篇文档可支撑 N 个对象, 一个对象可来自 N 篇文档';

-- ----------------------------
-- Table structure for agx_user_message
-- 一问一答全量归档：一行 = 一条 owner 消息(content) + 它的回答(reply_content)
-- 只增不删，采集器 tools/user_msg_collect.py 幂等写入；replies 由回放会话补齐
-- 刻意用 IF NOT EXISTS 而非 DROP，因为这是业务数据，误执行 dump 不能清空
-- ----------------------------
CREATE TABLE
  IF NOT EXISTS `agx_user_message` (
    `pkid` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `msg_uid` varchar(128) NOT NULL COMMENT '全局唯一: <host_id>:<event_id>, 幂等去重键',
    `host_id` varchar(64) NOT NULL COMMENT '来源主机 ID (agx host_id)',
    `platform` varchar(32) NOT NULL DEFAULT 'openclaw' COMMENT '来源平台: openclaw/claude_code/custom',
    `agent_id` varchar(64) NOT NULL COMMENT '所属 Agent',
    `session_id` varchar(64) NOT NULL COMMENT 'gateway 会话 ID',
    `session_key` varchar(160) NOT NULL DEFAULT '' COMMENT '会话寻址键 agent:<id>:<name>',
    `session_label` varchar(64) NOT NULL DEFAULT '' COMMENT '会话短名(label/display_name)',
    `role` varchar(16) NOT NULL DEFAULT 'user' COMMENT '消息角色: user/assistant',
    `msg_kind` varchar(16) NOT NULL DEFAULT 'human' COMMENT '消息类型: human=真人 / envelope=AgentGeeX派发信封 / notice=系统通知告警 / system=系统注入 / command=斜杠命令',
    `from_owner` tinyint NULL COMMENT '是否产品负责人本人: 1=确认(transcript 标记) 0=确认非本人 NULL=无标记(归档/旧消息)',
    `content` mediumtext NOT NULL COMMENT '消息正文原文(不脱敏)',
    `reply_content` mediumtext COMMENT '回答正文：assistant 在本轮内的所有 text 块按顺序合并（跳过工具调用与思考）',
    `content_len` int unsigned NOT NULL DEFAULT '0' COMMENT '正文字符数',
    `content_hash` char(32) NOT NULL DEFAULT '' COMMENT '正文 MD5, 用于重复语句检索',
    `reply_len` int unsigned DEFAULT '0' COMMENT '回答正文字符数；0 表示本轮无文字回答（纯工具调用或未回答）',
    `reply_hash` char(32) DEFAULT NULL COMMENT '回答正文 MD5，用于幂等跟重检索',
    `reply_at` datetime (3) DEFAULT NULL COMMENT '回答首个文字块的时间（Asia/Shanghai）',
    `reply_parts` smallint unsigned DEFAULT '0' COMMENT '回答由几个文字块合并而来；>1 说明中间夹了工具调用',
    `msg_at` datetime (3) NOT NULL COMMENT '消息原始时间(Asia/Shanghai)',
    `ingested_at` datetime (3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入库时间',
    `source` varchar(32) NOT NULL DEFAULT 'transcript_events' COMMENT '采集来源',
    PRIMARY KEY (`pkid`) USING BTREE,
    UNIQUE KEY `uk_msg_uid` (`msg_uid`) USING BTREE,
    KEY `idx_msg_at` (`msg_at`) USING BTREE,
    KEY `idx_agent_time` (`agent_id`, `msg_at`) USING BTREE,
    KEY `idx_host_time` (`host_id`, `msg_at`) USING BTREE,
    KEY `idx_hash` (`content_hash`) USING BTREE,
    KEY `idx_kind` (`msg_kind`, `msg_at`) USING BTREE,
    KEY `idx_session` (`session_id`, `msg_at`) USING BTREE
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '一问一答全量归档(跨主机/跨平台)：一行 = 一条 owner 消息 + 它的回答';

-- ----------------------------
-- Table structure for agx_user_message_cursor
-- 按会话 seq 增量游标，seq 由 transcript_events 主键 (session_id, seq) 提供
-- ----------------------------
CREATE TABLE
  IF NOT EXISTS `agx_user_message_cursor` (
    `pkid` int unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `host_id` varchar(64) NOT NULL COMMENT '主机 ID',
    `platform` varchar(32) NOT NULL DEFAULT 'openclaw' COMMENT '平台标识',
    `agent_id` varchar(64) NOT NULL COMMENT 'Agent ID',
    `session_id` varchar(64) NOT NULL COMMENT 'gateway 会话 ID',
    `last_seq` bigint NOT NULL DEFAULT '0' COMMENT '已采集到的最大 transcript seq',
    `msg_count` int unsigned NOT NULL DEFAULT '0' COMMENT '本会话累计采集条数',
    `updated_at` datetime (3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
    PRIMARY KEY (`pkid`),
    UNIQUE KEY `uk_cursor` (`host_id`, `platform`, `agent_id`, `session_id`) USING BTREE
  ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '用户消息采集游标(按会话 seq 增量)';

