import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ChatMemberHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters
)

# ==================== КОНФИГУРАЦИЯ ====================
TOKEN = "8083984129:AAH3-QEpXMkYb1bpgQpKniY39l6YkmHDXC0"  # Замените на токен вашего бота
DB_PATH = "users.db"

# Состояния для ConversationHandler
WAITING_FOR_TIME = 1

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
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id TEXT PRIMARY KEY,
                    kick_minutes INTEGER DEFAULT 43200,
                    warn_minutes INTEGER DEFAULT 41760
                )
            ''')
            
            conn.commit()
    
    def add_user(self, chat_id: str, user_id: str, username: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (chat_id, user_id, username, join_date, warned)
                VALUES (?, ?, ?, ?, 0)
            ''', (chat_id, user_id, username, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
    
    def get_users_to_kick(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT u.chat_id, u.user_id, u.username, u.join_date, s.kick_minutes
                FROM users u
                JOIN chat_settings s ON u.chat_id = s.chat_id
                WHERE u.warned = 1
            ''')
            
            users_to_kick = []
            for row in cursor.fetchall():
                chat_id, user_id, username, join_date_str, kick_minutes = row
                join_date = datetime.strptime(join_date_str, "%Y-%m-%d %H:%M:%S")
                
                if datetime.now() - join_date >= timedelta(minutes=kick_minutes):
                    users_to_kick.append((chat_id, user_id, username))
            
            return users_to_kick
    
    def get_users_to_warn(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT u.chat_id, u.user_id, u.username, u.join_date, s.warn_minutes
                FROM users u
                JOIN chat_settings s ON u.chat_id = s.chat_id
                WHERE u.warned = 0
            ''')
            
            users_to_warn = []
            for row in cursor.fetchall():
                chat_id, user_id, username, join_date_str, warn_minutes = row
                join_date = datetime.strptime(join_date_str, "%Y-%m-%d %H:%M:%S")
                
                warn_time = join_date + timedelta(minutes=warn_minutes)
                if datetime.now() >= warn_time:
                    users_to_warn.append((chat_id, user_id, username))
            
            return users_to_warn
    
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
    
    def set_chat_settings(self, chat_id: str, minutes: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            warn_minutes = int(minutes * 0.9)
            
            cursor.execute('''
                INSERT OR REPLACE INTO chat_settings (chat_id, kick_minutes, warn_minutes)
                VALUES (?, ?, ?)
            ''', (chat_id, minutes, warn_minutes))
            conn.commit()
    
    def get_chat_settings(self, chat_id: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT kick_minutes, warn_minutes 
                FROM chat_settings 
                WHERE chat_id = ?
            ''', (chat_id,))
            result = cursor.fetchone()
            
            if result:
                return result
            else:
                return (43200, 41760)

# ==================== ФОНОВАЯ ПРОВЕРКА ====================
async def check_and_process_users(application):
    """Фоновая проверка пользователей"""
    print("🔄 Фоновая проверка запущена")
    while True:
        try:
            db = Database()
            
            # Проверяем предупреждения
            users_to_warn = db.get_users_to_warn()
            for chat_id, user_id, username in users_to_warn:
                try:
                    await application.bot.send_message(
                        chat_id=int(chat_id),
                        text=f"⚠️ Внимание, {username}!\nСкоро истечёт время с момента входа в чат.\nЕсли вы неактивны, то будете исключены."
                    )
                    db.mark_as_warned(chat_id, user_id)
                    print(f"⚠️ Предупреждение отправлено {username}")
                    await asyncio.sleep(0.1)
                except Exception as e:
                    print(f"Ошибка предупреждения {username}: {e}")
            
            # Проверяем исключения
            users_to_kick = db.get_users_to_kick()
            for chat_id, user_id, username in users_to_kick:
                try:
                    await application.bot.ban_chat_member(
                        chat_id=int(chat_id),
                        user_id=int(user_id)
                    )
                    
                    await application.bot.unban_chat_member(
                        chat_id=int(chat_id),
                        user_id=int(user_id)
                    )
                    
                    await application.bot.send_message(
                        chat_id=int(chat_id),
                        text=f"🚫 Пользователь {username} исключён из чата.\nПричина: истекло время с момента входа."
                    )
                    
                    db.remove_user(chat_id, user_id)
                    print(f"🚫 Пользователь {username} исключён")
                    await asyncio.sleep(0.1)
                except Exception as e:
                    print(f"Ошибка исключения {username} из {chat_id}: {e}")
        
        except Exception as e:
            print(f"❌ Ошибка в проверке: {e}")
        
        await asyncio.sleep(60)  # Проверка каждую минуту

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def on_user_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик входа нового участника"""
    db = Database()
    
    if not update.chat_member.new_chat_member:
        return
    
    chat = update.effective_chat
    user = update.chat_member.new_chat_member.user
    
    if user.is_bot:
        return
    
    old_status = update.chat_member.old_chat_member.status
    if old_status not in ["left", "kicked"]:
        return
    
    db.add_user(str(chat.id), str(user.id), user.username or user.full_name)
    
    kick_minutes, warn_minutes = db.get_chat_settings(str(chat.id))
    kick_date = datetime.now() + timedelta(minutes=kick_minutes)
    
    if kick_minutes < 60:
        time_str = f"{kick_minutes} мин."
    elif kick_minutes < 1440:
        time_str = f"{kick_minutes // 60} час. {kick_minutes % 60} мин."
    else:
        days = kick_minutes // 1440
        time_str = f"{days} дн."
    
    await context.bot.send_message(
        chat_id=chat.id,
        text=f"👋 Привет, {user.mention_html()}!\n\n"
             f"⚠️ В этом чате действует правило: через {time_str} неактивные участники исключаются.\n"
             f"📅 Дата исключения: {kick_date.strftime('%d.%m.%Y %H:%M')}\n"
             f"💡 Чтобы остаться, будьте активны в чате!",
        parse_mode="HTML"
    )

async def set_time_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /settime - начало настройки времени"""
    chat = update.effective_chat
    
    if chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах")
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    chat_member = await context.bot.get_chat_member(chat.id, user_id)
    if chat_member.status not in ["creator", "administrator"]:
        await update.message.reply_text("Только администраторы могут настраивать время")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "⏰ Настройка времени до исключения\n\n"
        "Отправьте время в одном из форматов:\n"
        "• Число минут (например: 5)\n"
        "• Часы:минуты (например: 2:30)\n"
        "• Просто дни (например: 30d или 30д)\n\n"
        "Диапазон: от 1 минуты до 43200 минут (30 дней)\n"
        "Для отмены отправьте /cancel"
    )
    return WAITING_FOR_TIME

async def process_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка введённого времени"""
    db = Database()
    text = update.message.text.strip().lower()
    chat = update.effective_chat
    
    try:
        minutes = 0
        
        if 'd' in text or 'д' in text:
            days = int(''.join(filter(str.isdigit, text)))
            if days < 1 or days > 30:
                await update.message.reply_text("❌ Количество дней должно быть от 1 до 30")
                return WAITING_FOR_TIME
            minutes = days * 1440
        elif ':' in text:
            parts = text.split(':')
            if len(parts) == 2:
                hours = int(parts[0])
                mins = int(parts[1])
                minutes = hours * 60 + mins
            else:
                await update.message.reply_text("❌ Неверный формат. Пример: 2:30")
                return WAITING_FOR_TIME
        else:
            minutes = int(text)
        
        if minutes < 1 or minutes > 43200:
            await update.message.reply_text("❌ Время должно быть от 1 минуты до 43200 минут (30 дней)")
            return WAITING_FOR_TIME
        
        db.set_chat_settings(str(chat.id), minutes)
        
        if minutes < 60:
            time_str = f"{minutes} мин."
        elif minutes < 1440:
            time_str = f"{minutes // 60} час. {minutes % 60} мин."
        else:
            days = minutes // 1440
            hours = (minutes % 1440) // 60
            time_str = f"{days} дн. {hours} час."
        
        await update.message.reply_text(
            f"✅ Настройки сохранены!\n"
            f"⏰ Время до исключения: {time_str}\n"
            f"⚠️ Предупреждение будет отправлено за 10% времени до кика."
        )
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Используйте:\n"
            "• 5 - минуты\n"
            "• 2:30 - часы:минуты\n"
            "• 30d - дни\n"
            "Для отмены /cancel"
        )
        return WAITING_FOR_TIME

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена настройки времени"""
    await update.message.reply_text("❌ Настройка времени отменена")
    return ConversationHandler.END

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /list - показать список пользователей"""
    chat = update.effective_chat
    
    if chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах")
        return
    
    user_id = update.effective_user.id
    chat_member = await context.bot.get_chat_member(chat.id, user_id)
    if chat_member.status not in ["creator", "administrator"]:
        await update.message.reply_text("Только администраторы могут использовать эту команду")
        return
    
    db = Database()
    users = db.get_chat_users(str(chat.id))
    kick_minutes, warn_minutes = db.get_chat_settings(str(chat.id))
    
    if not users:
        await update.message.reply_text("В базе пока нет пользователей")
        return
    
    message = "📋 Список пользователей:\n\n"
    
    for user_id, username, join_date_str, warned in users:
        join_date = datetime.strptime(join_date_str, "%Y-%m-%d %H:%M:%S")
        time_left = kick_minutes - (datetime.now() - join_date).total_seconds() / 60
        status = "⚠️" if warned else "✅"
        
        if time_left > 0:
            if time_left < 60:
                time_str = f"{int(time_left)} мин."
            elif time_left < 1440:
                time_str = f"{int(time_left // 60)} час."
            else:
                time_str = f"{int(time_left // 1440)} дн."
        else:
            time_str = "просрочен"
            status = "🚫"
        
        message += f"{status} {username} - ост. {time_str}\n"
    
    await update.message.reply_text(message)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /settings - показать текущие настройки чата"""
    chat = update.effective_chat
    
    if chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах")
        return
    
    db = Database()
    kick_minutes, warn_minutes = db.get_chat_settings(str(chat.id))
    
    if kick_minutes < 60:
        kick_str = f"{kick_minutes} минут"
    elif kick_minutes < 1440:
        kick_str = f"{kick_minutes // 60} час. {kick_minutes % 60} мин."
    else:
        days = kick_minutes // 1440
        hours = (kick_minutes % 1440) // 60
        kick_str = f"{days} дн. {hours} час."
    
    await update.message.reply_text(
        f"⚙️ Настройки чата:\n\n"
        f"⏰ Время до кика: {kick_str}\n"
        f"⚠️ Предупреждение за: {int(warn_minutes)} мин. до кика\n\n"
        f"Для изменения используйте /settime"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = (
        "🤖 Бот автоматического контроля участников\n\n"
        "👑 Команды для администраторов:\n"
        "/settime - настроить время до исключения (1 мин. - 30 дн.)\n"
        "/settings - показать текущие настройки\n"
        "/list - список пользователей в чате\n"
        "/help - это сообщение\n\n"
        "⏰ Форматы времени для /settime:\n"
        "• 5 - минуты\n"
        "• 2:30 - часы:минуты\n"
        "• 30d - дни\n\n"
        "🤖 Автоматические действия:\n"
        "• Добавление новых участников в базу\n"
        "• Предупреждение перед исключением\n"
        "• Исключение по истечении времени"
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
    
    # ConversationHandler для настройки времени
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("settime", set_time_command)],
        states={
            WAITING_FOR_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_time)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    
    application.add_handler(conv_handler)
    application.add_handler(ChatMemberHandler(on_user_join, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(CommandHandler("list", list_users))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Запускаем фоновую проверку
    loop = asyncio.get_event_loop()
    loop.create_task(check_and_process_users(application))
    
    print("🚀 Бот запущен!")
    application.run_polling()

if __name__ == "__main__":
    db = Database()
    main()
