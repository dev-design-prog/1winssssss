import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict, Any


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    def get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER UNIQUE NOT NULL,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    balance INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS nft_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS promos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    stars INTEGER NOT NULL,
                    max_activations INTEGER NOT NULL,
                    used INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS promo_uses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    promo_name TEXT NOT NULL,
                    used_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, promo_name)
                );

                CREATE TABLE IF NOT EXISTS game_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    bet INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    win INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_deposits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE NOT NULL,
                    total_deposited INTEGER DEFAULT 0,
                    first_deposit_at TEXT,
                    can_withdraw_unlock BOOLEAN DEFAULT 0
                );
            """)

    def add_user(self, user_id: int, username: str, first_name: str):
        with self.get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                (user_id, username, first_name)
            )
            # Update username if changed
            conn.execute(
                "UPDATE users SET username=?, first_name=? WHERE user_id=?",
                (username, first_name, user_id)
            )

    def get_user(self, user_id: int) -> Optional[Dict]:
        with self.get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_user_id_by_username(self, username: str) -> Optional[int]:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT user_id FROM users WHERE LOWER(username)=LOWER(?)", (username,)
            ).fetchone()
            return row["user_id"] if row else None

    def get_balance(self, user_id: int) -> int:
        with self.get_conn() as conn:
            row = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
            return row["balance"] if row else 0

    def add_balance(self, user_id: int, amount: int):
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id=?",
                (amount, user_id)
            )

    def deduct_balance(self, user_id: int, amount: int) -> bool:
        with self.get_conn() as conn:
            row = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not row or row["balance"] < amount:
                return False
            conn.execute(
                "UPDATE users SET balance = balance - ? WHERE user_id=?",
                (amount, user_id)
            )
            return True

    def create_nft_request(self, user_id: int, amount: int) -> int:
        with self.get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO nft_requests (user_id, amount) VALUES (?, ?)",
                (user_id, amount)
            )
            return cursor.lastrowid

    def get_nft_request(self, request_id: int) -> Optional[Dict]:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM nft_requests WHERE id=?", (request_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_nft_request_status(self, request_id: int, status: str):
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE nft_requests SET status=? WHERE id=?",
                (status, request_id)
            )

    def create_promo(self, name: str, max_activations: int, stars: int):
        with self.get_conn() as conn:
            conn.execute(
                "INSERT INTO promos (name, stars, max_activations) VALUES (?, ?, ?)",
                (name, stars, max_activations)
            )

    def promo_exists(self, name: str) -> bool:
        with self.get_conn() as conn:
            row = conn.execute("SELECT id FROM promos WHERE UPPER(name)=UPPER(?)", (name,)).fetchone()
            return row is not None

    def get_promo(self, name: str) -> Optional[Dict]:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM promos WHERE UPPER(name)=UPPER(?)", (name,)
            ).fetchone()
            return dict(row) if row else None

    def activate_promo(self, user_id: int, name: str) -> Dict:
        promo = self.get_promo(name)
        if not promo:
            return {"success": False, "error": "not_found"}
        if promo["used"] >= promo["max_activations"]:
            return {"success": False, "error": "expired"}
        
        with self.get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO promo_uses (user_id, promo_name) VALUES (?, ?)",
                    (user_id, name.upper())
                )
            except sqlite3.IntegrityError:
                return {"success": False, "error": "already_used"}
            
            conn.execute(
                "UPDATE promos SET used = used + 1 WHERE UPPER(name)=UPPER(?)", (name,)
            )
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id=?",
                (promo["stars"], user_id)
            )
        
        new_balance = self.get_balance(user_id)
        return {"success": True, "stars": promo["stars"], "balance": new_balance}

    def get_all_promos(self) -> List[Dict]:
        with self.get_conn() as conn:
            rows = conn.execute("SELECT * FROM promos ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def add_game_history(self, user_id: int, game: str, bet: int, result: str, win: int):
        with self.get_conn() as conn:
            conn.execute(
                "INSERT INTO game_history (user_id, game, bet, result, win) VALUES (?, ?, ?, ?, ?)",
                (user_id, game, bet, result, win)
            )

    def get_game_history(self, user_id: int, limit: int = 20) -> List[Dict]:
        try:
            safe_limit = max(1, min(int(limit), 100))
            with self.get_conn() as conn:
                rows = conn.execute(
                    "SELECT id, user_id, game, bet, result, win, created_at "
                    "FROM game_history WHERE user_id=? "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (int(user_id), safe_limit)
                ).fetchall()
                history = []
                for row in rows:
                    item = dict(row)
                    item["created_at"] = str(item.get("created_at") or "")
                    try:
                        item["bet"] = int(item.get("bet") or 0)
                    except (TypeError, ValueError):
                        item["bet"] = 0
                    try:
                        item["win"] = int(item.get("win") or 0)
                    except (TypeError, ValueError):
                        item["win"] = 0
                    item["game"] = str(item.get("game") or "unknown")
                    item["result"] = str(item.get("result") or "")
                    history.append(item)
                return history
        except Exception as e:
            print(f"❌ Ошибка получения истории: {e}")
            return []

    def get_top_users(self, limit: int = 10) -> List[Dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT u.username, u.first_name,
                       COALESCE(SUM(CASE WHEN gh.win > 0 THEN gh.win ELSE 0 END), 0) as total_won
                FROM users u
                LEFT JOIN game_history gh ON u.user_id = gh.user_id
                GROUP BY u.user_id
                ORDER BY total_won DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_top_deposits(self, limit: int = 10) -> List[Dict]:
        """Get top users by total deposited (for leaderboard like in screenshots)"""
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT u.user_id, u.username, u.first_name,
                       COALESCE(SUM(CASE WHEN ud.can_withdraw_unlock OR ud.total_deposited > 0 
                                         THEN ud.total_deposited ELSE 0 END), 0) as total_deposit
                FROM users u
                LEFT JOIN user_deposits ud ON u.user_id = ud.user_id
                GROUP BY u.user_id
                HAVING total_deposit > 0
                ORDER BY total_deposit DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_user_ids(self) -> list:
        """Возвращает список всех user_id для рассылки."""
        with self.get_conn() as conn:
            rows = conn.execute("SELECT user_id FROM users").fetchall()
            return [r["user_id"] for r in rows]

    def get_all_users_count(self) -> int:
        """Количество пользователей в базе."""
        with self.get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
            return row["cnt"] if row else 0

    def track_deposit(self, user_id: int, amount: int):
        """Отследить депозит пользователя"""
        with self.get_conn() as conn:
            existing = conn.execute(
                "SELECT * FROM user_deposits WHERE user_id=?", (user_id,)
            ).fetchone()
            
            if existing:
                conn.execute(
                    "UPDATE user_deposits SET total_deposited = total_deposited + ? WHERE user_id=?",
                    (amount, user_id)
                )
            else:
                conn.execute(
                    "INSERT INTO user_deposits (user_id, total_deposited, first_deposit_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (user_id, amount)
                )

    def get_user_deposits(self, user_id: int) -> Optional[Dict]:
        """Получить информацию о депозитах пользователя"""
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_deposits WHERE user_id=?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def unlock_withdraw_for_user(self, user_id: int):
        """Разблокировать вывод для пользователя (админ команда)"""
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE user_deposits SET can_withdraw_unlock = 1 WHERE user_id=?", (user_id,)
            )

    def create_withdrawal_request(self, user_id: int, amount: int) -> int:
        """Создать запрос на вывод"""
        with self.get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO withdrawals (user_id, amount) VALUES (?, ?)",
                (user_id, amount)
            )
            return cursor.lastrowid

    def get_withdrawal_request(self, request_id: int) -> Optional[Dict]:
        """Получить информацию о запросе вывода"""
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM withdrawals WHERE id=?", (request_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_withdrawal_status(self, request_id: int, status: str):
        """Обновить статус запроса вывода"""
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE withdrawals SET status=? WHERE id=?",
                (status, request_id)
            )

    def get_pending_withdrawals(self) -> List[Dict]:
        """Получить все ожидающие выводы"""
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM withdrawals WHERE status='pending' ORDER BY created_at"
            ).fetchall()
            return [dict(r) for r in rows]
