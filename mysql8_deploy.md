ниже — **единый idempotent SQL-скрипт** для развёртывания новой БД MySQL 8 под текущий код и новую политику ретраев. Он:

* создаёт БД `smart_agent` и `smart_agent_admin`;
* заводит пользователя/пароль из вашего `.env`;
* создаёт все нужные таблицы (включая новые поля `last_attempt_at`, `last_fail_notice_at`, `consecutive_failures`);
* накидывает индексы и внешние ключи;
* безопасно повторно выполняется (IF NOT EXISTS).

Сохраните как `init_smart_agent.sql` и выполните под root/админом:

```bash
mysql -uroot -p < init_smart_agent.sql
```

---

```sql
-- =====================================================================
-- Smart Agent • fresh install (MySQL 8+)
-- =====================================================================

/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='STRICT_ALL_TABLES' */;

-- -----------------------------------------------------------------------------
-- 0) Databases + user
-- -----------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS `smart_agent`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS `smart_agent_admin`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- user from your config:
-- MYSQL_USER=smartagent
-- MYSQL_PASSWORD=Kid_pkxcrh48HocENFFzqjn1|l|L9Hguci
CREATE USER IF NOT EXISTS 'smartagent'@'%'
  IDENTIFIED BY 'Kid_pkxcrh48HocENFFzqjn1|l|L9Hguci';

GRANT ALL PRIVILEGES ON `smart_agent`.*       TO 'smartagent'@'%';
GRANT ALL PRIVILEGES ON `smart_agent_admin`.* TO 'smartagent'@'%';
FLUSH PRIVILEGES;

-- Далее работаем в основной БД
USE `smart_agent`;

-- -----------------------------------------------------------------------------
-- 1) Core billing tables
-- -----------------------------------------------------------------------------

-- subscriptions
CREATE TABLE IF NOT EXISTS `subscriptions` (
  `id`                   INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`              BIGINT NOT NULL,
  `plan_code`            VARCHAR(32)  NOT NULL,
  `interval_months`      INT          NOT NULL DEFAULT 1,
  `amount_value`         VARCHAR(32)  NOT NULL,
  `amount_currency`      VARCHAR(8)   NOT NULL DEFAULT 'RUB',
  `payment_method_id`    VARCHAR(64)       NULL,             -- провайдерский токен карты
  `payment_method_token` VARCHAR(128)      NULL,             -- legacy/не используется
  `status`               VARCHAR(16)  NOT NULL DEFAULT 'active',  -- active|canceled
  `next_charge_at`       DATETIME(6)       NULL,
  `last_charge_at`       DATETIME(6)       NULL,
  `last_attempt_at`      DATETIME(6)       NULL,             -- NEW (щит B)
  `consecutive_failures` INT          NOT NULL DEFAULT 0,    -- NEW (счётчик фейлов)
  `last_fail_notice_at`  DATETIME(6)       NULL,             -- NEW (троттлинг уведомлений)
  `cancel_at`            DATETIME(6)       NULL,
  `created_at`           DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at`           DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  KEY `idx_sub_user` (`user_id`),
  KEY `idx_sub_status_next` (`status`, `next_charge_at`),
  KEY `idx_sub_user_status` (`user_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- payment_methods
