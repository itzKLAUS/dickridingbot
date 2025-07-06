# database.py
import sqlite3
from datetime import datetime

DATABASE_NAME = 'game_data.db'

def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            stat_value INTEGER DEFAULT 0,
            last_grow_time TEXT,
            suck_count INTEGER DEFAULT 0,
            last_suck_time TEXT,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            win_streak INTEGER DEFAULT 0,
            max_win_streak INTEGER DEFAULT 0,
            UNIQUE(user_id, chat_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            challenger_id INTEGER NOT NULL,
            challenger_name TEXT NOT NULL,
            bet_amount INTEGER NOT NULL,
            challenged_id INTEGER,
            creation_time TEXT DEFAULT (datetime('now')),
            is_active INTEGER DEFAULT 1,
            message_id INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            value INTEGER NOT NULL,
            uses_remaining INTEGER DEFAULT -1 -- -1 for unlimited
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS redeemed_codes (
            user_id INTEGER,
            code TEXT,
            redeemed_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, code)
        )
    ''')
    conn.commit()
    conn.close()

def upsert_player(user_id, chat_id, username, first_name, stat_value, last_grow_time, suck_count, last_suck_time, wins=None, losses=None, win_streak=None, max_win_streak=None):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    player = get_player(user_id, chat_id)
    if player:
        cursor.execute('''
            UPDATE players
            SET username=?, first_name=?, stat_value=?, last_grow_time=?, suck_count=?, last_suck_time=?,
                wins=COALESCE(?, wins), losses=COALESCE(?, losses), win_streak=COALESCE(?, win_streak), max_win_streak=COALESCE(?, max_win_streak)
            WHERE user_id=? AND chat_id=?
        ''', (username, first_name, stat_value, last_grow_time, suck_count, last_suck_time, wins, losses, win_streak, max_win_streak, user_id, chat_id))
    else:
        cursor.execute('''
            INSERT INTO players (user_id, chat_id, username, first_name, stat_value, last_grow_time, suck_count, last_suck_time, wins, losses, win_streak, max_win_streak)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0), COALESCE(?, 0), COALESCE(?, 0), COALESCE(?, 0))
        ''', (user_id, chat_id, username, first_name, stat_value, last_grow_time, suck_count, last_suck_time, wins, losses, win_streak, max_win_streak))
    conn.commit()
    conn.close()

def get_player(user_id, chat_id):
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE user_id=? AND chat_id=?", (user_id, chat_id,))
    result = cursor.fetchone()
    conn.close()
    if result: return dict(result)
    return None

def get_player_by_username(username, chat_id):
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE username LIKE ? AND chat_id=?", (username, chat_id,))
    result = cursor.fetchone()
    conn.close()
    if result: return dict(result)
    return None

def get_leaderboard(chat_id):
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT first_name, stat_value, user_id FROM players WHERE chat_id=? ORDER BY stat_value DESC", (chat_id,))
    results = cursor.fetchall()
    conn.close()
    return [dict(row) for row in results]

def create_challenge(chat_id, challenger_id, challenger_name, bet_amount, challenged_id=None):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO challenges (chat_id, challenger_id, challenger_name, bet_amount, challenged_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (chat_id, challenger_id, challenger_name, bet_amount, challenged_id))
    challenge_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return challenge_id

def get_challenge(challenge_id):
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM challenges WHERE id=?", (challenge_id,))
    result = cursor.fetchone()
    conn.close()
    if result: return dict(result)
    return None

def deactivate_challenge(challenge_id):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE challenges SET is_active=0 WHERE id=?", (challenge_id,))
    conn.commit()
    conn.close()

def update_challenge_message_id(challenge_id, message_id):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE challenges SET message_id=? WHERE id=?", (message_id, challenge_id,))
    conn.commit()
    conn.close()

def create_promo_code(code, value):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO promo_codes (code, value) VALUES (?, ?)", (code, value))
    conn.commit()
    conn.close()

def get_promo_code(code):
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM promo_codes WHERE code=?", (code,))
    result = cursor.fetchone()
    conn.close()
    if result: return dict(result)
    return None

def has_user_redeemed_code(user_id, code):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM redeemed_codes WHERE user_id=? AND code=?", (user_id, code,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def mark_code_as_redeemed(user_id, code):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO redeemed_codes (user_id, code) VALUES (?, ?)", (user_id, code))
    conn.commit()
    conn.close()
