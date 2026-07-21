import logging
import os
import asyncio
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

logging.basicConfig(level=logging.INFO)

# Состояния пользователей (ждём ли адрес)
waiting_for_address = {}

# Создаём приложение
application = Application.builder().token(TOKEN).build()

# --- Обработчики команд ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для выдачи книги «О чём зудят твои таланты?».\n\n"
        "Доступные команды:\n"
        "/electronic — получить электронную книгу\n"
        "/printed — заказать печатную книгу (запрос адреса)\n"
        "/both — получить электронную и заказать печатную\n"
        "/cancel — отменить запрос адреса"
    )

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
        with open("book.txt", 'rb') as f:
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

        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"📍 Новый заказ печатной книги!\nПользователь: {user_id}\nАдрес ПВЗ: {address}"
        )

        await update.message.reply_text("✅ Спасибо! Ваш адрес передан. В ближайшее время с вами свяжутся для подтверждения заказа.")

        waiting_for_address[user_id] = False
    else:
        pass

# --- Регистрация обработчиков ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("electronic", order_electronic))
application.add_handler(CommandHandler("printed", order_printed))
application.add_handler(CommandHandler("both", order_both))
application.add_handler(CommandHandler("cancel", cancel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_address))

# --- Flask приложение ---
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Принимает данные из Tilda (для заказов)"""
    data = request.get_json()
    logging.info(f"Получены данные из Tilda: {data}")
    return jsonify({"status": "ok"}), 200

@flask_app.route('/telegram-webhook', methods=['POST'])
def telegram_webhook():
    """
    Принимает обновления от Telegram.
    Инициализируем приложение и обрабатываем каждое обновление в отдельном цикле.
    """
    try:
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, application.bot)

        # Инициализируем приложение (если ещё не инициализировано)
        # и обрабатываем обновление в одном событийном цикле
        async def process_update():
            await application.initialize()
            await application.process_update(update)
            await application.shutdown()

        asyncio.run(process_update())

        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logging.error(f"Ошибка в вебхуке Telegram: {e}")
        return jsonify({"status": "error"}), 500

@flask_app.route('/set-webhook', methods=['GET'])
def set_webhook():
    """Устанавливает вебхук для бота"""
    webhook_url = f"https://future-mission-book-bot.onrender.com/telegram-webhook"
    try:
        async def set_webhook_async():
            await application.initialize()
            await application.bot.set_webhook(url=webhook_url)
            await application.shutdown()

        asyncio.run(set_webhook_async())
        return jsonify({"status": "webhook set successfully"}), 200
    except Exception as e:
        logging.error(f"Ошибка установки вебхука: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=10000)
