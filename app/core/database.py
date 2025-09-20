"""
数据库管理模块
"""
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

from .logging import logger


class DatabaseManager:
    """数据库管理器"""
    
    def __init__(self, db_path: str = "sync_records.db"):
        self.db_path = Path(db_path)
        self._init_database()
    
    def _init_database(self) -> None:
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建同步记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sync_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_name TEXT NOT NULL,
                title TEXT NOT NULL,
                ori_title TEXT,
                season INTEGER NOT NULL,
                episode INTEGER NOT NULL,
                subject_id TEXT,
                episode_id TEXT,
                status TEXT NOT NULL,
                message TEXT,
                source TEXT NOT NULL
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info(f'数据库初始化完成: {self.db_path}')
    
    def log_sync_record(self, user_name: str, title: str, ori_title: str, 
                       season: int, episode: int, subject_id: Optional[str] = None, 
                       episode_id: Optional[str] = None, status: str = "success", 
                       message: str = "", source: str = "custom") -> None:
        """记录同步日志到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 使用本地时间而不是UTC时间
            local_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute('''
                INSERT INTO sync_records 
                (timestamp, user_name, title, ori_title, season, episode, subject_id, episode_id, status, message, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (local_time, user_name, title, ori_title, season, episode, subject_id, episode_id, status, message, source))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"记录同步日志失败: {e}")
    
    def get_sync_records(self, limit: int = 100, offset: int = 0, 
                        status: Optional[str] = None, user_name: Optional[str] = None, 
                        source: Optional[str] = None) -> Dict[str, Any]:
        """获取同步记录"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 构建查询条件
            where_conditions = []
            params = []
            
            if status:
                where_conditions.append("status = ?")
                params.append(status)
            
            if user_name:
                where_conditions.append("user_name = ?")
                params.append(user_name)
            
            if source:
                where_conditions.append("source = ?")
                params.append(source)
            
            where_clause = " WHERE " + " AND ".join(where_conditions) if where_conditions else ""
            
            # 获取总数
            count_query = f"SELECT COUNT(*) FROM sync_records{where_clause}"
            cursor.execute(count_query, params)
            total = cursor.fetchone()[0]
            
            # 获取记录
            query = f"""
                SELECT id, timestamp, user_name, title, ori_title, season, episode, 
                       subject_id, episode_id, status, message, source
                FROM sync_records{where_clause}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """
            cursor.execute(query, params + [limit, offset])
            
            records = []
            for row in cursor.fetchall():
                records.append({
                    "id": row[0],
                    "timestamp": row[1],
                    "user_name": row[2],
                    "title": row[3],
                    "ori_title": row[4],
                    "season": row[5],
                    "episode": row[6],
                    "subject_id": row[7],
                    "episode_id": row[8],
                    "status": row[9],
                    "message": row[10],
                    "source": row[11]
                })
            
            conn.close()
            
            return {
                "records": records,
                "total": total,
                "limit": limit,
                "offset": offset
            }
        except Exception as e:
            logger.error(f"获取同步记录失败: {e}")
            raise
    
    def get_sync_record_by_id(self, record_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取单个同步记录"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, timestamp, user_name, title, ori_title, season, episode, 
                       subject_id, episode_id, status, message, source
                FROM sync_records 
                WHERE id = ?
            ''', (record_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    "id": row[0],
                    "timestamp": row[1],
                    "user_name": row[2],
                    "title": row[3],
                    "ori_title": row[4],
                    "season": row[5],
                    "episode": row[6],
                    "subject_id": row[7],
                    "episode_id": row[8],
                    "status": row[9],
                    "message": row[10],
                    "source": row[11]
                }
            return None
        except Exception as e:
            logger.error(f"获取同步记录详情失败: {e}")
            raise
    
    def get_sync_stats(self) -> Dict[str, Any]:
        """获取同步统计信息"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 总同步次数
            cursor.execute("SELECT COUNT(*) FROM sync_records")
            total_syncs = cursor.fetchone()[0]
            
            # 成功同步次数
            cursor.execute("SELECT COUNT(*) FROM sync_records WHERE status = 'success'")
            success_syncs = cursor.fetchone()[0]
            
            # 失败同步次数
            cursor.execute("SELECT COUNT(*) FROM sync_records WHERE status = 'error'")
            error_syncs = cursor.fetchone()[0]
            
            # 今日同步次数
            cursor.execute("SELECT COUNT(*) FROM sync_records WHERE DATE(timestamp) = DATE('now')")
            today_syncs = cursor.fetchone()[0]
            
            # 用户统计
            cursor.execute("SELECT user_name, COUNT(*) FROM sync_records GROUP BY user_name ORDER BY COUNT(*) DESC")
            user_stats = [{"user": row[0], "count": row[1]} for row in cursor.fetchall()]
            
            # 最近7天统计
            cursor.execute("""
                SELECT DATE(timestamp) as date, COUNT(*) as count
                FROM sync_records 
                WHERE timestamp >= datetime('now', '-7 days')
                GROUP BY DATE(timestamp)
                ORDER BY date
            """)
            daily_stats = [{"date": row[0], "count": row[1]} for row in cursor.fetchall()]
            
            conn.close()
            
            return {
                "total_syncs": total_syncs,
                "success_syncs": success_syncs,
                "error_syncs": error_syncs,
                "today_syncs": today_syncs,
                "success_rate": round(success_syncs / total_syncs * 100, 2) if total_syncs > 0 else 0,
                "user_stats": user_stats,
                "daily_stats": daily_stats
            }
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            raise


# 全局数据库实例
database_manager = DatabaseManager() 