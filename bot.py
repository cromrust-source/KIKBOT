import sqlite3
import os
from datetime import datetime, timedelta, date
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ChatMemberHandler,
    ContextTypes
)

# ==================== КОНФИГУРАЦИЯ ====================
TOKEN = "8083984129:AAH3-QEpXMkYb1bpgQpKniY39l6YkmHDXC0"  # Замените на токен вашего бота
DB_PATH = "users.db"
KICK_AFTER_DAYS = 30
WARN_BEFORE_DAYS = 29

# ==================== БАЗА ДАННЫХ ====================
class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_database()
    
    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def init_database(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    username TEXT,
                    join_date TEXT NOT NULL,
                    warned INTEGER DEFAULT 0,
                    UNIQUE(chat_id, user_id)
                )
            ''')
            conn.commit()
    
    def add_user(self, chat_id: str, user_id: str, username: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (chat_id, user_id, username, join_date, warned)
                VALUES (?, ?, ?, ?, 0)
            ''', (chat_id, user_id, username, datetime.now().strftime("%Y-%m-%d")))
            conn.commit()
    
    def get_users_to_kick(self, days: int = KICK_AFTER_DAYS):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            target_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            cursor.execute('''
                SELECT chat_id, user_id, username 
                FROM users 
                WHERE join_date <= ? AND warned = 1
            ''', (target_date,))
            return cursor.fetchall()
    
    def get_users_to_warn(self, days: int = WARN_BEFORE_DAYS):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            target_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            cursor.execute('''
                SELECT chat_id, user_id, username 
                FROM users 
                WHERE join_date = ? AND warned = 0
            ''', (target_date,))
            return cursor.fetchall()
    
    def mark_as_warned(self, chat_id: str, user_id: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET warned = 1 
                WHERE chat_id = ? AND user_id = ?
            ''', (chat_id, user_id))
            conn.commit()
    
    def remove_user(self, chat_id: str, user_id: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM users 
                WHERE chat_id = ? AND user_id = ?
            ''', (chat_id, user_id))
            conn.commit()
    
    def get_chat_users(self, chat_id: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, username, join_date, warned 
                FROM users 
                WHERE chat_id = ?
                ORDER BY join_date
            ''', (chat_id,))
            return cursor.fetchall()

# ==================== ОБРАБОТЧИКИ ====================
async def on_user_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик входа нового участника"""
    db = Database()
    
    # Проверяем, что это добавление в группу
    if not update.chat_member.new_chat_member:
        return
    
    chat = update.effective_chat
    user = update.chat_member.new_chat_member.user
    
    # Пропускаем ботов
    if user.is_bot:
        return
    
    # Проверяем, что пользователь только что присоединился
    old_status = update.chat_member.old_chat_member.status
    if old_status not in ["left", "kicked"]:
        return
    
    # Сохраняем в базу
    db.add_user(str(chat.id), str(user.id), user.username or user.full_name)
    
    # Вычисляем дату исключения
    kick_date = (datetime.now() + timedelta(days=KICK_AFTER_DAYS)).strftime("%d.%m.%Y")
    
    # Приветственное сообщение
    await context.bot.send_message(
        chat_id=chat.id,
        text=f"👋 Привет, {user.mention_html()}!\n\n"
             f"⚠️ В этом чате действует правило: через {KICK_AFTER_DAYS} дней неактивные участники исключаются.\n"
             f"📅 Дата возможного исключения: {kick_date}\n"
             f"💡 Чтобы остаться, будьте активны в чате!",
        parse_mode="HTML"
    )

async def warn_users(context: ContextTypes.DEFAULT_TYPE):
    """Отправка предупреждений за день до исключения"""
    db = Database()
    users_to_warn = db.get_users_to_warn()
    
    for chat_id, user_id, username in users_to_warn:
        try:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=f"⚠️ Внимание, {username}!\n"
                     f"Завтра истекает {KICK_AFTER_DAYS} дней с момента входа в чат.\n"
                     f"Если вы неактивны, то будете исключены."
            )
            db.mark_as_warned(chat_id, user_id)
        except Exception as e:
            print(f"Ошибка предупреждения {username}: {e}")

async def kick_users(context: ContextTypes.DEFAULT_TYPE):
    """Исключение пользователей, у которых прошло 30 дней"""
    db = Database()
    users_to_kick = db.get_users_to_kick()
    
    for chat_id, user_id, username in users_to_kick:
        try:
            # Кикаем пользователя
            await context.bot.ban_chat_member(
                chat_id=int(chat_id),
                user_id=int(user_id)
            )
            
            # Разбаниваем (чтобы мог вернуться по ссылке)
            await context.bot.unban_chat_member(
                chat_id=int(chat_id),
                user_id=int(user_id)
            )
            
            # Уведомление в чат
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=f"🚫 Пользователь {username} исключён из чата.\n"
                     f"Причина: прошло {KICK_AFTER_DAYS} дней с момента входа."
            )
            
            # Удаляем из базы
            db.remove_user(chat_id, user_id)
            
        except Exception as e:
            print(f"Ошибка исключения {username} из {chat_id}: {e}")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /list - показать список пользователей"""
    chat = update.effective_chat
    
    # Проверяем, что команда в группе
    if chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах")
        return
    
    # Проверяем права (только админы)
    user_id = update.effective_user.id
    chat_member = await context.bot.get_chat_member(chat.id, user_id)
    if chat_member.status not in ["creator", "administrator"]:
        await update.message.reply_text("Только администраторы могут использовать эту команду")
        return
    
    db = Database()
    users = db.get_chat_users(str(chat.id))
    
    if not users:
        await update.message.reply_text("В базе пока нет пользователей")
        return
    
    today = date.today()
    message = "📋 Список пользователей:\n\n"
    
    for user_id, username, join_date_str, warned in users:
        join_date = datetime.strptime(join_date_str, "%Y-%m-%d").date()
        days_left = KICK_AFTER_DAYS - (today - join_date).days
        status = "⚠️" if warned else "✅"
        
        message += f"{status} {username} - {join_date_str}"
        if days_left > 0:
            message += f" (осталось {days_left} дн.)"
        else:
            message += " (будет исключён)"
        message += "\n"
    
    await update.message.reply_text(message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = (
        "🤖 Бот автоматического контроля участников\n\n"
        "Команды (для админов):\n"
        "/list - список пользователей в чате\n"
        "/help - это сообщение\n\n"
        "Автоматические действия:\n"
        f"• Добавление новых участников в базу\n"
        f"• Предупреждение за 1 день до исключения\n"
        f"• Исключение через {KICK_AFTER_DAYS} дней\n"
    )
    await update.message.reply_text(help_text)

# ==================== ЗАПУСК БОТА ====================
def main():
    """Запуск бота"""
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Ошибка: укажите токен бота в переменной TOKEN")
        return
    
    # Создаём приложение
    application = Application.builder().token(TOKEN).build()
    
    # Обработчик входа новых участников
    application.add_handler(ChatMemberHandler(on_user_join, ChatMemberHandler.CHAT_MEMBER))
    
    # Команды
    application.add_handler(CommandHandler("list", list_users))
    application.add_handler(CommandHandler("help", help_command))
    
    # Планировщик задач
    job_queue = application.job_queue
    
    # Проверка предупреждений каждый день в 12:00
    job_queue.run_daily(
        warn_users,
        time=datetime.time(hour=12, minute=0),
        days=(0, 1, 2, 3, 4, 5, 6)
    )
    
    # Проверка исключений каждый день в 12:30
    job_queue.run_daily(
        kick_users,
        time=datetime.time(hour=12, minute=30),
        days=(0, 1, 2, 3, 4, 5, 6)
    )
    
    # Запускаем бота
    print("🚀 Бот запущен!")
    application.run_polling()

if __name__ == "__main__":
    # Инициализация базы при запуске
    db = Database()
    main()
