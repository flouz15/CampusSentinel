-- 创建数据库脚本
-- 请在MySQL命令行或MySQL Workbench中执行此脚本

-- 创建数据库
CREATE DATABASE IF NOT EXISTS campus_security
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

-- 显示数据库
SHOW DATABASES LIKE 'campus_security';

-- 使用数据库
USE campus_security;

-- 显示当前数据库
SELECT DATABASE();
