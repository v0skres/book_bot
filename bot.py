import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))
BOOK_FILE = os.getenv("BOOK_FILE_PATH", "book.pdf")

logging.basicConfig(level=logging.INFO)

waiting_for_address = {}

# --- Обработчики команд (оставляем как было) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(...)

# --- Команды ---
async def order_electronic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await send_electronic_book(update, context, user_id)

async def order_printed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    waiting_for_address[user_id] = True
    await context.bot.send_message(
        chat_id=user_id,
        text="📦 Для отправки печатной версии книги, пожалуйста, напишите адрес ближайшего пункта выдачи заказов (ПВЗ).\n\n"
             "Чтобы отменить, отправьте /cancel"
    )

async def order_both(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await send_electronic_book(update, context, user_id)
    waiting_for_address[user_id] = True
    await context.bot.send_message(
        chat_id=user_id,
        text="📦 Теперь для печатной версии книги, пожалуйста, напишите адрес ближайшего пункта выдачи заказов (ПВЗ).\n\n"
             "Чтобы отменить, отправьте /cancel"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    waiting_for_address[user_id] = False
    await update.message.reply_text("❌ Операция отменена.")

# --- Основные функции ---
async def send_electronic_book(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        with open("book.pdf", 'rb') as f:
            await context.bot.send_document(
                chat_id=user_id,
                document=f,
                caption="📖 Вот ваша электронная книга «О чём зудят твои таланты?». Приятного чтения!"
            )
        logging.info(f"Книга отправлена пользователю {user_id}")
    except FileNotFoundError:
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ К сожалению, файл книги временно недоступен. Мы уже работаем над этим!"
        )

async def handle_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = update.message.text

    if waiting_for_address.get(user_id, False):
        logging.info(f"Адрес получен от {user_id}: {address}")

        await update.message.reply_text(
            f"✅ Адрес получен: {address}\n(в реальности это уйдёт руководителю)"
        )

        # Отправка руководителю
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"📍 Новый заказ печатной книги!\nПользователь: {user_id}\nАдрес ПВЗ: {address}"
        )

        waiting_for_address[user_id] = False
    else:
        pass

# --- Главная функция бота ---
def main():
    # Создаём приложение
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    application = ApplicationBuilder().token(TOKEN).request(request).build()

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("electronic", order_electronic))
    application.add_handler(CommandHandler("printed", order_printed))
    application.add_handler(CommandHandler("both", order_both))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_address))

    # Получаем порт из переменной окружения (Render задаёт PORT)
    port = int(os.environ.get('PORT', 10000))
    
    # Запускаем бота в режиме вебхука
    application.run_webhook(
        listen='0.0.0.0',
        port=port,
        url_path=TOKEN,  # Путь для вебхука (можно любой)
        webhook_url=f'https://future-mission-book-bot.onrender.com/{TOKEN}'  # URL твоего бота + токен
    )

if __name__ == "__main__":
    main()
