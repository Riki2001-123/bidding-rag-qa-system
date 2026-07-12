-- 创建 text_chunks 表
CREATE TABLE IF NOT EXISTS `text_chunks` (
    `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `domain` varchar(32) NOT NULL COMMENT '业务域(policy/tender/enterprise)',
    `record_id` int NOT NULL COMMENT '关联记录ID',
    `chunk_key` varchar(255) NOT NULL COMMENT '分块唯一标识',
    `source_field` varchar(128) NOT NULL COMMENT '来源字段名',
    `chunk_order` int DEFAULT 0 COMMENT '分块顺序',
    `content` text NOT NULL COMMENT '分块内容',
    `content_preview` varchar(255) DEFAULT '' COMMENT '内容预览',
    `embedding_model` varchar(128) DEFAULT 'hashing' COMMENT '使用的嵌入模型',
    `vector_indexed` tinyint(1) DEFAULT 0 COMMENT '是否已建立向量索引',
    `metadata_json` text COMMENT '元数据JSON',
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_chunk_key` (`chunk_key`),
    KEY `idx_domain` (`domain`),
    KEY `idx_record_id` (`domain`, `record_id`),
    KEY `idx_vector_indexed` (`vector_indexed`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='文本分块表';