CREATE TABLE IF NOT EXISTS `payment_methods` (
  `id`                 INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`            BIGINT      NOT NULL,
  `provider`           VARCHAR(32) NOT NULL DEFAULT 'yookassa',
  `provider_pm_token`  VARCHAR(128) NOT NULL,
  `brand`              VARCHAR(32)  NULL,
  `first6`             VARCHAR(6)   NULL,
  `last4`              VARCHAR(4)   NULL,
  `exp_month`          INT          NULL,
  `exp_year`           INT          NULL,
  `created_at`         DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `deleted_at`         DATETIME(6)  NULL,
  UNIQUE KEY `uq_provider_token` (`provider_pm_token`),
  KEY `idx_pm_user` (`user_id`),
  KEY `idx_pm_deleted` (`deleted_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- charge_attempts (источник правды по попыткам)
CREATE TABLE IF NOT EXISTS `charge_attempts` (
  `id`               INT AUTO_INCREMENT PRIMARY KEY,
  `subscription_id`  INT     NOT NULL,
  `user_id`          BIGINT  NOT NULL,
  `payment_id`       VARCHAR(64) NULL,            -- id платежа у провайдера
  `status`           VARCHAR(16) NOT NULL DEFAULT 'created', -- created|succeeded|canceled|expired
  `attempted_at`     DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY `idx_attempt_sub_time` (`subscription_id`, `attempted_at`),
  KEY `idx_attempt_user` (`user_id`),
  CONSTRAINT `fk_attempt_sub`
    FOREIGN KEY (`subscription_id`) REFERENCES `subscriptions`(`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- payment_log (журнал вебхуков)
CREATE TABLE IF NOT EXISTS `payment_log` (
  `payment_id`       VARCHAR(64) PRIMARY KEY,
  `user_id`          BIGINT       NULL,
  `amount_value`     VARCHAR(32)  NULL,
  `amount_currency`  VARCHAR(8)   NULL,
  `event`            VARCHAR(64)  NULL,
  `status`           VARCHAR(32)  NULL,
  `metadata_json`    LONGTEXT     NULL,
  `raw_payload_json` LONGTEXT     NULL,
  `created_at`       DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `processed_at`     DATETIME(6)  NULL,
  KEY `idx_pl_user` (`user_id`),
  KEY `idx_pl_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- -----------------------------------------------------------------------------
-- 2) App tables, в том виде как они уже встречаются в проде
-- -----------------------------------------------------------------------------

-- users
CREATE TABLE IF NOT EXISTS `users` (
  `user_id`  BIGINT      NOT NULL PRIMARY KEY,
  `chat_id`  BIGINT      NULL,
  `username` VARCHAR(255) NULL,
  KEY `idx_users_chat` (`chat_id`),
  KEY `idx_users_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- trials
CREATE TABLE IF NOT EXISTS `trials` (
  `user_id`   BIGINT      NOT NULL PRIMARY KEY,
  `until_at`  DATETIME(6) NOT NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- user_consents
CREATE TABLE IF NOT EXISTS `user_consents` (
  `id`         INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`    BIGINT      NOT NULL,
  `kind`       VARCHAR(32) NOT NULL,
  `accepted_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY `idx_uc_user_kind` (`user_id`, `kind`),
  KEY `idx_uc_accepted` (`accepted_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- description_history (как в вашем дампе)
CREATE TABLE IF NOT EXISTS `description_history` (
  `id`          INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`     BIGINT      NOT NULL,
  `created_at`  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `fields_json` LONGTEXT    NOT NULL,
  `result_text` LONGTEXT    NOT NULL,
  KEY `idx_desc_user_created` (`user_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- review_history (пусто у вас — оставим совместимую форму)
CREATE TABLE IF NOT EXISTS `review_history` (
  `id`          INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`     BIGINT      NOT NULL,
  `created_at`  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `fields_json` LONGTEXT    NULL,
  `result_text` LONGTEXT    NULL,
  KEY `idx_rev_user_created` (`user_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- summary_history (схема совместимая/минимальная)
CREATE TABLE IF NOT EXISTS `summary_history` (
  `id`          INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`     BIGINT      NOT NULL,
  `created_at`  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `summary_text` LONGTEXT   NULL,
  KEY `idx_sum_user_created` (`user_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- -----------------------------------------------------------------------------
-- 3) Safety re-assert (если база уже частично была создана без новых полей)
-- -----------------------------------------------------------------------------
-- добавим недостающие столбцы на subscriptions (idempotent)
ALTER TABLE `subscriptions`
  ADD COLUMN IF NOT EXISTS `last_attempt_at`     DATETIME(6) NULL,
  ADD COLUMN IF NOT EXISTS `last_fail_notice_at` DATETIME(6) NULL,
  ADD COLUMN IF NOT EXISTS `consecutive_failures` INT NOT NULL DEFAULT 0;

UPDATE `subscriptions` SET `consecutive_failures` = 0
 WHERE `consecutive_failures` IS NULL;

-- индексы (если кто-то менял структуру вручную)
SET @need := (SELECT COUNT(*) = 0 FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME='subscriptions'
                 AND INDEX_NAME='idx_sub_status_next');
SET @sql := IF(@need, 'CREATE INDEX `idx_sub_status_next` ON `subscriptions` (`status`,`next_charge_at`)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @need := (SELECT COUNT(*) = 0 FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME='subscriptions'
                 AND INDEX_NAME='idx_sub_user_status');
SET @sql := IF(@need, 'CREATE INDEX `idx_sub_user_status` ON `subscriptions` (`user_id`,`status`)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @need := (SELECT COUNT(*) = 0 FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME='charge_attempts'
                 AND INDEX_NAME='idx_attempt_sub_time');
SET @sql := IF(@need, 'CREATE INDEX `idx_attempt_sub_time` ON `charge_attempts` (`subscription_id`,`attempted_at`)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- Гарантируем NOT NULL + DEFAULT у счётчика
ALTER TABLE `subscriptions`
  MODIFY `consecutive_failures` INT NOT NULL DEFAULT 0;

-- -----------------------------------------------------------------------------
/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
```

**Как запускать на чистом сервере:**

```bash
# 1) загрузка скрипта
scp init_smart_agent.sql root@NEW_HOST:/root/

# 2) применение под root
ssh root@NEW_HOST "mysql -uroot -p < /root/init_smart_agent.sql"

# 3) (опционально) проверить доступ app-пользователя
mysql -usmartagent -p smart_agent -e "SHOW TABLES; SHOW GRANTS FOR 'smartagent'@'%';"
```

если позже появятся новые таблицы приложения — добавим их в этот же скрипт по тому же принципу (idempotent + utf8mb4 + InnoDB).
