-- --------------------------------------------------------
-- Host:                         127.0.0.1
-- Server version:               10.4.28-MariaDB - mariadb.org binary distribution
-- Server OS:                    Win64
-- HeidiSQL Version:             12.0.0.6468
-- --------------------------------------------------------

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES utf8 */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;


-- Dumping database structure for latinbat_bedrock
CREATE DATABASE IF NOT EXISTS `latinbat_bedrock` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci */;
USE `latinbat_bedrock`;

-- Dumping structure for table latinbat_bedrock.daily_stats
CREATE TABLE IF NOT EXISTS `daily_stats` (
  `stat_date` date NOT NULL,
  `unique_players` int(11) DEFAULT NULL,
  `total_seconds` bigint(20) DEFAULT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`stat_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Dumping data for table latinbat_bedrock.daily_stats: ~1 rows (approximately)
REPLACE INTO `daily_stats` (`stat_date`, `unique_players`, `total_seconds`, `created_at`) VALUES
	('2026-01-08', 0, 0, '2026-01-09 17:58:58');

-- Dumping structure for function latinbat_bedrock.format_seconds
DELIMITER //
CREATE FUNCTION `format_seconds`(sec BIGINT) RETURNS varchar(20) CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci
    DETERMINISTIC
BEGIN
    RETURN SEC_TO_TIME(sec);
END//
DELIMITER ;

-- Dumping structure for table latinbat_bedrock.players
CREATE TABLE IF NOT EXISTS `players` (
  `xuid` varchar(32) NOT NULL,
  `name` varchar(32) NOT NULL,
  `first_seen` datetime NOT NULL,
  `last_seen` datetime NOT NULL,
  `total_seconds` bigint(20) DEFAULT 0,
  PRIMARY KEY (`xuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Dumping data for table latinbat_bedrock.players: ~2 rows (approximately)
REPLACE INTO `players` (`xuid`, `name`, `first_seen`, `last_seen`, `total_seconds`) VALUES
	('2533274852016615', 'Azzlaer', '2026-01-09 19:17:01', '2026-01-09 22:07:23', 848),
	('2535427999433519', 'Kiranever533', '2026-01-09 20:17:44', '2026-01-09 20:18:38', 53);

-- Dumping structure for table latinbat_bedrock.sessions
CREATE TABLE IF NOT EXISTS `sessions` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `xuid` varchar(32) NOT NULL,
  `join_time` datetime NOT NULL,
  `leave_time` datetime DEFAULT NULL,
  `session_seconds` int(11) DEFAULT 0,
  PRIMARY KEY (`id`),
  KEY `xuid` (`xuid`),
  CONSTRAINT `sessions_ibfk_1` FOREIGN KEY (`xuid`) REFERENCES `players` (`xuid`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=11 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Dumping data for table latinbat_bedrock.sessions: ~10 rows (approximately)
REPLACE INTO `sessions` (`id`, `xuid`, `join_time`, `leave_time`, `session_seconds`) VALUES
	(1, '2533274852016615', '2026-01-09 19:17:01', '2026-01-09 19:18:12', 71),
	(2, '2533274852016615', '2026-01-09 19:19:16', '2026-01-09 19:19:30', 14),
	(3, '2533274852016615', '2026-01-09 19:23:00', '2026-01-09 19:23:08', 7),
	(4, '2533274852016615', '2026-01-09 19:23:58', '2026-01-09 19:24:07', 9),
	(5, '2533274852016615', '2026-01-09 19:29:26', NULL, 0),
	(6, '2533274852016615', '2026-01-09 19:51:02', '2026-01-09 20:01:27', 624),
	(7, '2535427999433519', '2026-01-09 20:17:44', '2026-01-09 20:18:38', 53),
	(8, '2533274852016615', '2026-01-09 20:26:40', '2026-01-09 20:28:43', 123),
	(9, '2533274852016615', '2026-01-09 22:01:34', NULL, 0),
	(10, '2533274852016615', '2026-01-09 22:07:23', NULL, 0);

-- Dumping structure for view latinbat_bedrock.v_last_sessions
-- Creating temporary table to overcome VIEW dependency errors
CREATE TABLE `v_last_sessions` (
	`name` VARCHAR(32) NOT NULL COLLATE 'utf8mb4_unicode_ci',
	`join_time` DATETIME NOT NULL,
	`leave_time` DATETIME NULL,
	`duration` TIME NULL
) ENGINE=MyISAM;

-- Dumping structure for view latinbat_bedrock.v_today_playtime
-- Creating temporary table to overcome VIEW dependency errors
CREATE TABLE `v_today_playtime` (
	`name` VARCHAR(32) NOT NULL COLLATE 'utf8mb4_unicode_ci',
	`today_time` TIME NULL
) ENGINE=MyISAM;

-- Dumping structure for view latinbat_bedrock.v_top_players
-- Creating temporary table to overcome VIEW dependency errors
CREATE TABLE `v_top_players` (
	`name` VARCHAR(32) NOT NULL COLLATE 'utf8mb4_unicode_ci',
	`total_time` TIME NULL,
	`total_seconds` BIGINT(20) NULL
) ENGINE=MyISAM;

-- Dumping structure for view latinbat_bedrock.v_last_sessions
-- Removing temporary table and create final VIEW structure
DROP TABLE IF EXISTS `v_last_sessions`;
CREATE ALGORITHM=UNDEFINED SQL SECURITY DEFINER VIEW `v_last_sessions` AS SELECT
    p.name,
    s.join_time,
    s.leave_time,
    SEC_TO_TIME(s.session_seconds) AS duration
FROM sessions s
JOIN players p ON p.xuid = s.xuid
ORDER BY s.join_time DESC ;

-- Dumping structure for view latinbat_bedrock.v_today_playtime
-- Removing temporary table and create final VIEW structure
DROP TABLE IF EXISTS `v_today_playtime`;
CREATE ALGORITHM=UNDEFINED SQL SECURITY DEFINER VIEW `v_today_playtime` AS SELECT
    p.name,
    SEC_TO_TIME(SUM(s.session_seconds)) AS today_time
FROM sessions s
JOIN players p ON p.xuid = s.xuid
WHERE DATE(s.join_time) = CURDATE()
GROUP BY p.name ;

-- Dumping structure for view latinbat_bedrock.v_top_players
-- Removing temporary table and create final VIEW structure
DROP TABLE IF EXISTS `v_top_players`;
CREATE ALGORITHM=UNDEFINED SQL SECURITY DEFINER VIEW `v_top_players` AS SELECT
    name,
    SEC_TO_TIME(total_seconds) AS total_time,
    total_seconds
FROM players
ORDER BY total_seconds DESC ;

/*!40103 SET TIME_ZONE=IFNULL(@OLD_TIME_ZONE, 'system') */;
/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IFNULL(@OLD_FOREIGN_KEY_CHECKS, 1) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40111 SET SQL_NOTES=IFNULL(@OLD_SQL_NOTES, 1) */;
