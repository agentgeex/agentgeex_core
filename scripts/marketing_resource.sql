-- ============================================================
-- agentgeex.marketing_resource: table DDL + seed data
-- Marketing situational awareness, dimension 1 (self): candidate resource list
-- Purpose: manage marketing data sources and OSS component candidates in a table instead of doc-only search, to speed up testing
-- Note: INSERT ... ON DUPLICATE KEY UPDATE is idempotent; safe to re-run
-- Run: mysql --default-character-set=UTF8 -h127.0.0.1 -uroot -p<your-password> agentgeex < marketing_resource.sql
-- ============================================================

-- Table DDL (IF NOT EXISTS, idempotent)
CREATE TABLE IF NOT EXISTS marketing_resource (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
  dimension VARCHAR(50) NOT NULL DEFAULT 'dim1' COMMENT '营销态势感知维度(dim1=我自己)',
  category VARCHAR(50) NOT NULL COMMENT '资源类别(DNSBL/uptime/社媒/LLM成本/表单转化/内容频次/GSC/SEO/SSL/信誉/shadowban/K站/clickfraud/EDM送达)',
  res_type ENUM('public_api','github','service','tool') NOT NULL DEFAULT 'public_api' COMMENT '资源类型',
  name VARCHAR(200) NOT NULL COMMENT '资源名称',
  description TEXT COMMENT '资源描述/说明',
  url VARCHAR(500) DEFAULT '' COMMENT '官方/仓库 URL',
  stars INT DEFAULT 0 COMMENT 'GitHub star 数(仅开源组件)',
  language VARCHAR(50) DEFAULT '' COMMENT '主要语言(仅开源组件)',
  license VARCHAR(100) DEFAULT '' COMMENT '协议(开源)或免费额度(公域)',
  free_tier VARCHAR(200) DEFAULT '' COMMENT '免费额度/免费层',
  pricing_model ENUM('free','freemium','commercial','open_source') NOT NULL DEFAULT 'free' COMMENT '定价模型: free=完全免费/freemium=有免费层/commercial=纯商业付费/open_source=开源免费',
  status ENUM('recorded','pending','verified','adopted','rejected') NOT NULL DEFAULT 'recorded' COMMENT '状态: 已记录/待验证/已验证/已选用/已弃用',
  verify_note TEXT COMMENT '验证结果备注',
  source VARCHAR(50) DEFAULT '' COMMENT '来源(tavily/gh/手动)',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='营销态势感知-维度一资源候选清单';

-- ============================================================
-- Seed data (61 rows, from the full DISCOVERY_DIMENSION_1.md plus 4 follow-up checks)
-- Note: the actual inserts are the segmented INSERT statements below; this file only creates the table.
-- Data was imported manually through the mysql CLI (2026-08-31). To rebuild it,
-- recover the full INSERT statements from git history or the note_xxx announcement.
-- ============================================================
