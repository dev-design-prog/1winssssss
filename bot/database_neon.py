"""
PostgreSQL Database Module для Neon
Хранит данные в облаке, баланс не теряется при обновлении бота
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime
from typing import Optional, List, Dict, Any


class Database:
    def __init__(self, connection_string: str):
        """
        Инициализация БД с Neon PostgreSQL
        
        Args:
            connection_string: строка подключения вида
            postgresql://user:password@host/database?sslmode=require
        """
        self.connection_string = connection_string
        self.init_db()

    def get_conn(self):
        """Получить подключение к БД"""
        try:
            conn = psycopg2.connect(self.connection_string)
            return conn
        except psycopg2.Error as e:
            print(f"❌ Ошибка подключения к БД: {e}")
            raise

    def init_db(self):
        """Создать таблицы если их нет"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    # Таблица пользователей
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            id SERIAL PRIMARY KEY,
                            user_id BIGINT UNIQUE NOT NULL,
                            username VARCHAR(255) DEFAULT '',
                            first_name VARCHAR(255) DEFAULT '',
                            balance BIGINT DEFAULT 0,
                            terms_accepted BOOLEAN DEFAULT FALSE,
                            is_blocked BOOLEAN DEFAULT FALSE,
                            blocked_at TIMESTAMP,
                            blocked_by VARCHAR(255) DEFAULT '',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                    """)

                    # Миграция: добавить колонку terms_accepted если её нет (для старых БД)
                    cursor.execute("""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns
                                WHERE table_name='users' AND column_name='terms_accepted'
                            ) THEN
                                ALTER TABLE users ADD COLUMN terms_accepted BOOLEAN DEFAULT FALSE;
                            END IF;
                        END;
                        $$;
                    """)

                    # Миграция ЧС для уже существующей БД.
                    cursor.execute("""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns
                                WHERE table_name='users' AND column_name='is_blocked'
                            ) THEN
                                ALTER TABLE users ADD COLUMN is_blocked BOOLEAN DEFAULT FALSE;
                            END IF;
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns
                                WHERE table_name='users' AND column_name='blocked_at'
                            ) THEN
                                ALTER TABLE users ADD COLUMN blocked_at TIMESTAMP;
                            END IF;
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns
                                WHERE table_name='users' AND column_name='blocked_by'
                            ) THEN
                                ALTER TABLE users ADD COLUMN blocked_by VARCHAR(255) DEFAULT '';
                            END IF;
                        END;
                        $$;
                    """)

                    # Таблица заявок на пополнение NFT
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS nft_requests (
                            id SERIAL PRIMARY KEY,
                            user_id BIGINT NOT NULL,
                            amount BIGINT NOT NULL,
                            status VARCHAR(20) DEFAULT 'pending',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (user_id) REFERENCES users(user_id)
                        );
                    """)

                    # Таблица промокодов
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS promos (
                            id SERIAL PRIMARY KEY,
                            name VARCHAR(100) UNIQUE NOT NULL,
                            stars BIGINT NOT NULL,
                            max_activations BIGINT NOT NULL,
                            used BIGINT DEFAULT 0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                    """)

                    # Таблица использованных промокодов
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS promo_uses (
                            id SERIAL PRIMARY KEY,
                            user_id BIGINT NOT NULL,
                            promo_name VARCHAR(100) NOT NULL,
                            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(user_id, promo_name),
                            FOREIGN KEY (user_id) REFERENCES users(user_id)
                        );
                    """)

                    # Таблица истории игр
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS game_history (
                            id SERIAL PRIMARY KEY,
                            user_id BIGINT NOT NULL,
                            game VARCHAR(50) NOT NULL,
                            bet BIGINT NOT NULL,
                            result VARCHAR(255) NOT NULL,
                            win BIGINT DEFAULT 0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (user_id) REFERENCES users(user_id)
                        );
                    """)

                    # Таблица заявок на вывод
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS withdrawals (
                            id SERIAL PRIMARY KEY,
                            user_id BIGINT NOT NULL,
                            amount BIGINT NOT NULL,
                            status VARCHAR(20) DEFAULT 'pending',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (user_id) REFERENCES users(user_id)
                        );
                    """)

                    # Таблица депозитов пользователей
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS user_deposits (
                            id SERIAL PRIMARY KEY,
                            user_id BIGINT UNIQUE NOT NULL,
                            total_deposited BIGINT DEFAULT 0,
                            first_deposit_at TIMESTAMP,
                            can_withdraw_unlock BOOLEAN DEFAULT FALSE,
                            FOREIGN KEY (user_id) REFERENCES users(user_id)
                        );
                    """)

                    # Активные игровые сессии — храним в Neon, чтобы игра и кнопка «Забрать»
                    # переживали перезапуск Render/процесса и не терялись между инстансами.
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS active_game_sessions (
                            user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                            game VARCHAR(30) NOT NULL,
                            session JSONB NOT NULL,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                    """)

                    # Создать индексы для быстрого поиска
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON users(user_id);")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_history_user ON game_history(user_id);")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_nft_requests_user ON nft_requests(user_id);")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_withdrawals_user ON withdrawals(user_id);")

                    conn.commit()
                    print("✅ БД инициализирована (Neon PostgreSQL)")
        except psycopg2.Error as e:
            print(f"❌ Ошибка инициализации БД: {e}")
            raise

    def save_game_session(self, user_id: int, session: Dict) -> bool:
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO active_game_sessions (user_id, game, session, updated_at)
                        VALUES (%s, %s, %s::jsonb, CURRENT_TIMESTAMP)
                        ON CONFLICT (user_id) DO UPDATE SET
                            game = EXCLUDED.game, session = EXCLUDED.session, updated_at = CURRENT_TIMESTAMP;
                    """, (user_id, session.get("game", ""), json.dumps(session, ensure_ascii=False)))
                    conn.commit()
            return True
        except Exception as e:
            print(f"❌ Ошибка сохранения игровой сессии: {e}")
            return False

    def get_game_session(self, user_id: int) -> Optional[Dict]:
        try:
            with self.get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("SELECT session FROM active_game_sessions WHERE user_id=%s;", (user_id,))
                    row = cursor.fetchone()
                    if not row:
                        return None
                    value = row["session"]
                    return dict(value) if isinstance(value, dict) else json.loads(value)
        except Exception as e:
            print(f"❌ Ошибка получения игровой сессии: {e}")
            return None

    def delete_game_session(self, user_id: int) -> None:
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM active_game_sessions WHERE user_id=%s;", (user_id,))
                    conn.commit()
        except Exception as e:
            print(f"❌ Ошибка удаления игровой сессии: {e}")

    def add_user(self, user_id: int, username: str, first_name: str):
        """Добавить или обновить пользователя"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO users (user_id, username, first_name)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id) DO UPDATE SET
                        username = EXCLUDED.username,
                        first_name = EXCLUDED.first_name;
                    """, (user_id, username, first_name))
                    conn.commit()
        except psycopg2.Error as e:
            print(f"❌ Ошибка добавления пользователя: {e}")

    def get_user(self, user_id: int) -> Optional[Dict]:
        """Получить информацию о пользователе"""
        try:
            with self.get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("SELECT * FROM users WHERE user_id=%s;", (user_id,))
                    row = cursor.fetchone()
                    return dict(row) if row else None
        except psycopg2.Error as e:
            print(f"❌ Ошибка получения пользователя: {e}")
            return None

    def is_user_blocked(self, user_id: int) -> bool:
        """Проверить, находится ли пользователь в чёрном списке."""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COALESCE(is_blocked, FALSE) FROM users WHERE user_id=%s;", (user_id,))
                    row = cursor.fetchone()
                    return bool(row[0]) if row else False
        except psycopg2.Error as e:
            print(f"❌ Ошибка проверки ЧС пользователя: {e}")
            return False

    def block_user(self, user_id: int, blocked_by: str = "") -> bool:
        """Заблокировать пользователя."""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE users
                        SET is_blocked=TRUE, blocked_at=CURRENT_TIMESTAMP, blocked_by=%s
                        WHERE user_id=%s;
                    """, (blocked_by, user_id))
                    changed = cursor.rowcount > 0
                    conn.commit()
                    return changed
        except psycopg2.Error as e:
            print(f"❌ Ошибка блокировки пользователя: {e}")
            return False

    def unblock_user(self, user_id: int) -> bool:
        """Снять блокировку с пользователя."""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE users
                        SET is_blocked=FALSE, blocked_at=NULL, blocked_by=''
                        WHERE user_id=%s;
                    """, (user_id,))
                    changed = cursor.rowcount > 0
                    conn.commit()
                    return changed
        except psycopg2.Error as e:
            print(f"❌ Ошибка снятия блокировки: {e}")
            return False

    def get_user_id_by_username(self, username: str) -> Optional[int]:
        """Получить ID по username'у"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT user_id FROM users WHERE LOWER(username)=LOWER(%s);",
                        (username,)
                    )
                    row = cursor.fetchone()
                    return row[0] if row else None
        except psycopg2.Error as e:
            print(f"❌ Ошибка поиска по username: {e}")
            return None

    def has_accepted_terms(self, user_id: int) -> bool:
        """Проверить, принял ли пользователь условия использования"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT terms_accepted FROM users WHERE user_id=%s;", (user_id,))
                    row = cursor.fetchone()
                    if row is None:
                        return False
                    return bool(row[0])
        except psycopg2.Error as e:
            print(f"❌ Ошибка проверки terms_accepted: {e}")
            return False

    def accept_terms(self, user_id: int):
        """Сохранить подтверждение условий использования"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE users SET terms_accepted = TRUE WHERE user_id = %s;",
                        (user_id,)
                    )
                    conn.commit()
        except psycopg2.Error as e:
            print(f"❌ Ошибка сохранения terms_accepted: {e}")

    def reset_all_terms_accepted(self):
        """Сбросить принятие условий для всех пользователей (для принудительной рерассылки)"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("UPDATE users SET terms_accepted = FALSE;")
                    conn.commit()
                    cursor.execute("SELECT COUNT(*) FROM users WHERE terms_accepted = FALSE;")
                    count = cursor.fetchone()[0]
                    return count
        except psycopg2.Error as e:
            print(f"❌ Ошибка сброса terms_accepted: {e}")
            return 0

    def get_balance(self, user_id: int) -> int:
        """Получить баланс пользователя"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT balance FROM users WHERE user_id=%s;", (user_id,))
                    row = cursor.fetchone()
                    return row[0] if row else 0
        except psycopg2.Error as e:
            print(f"❌ Ошибка получения баланса: {e}")
            return 0

    def add_balance(self, user_id: int, amount: int):
        """Добавить баланс"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE users SET balance = balance + %s WHERE user_id=%s;",
                        (amount, user_id)
                    )
                    conn.commit()
        except psycopg2.Error as e:
            print(f"❌ Ошибка добавления баланса: {e}")

    def set_balance(self, user_id: int, amount: int) -> bool:
        """Установить точный баланс пользователя (админская операция)."""
        if amount < 0:
            return False
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE users SET balance = %s WHERE user_id=%s;",
                        (amount, user_id)
                    )
                    changed = cursor.rowcount == 1
                    conn.commit()
                    return changed
        except psycopg2.Error as e:
            print(f"❌ Ошибка установки баланса: {e}")
            return False

    def reset_all_balances(self) -> dict:
        """Сбросить баланс звёзд у всех пользователей до 0. Возвращает статистику операции."""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM users WHERE balance <> 0;")
                    row = cursor.fetchone()
                    affected = int(row[0] or 0)
                    total_stars = int(row[1] or 0)
                    cursor.execute("UPDATE users SET balance = 0 WHERE balance <> 0;")
                    conn.commit()
                    return {"users": affected, "stars": total_stars}
        except psycopg2.Error as e:
            print(f"❌ Ошибка сброса балансов: {e}")
            return {"users": 0, "stars": 0, "error": str(e)}

    def deduct_balance(self, user_id: int, amount: int) -> bool:
        """Атомарно списать баланс, не допуская race-condition при двух запросах."""
        if amount <= 0:
            return False
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE users SET balance = balance - %s WHERE user_id=%s AND balance >= %s;",
                        (amount, user_id, amount)
                    )
                    changed = cursor.rowcount == 1
                    conn.commit()
                    return changed
        except psycopg2.Error as e:
            print(f"❌ Ошибка списания баланса: {e}")
            return False

    def create_nft_request(self, user_id: int, amount: int) -> int:
        """Создать заявку на пополнение NFT"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO nft_requests (user_id, amount) VALUES (%s, %s) RETURNING id;",
                        (user_id, amount)
                    )
                    request_id = cursor.fetchone()[0]
                    conn.commit()
                    return request_id
        except psycopg2.Error as e:
            print(f"❌ Ошибка создания заявки NFT: {e}")
            return -1

    def get_nft_request(self, request_id: int) -> Optional[Dict]:
        """Получить информацию о заявке NFT"""
        try:
            with self.get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("SELECT * FROM nft_requests WHERE id=%s;", (request_id,))
                    row = cursor.fetchone()
                    return dict(row) if row else None
        except psycopg2.Error as e:
            print(f"❌ Ошибка получения заявки NFT: {e}")
            return None

    def update_nft_request_status(self, request_id: int, status: str):
        """Обновить статус заявки NFT"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE nft_requests SET status=%s WHERE id=%s;",
                        (status, request_id)
                    )
                    conn.commit()
        except psycopg2.Error as e:
            print(f"❌ Ошибка обновления статуса NFT: {e}")

    def create_promo(self, name: str, max_activations: int, stars: int):
        """Создать промокод"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO promos (name, stars, max_activations) VALUES (%s, %s, %s);",
                        (name, stars, max_activations)
                    )
                    conn.commit()
        except psycopg2.Error as e:
            print(f"❌ Ошибка создания промокода: {e}")

    def promo_exists(self, name: str) -> bool:
        """Проверить существует ли промокод"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM promos WHERE UPPER(name)=UPPER(%s);",
                        (name,)
                    )
                    return cursor.fetchone() is not None
        except psycopg2.Error as e:
            print(f"❌ Ошибка проверки промокода: {e}")
            return False

    def get_promo(self, name: str) -> Optional[Dict]:
        """Получить информацию о промокоде"""
        try:
            with self.get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM promos WHERE UPPER(name)=UPPER(%s);",
                        (name,)
                    )
                    row = cursor.fetchone()
                    return dict(row) if row else None
        except psycopg2.Error as e:
            print(f"❌ Ошибка получения промокода: {e}")
            return None

    def activate_promo(self, user_id: int, name: str) -> Dict:
        """Активировать промокод"""
        promo = self.get_promo(name)
        if not promo:
            return {"success": False, "error": "not_found"}
        if promo["used"] >= promo["max_activations"]:
            return {"success": False, "error": "expired"}
        
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    # Проверить использован ли уже этим пользователем
                    cursor.execute(
                        "SELECT id FROM promo_uses WHERE user_id=%s AND promo_name=%s;",
                        (user_id, name.upper())
                    )
                    if cursor.fetchone():
                        return {"success": False, "error": "already_used"}
                    
                    # Добавить в использованные
                    cursor.execute(
                        "INSERT INTO promo_uses (user_id, promo_name) VALUES (%s, %s);",
                        (user_id, name.upper())
                    )
                    
                    # Обновить счетчик
                    cursor.execute(
                        "UPDATE promos SET used = used + 1 WHERE UPPER(name)=UPPER(%s);",
                        (name,)
                    )
                    
                    # Добавить баланс
                    cursor.execute(
                        "UPDATE users SET balance = balance + %s WHERE user_id=%s;",
                        (promo["stars"], user_id)
                    )
                    
                    conn.commit()
                    new_balance = self.get_balance(user_id)
                    return {"success": True, "stars": promo["stars"], "balance": new_balance}
        except psycopg2.Error as e:
            print(f"❌ Ошибка активации промокода: {e}")
            return {"success": False, "error": "database_error"}

    def get_all_promos(self) -> List[Dict]:
        """Получить все промокоды"""
        try:
            with self.get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("SELECT * FROM promos ORDER BY created_at DESC;")
                    return [dict(r) for r in cursor.fetchall()]
        except psycopg2.Error as e:
            print(f"❌ Ошибка получения промокодов: {e}")
            return []

    def add_game_history(self, user_id: int, game: str, bet: int, result: str, win: int):
        """Добавить игру в историю"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO game_history (user_id, game, bet, result, win) VALUES (%s, %s, %s, %s, %s);",
                        (user_id, game, bet, result, win)
                    )
                    conn.commit()
        except psycopg2.Error as e:
            print(f"❌ Ошибка добавления в историю: {e}")

    def get_game_history(self, user_id: int, limit: int = 20) -> List[Dict]:
        """Получить историю игр пользователя в JSON-safe виде."""
        try:
            safe_limit = max(1, min(int(limit), 100))
            with self.get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(
                        "SELECT id, user_id, game, bet, result, win, created_at "
                        "FROM game_history WHERE user_id=%s "
                        "ORDER BY created_at DESC, id DESC LIMIT %s;",
                        (int(user_id), safe_limit)
                    )
                    history = []
                    for row in cursor.fetchall():
                        item = dict(row)
                        created_at = item.get("created_at")
                        if hasattr(created_at, "isoformat"):
                            item["created_at"] = created_at.isoformat()
                        elif created_at is None:
                            item["created_at"] = ""
                        else:
                            item["created_at"] = str(created_at)
                        # Keep values consistently numeric for the frontend.
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
        except (psycopg2.Error, ValueError, TypeError) as e:
            print(f"❌ Ошибка получения истории: {e}")
            return []

    def get_top_deposits(self, limit: int = 10) -> List[Dict]:
        """Получить топ донатеров по депозитам"""
        try:
            with self.get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT u.user_id, u.username, u.first_name,
                               COALESCE(ud.total_deposited, 0) as total_deposit
                        FROM users u
                        LEFT JOIN user_deposits ud ON u.user_id = ud.user_id
                        WHERE COALESCE(ud.total_deposited, 0) > 0
                        ORDER BY total_deposit DESC
                        LIMIT %s;
                    """, (limit,))
                    return [dict(r) for r in cursor.fetchall()]
        except psycopg2.Error as e:
            print(f"❌ Ошибка получения топа: {e}")
            return []

    def get_all_user_ids(self) -> list:
        """Получить список всех user_id для рассылки"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT user_id FROM users;")
                    return [r[0] for r in cursor.fetchall()]
        except psycopg2.Error as e:
            print(f"❌ Ошибка получения user_id: {e}")
            return []

    def get_all_users_count(self) -> int:
        """Получить количество пользователей"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) as cnt FROM users;")
                    row = cursor.fetchone()
                    return row[0] if row else 0
        except psycopg2.Error as e:
            print(f"❌ Ошибка подсчета пользователей: {e}")
            return 0

    def track_deposit(self, user_id: int, amount: int):
        """Отследить депозит пользователя"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM user_deposits WHERE user_id=%s;",
                        (user_id,)
                    )
                    existing = cursor.fetchone()
                    
                    if existing:
                        cursor.execute(
                            "UPDATE user_deposits SET total_deposited = total_deposited + %s WHERE user_id=%s;",
                            (amount, user_id)
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO user_deposits (user_id, total_deposited, first_deposit_at) VALUES (%s, %s, CURRENT_TIMESTAMP);",
                            (user_id, amount)
                        )
                    conn.commit()
        except psycopg2.Error as e:
            print(f"❌ Ошибка отслеживания депозита: {e}")

    def get_user_deposits(self, user_id: int) -> Optional[Dict]:
        """Получить информацию о депозитах пользователя"""
        try:
            with self.get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM user_deposits WHERE user_id=%s;",
                        (user_id,)
                    )
                    row = cursor.fetchone()
                    return dict(row) if row else None
        except psycopg2.Error as e:
            print(f"❌ Ошибка получения депозитов: {e}")
            return None

    def unlock_withdraw_for_user(self, user_id: int) -> bool:
        """Разблокировать вывод, даже если у пользователя ещё нет записи о депозитах."""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1 FROM users WHERE user_id=%s;", (user_id,))
                    if not cursor.fetchone():
                        return False
                    cursor.execute("""
                        INSERT INTO user_deposits (user_id, total_deposited, can_withdraw_unlock)
                        VALUES (%s, 0, TRUE)
                        ON CONFLICT (user_id) DO UPDATE SET can_withdraw_unlock = TRUE;
                    """, (user_id,))
                    conn.commit()
                    return True
        except psycopg2.Error as e:
            print(f"❌ Ошибка разблокировки вывода: {e}")
            return False

    def create_withdrawal_request(self, user_id: int, amount: int) -> int:
        """Создать запрос на вывод"""
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO withdrawals (user_id, amount) VALUES (%s, %s) RETURNING id;",
                        (user_id, amount)
                    )
                    request_id = cursor.fetchone()[0]
                    conn.commit()
                    return request_id
        except psycopg2.Error as e:
            print(f"❌ Ошибка создания запроса вывода: {e}")
            return -1

    def get_withdrawal_request(self, request_id: int) -> Optional[Dict]:
        """Получить информацию о запросе вывода"""
        try:
            with self.get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM withdrawals WHERE id=%s;",
                        (request_id,)
                    )
                    row = cursor.fetchone()
                    return dict(row) if row else None
        except psycopg2.Error as e:
            print(f"❌ Ошибка получения запроса вывода: {e}")
            return None

    def update_withdrawal_status(self, request_id: int, status: str) -> bool:
        """Атомарно перевести заявку из pending в новый статус.
        Возвращает True только если именно эта операция изменила pending-заявку.
        Это защищает от повторного одобрения/отказа после перезапуска и от двойного возврата.
        """
        if status not in {"approved", "rejected"}:
            return False
        try:
            with self.get_conn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE withdrawals SET status=%s WHERE id=%s AND status='pending' RETURNING id;",
                        (status, request_id)
                    )
                    changed = cursor.fetchone() is not None
                    conn.commit()
                    return changed
        except psycopg2.Error as e:
            print(f"❌ Ошибка обновления статуса вывода: {e}")
            return False

    def get_pending_withdrawals(self) -> List[Dict]:
        """Получить все ожидающие выводы"""
        try:
            with self.get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM withdrawals WHERE status='pending' ORDER BY created_at;"
                    )
                    return [dict(r) for r in cursor.fetchall()]
        except psycopg2.Error as e:
            print(f"❌ Ошибка получения ожидающих выводов: {e}")
            return []
