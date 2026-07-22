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

waiting_for_address = {}
processed_orders = set()

# Создаём отдельный экземпляр Bot для отправки сообщений
bot = Bot(token=TOKEN)

# Создаём Application для обработки команд (свой собственный цикл)
application = Application.builder().token(TOKEN).build()

# Инициализация Application
def init_app():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(application.initialize())
    finally:
        loop.close()
init_app()

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
        send_telegram_message(
            f"📍 Новый заказ печатной книги!\nПользователь: {user_id}\nАдрес ПВЗ: {address}"
        )
        await update.message.reply_text("✅ Спасибо! Ваш адрес передан. В ближайшее время с вами свяжутся для подтверждения заказа.")
        waiting_for_address[user_id] = False

# --- Регистрация обработчиков ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("electronic", order_electronic))
application.add_handler(CommandHandler("printed", order_printed))
application.add_handler(CommandHandler("both", order_both))
application.add_handler(CommandHandler("cancel", cancel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_address))

# --- Функция для синхронной отправки сообщений (использует отдельный bot) ---
def send_telegram_message(text):
    """Отправляет сообщение в Telegram (синхронно)"""
    async def send():
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    asyncio.run(send())

# --- Flask приложение ---
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.form.to_dict()
        logging.info(f"Получены данные из Tilda: {data}")

        # Приводим ключи к нижнему регистру для единообразия
        data_lower = {k.lower(): v for k, v in data.items()}

        order_id = data_lower.get('payment[orderid]')
        if order_id:
            if order_id in processed_orders:
                logging.info(f"Дубликат заказа {order_id} — игнорируем")
                return jsonify({"status": "duplicate ignored"}), 200
            processed_orders.add(order_id)

        client_name = data_lower.get('name', 'Не указано')
        client_phone = data_lower.get('phone', 'Не указано')
        client_email = data_lower.get('email', 'Не указано')
        client_city = data_lower.get('city', 'Не указано')

        products = []
        i = 0
        while True:
            name_key = f'payment[products][{i}][name]'
            if name_key not in data_lower:
                break
            product = {
                'name': data_lower.get(name_key, ''),
                'quantity': data_lower.get(f'payment[products][{i}][quantity]', '1'),
                'amount': data_lower.get(f'payment[products][{i}][amount]', '0'),
                'price': data_lower.get(f'payment[products][{i}][price]', '0'),
            }
            products.append(product)
            i += 1

        if not products:
            product_name = data_lower.get('product_name') or data_lower.get('product') or data_lower.get('payment[product][name]')
            if product_name:
                products.append({
                    'name': product_name,
                    'quantity': data_lower.get('quantity', '1'),
                    'amount': data_lower.get('amount') or data_lower.get('payment[amount]', '0'),
                    'price': data_lower.get('price') or data_lower.get('payment[amount]', '0'),
                })

        order_types = set()
        total_price = 0
        for p in products:
            amount_str = p.get('amount', '0')
            if amount_str and amount_str.isdigit():
                total_price += int(amount_str)
            name_lower = p['name'].lower()
            if 'электрон' in name_lower:
                order_types.add('electronic')
            if 'печатн' in name_lower:
                order_types.add('printed')

        if len(order_types) == 2:
            order_type = 'both'
        elif 'electronic' in order_types:
            order_type = 'electronic'
        elif 'printed' in order_types:
            order_type = 'printed'
        else:
            order_type = 'unknown'

        message = (
            f"📦 **Новый заказ книги!**\n"
            f"Клиент: {client_name}\n"
            f"Телефон: {client_phone}\n"
            f"Email: {client_email}\n"
            f"Город: {client_city}\n"
            f"Товары:\n"
        )
        for p in products:
            message += f"  - {p['name']} x{p['quantity']} = {p['amount']} руб.\n"
        message += f"Итого: {total_price} руб.\n"
        message += f"Тип заказа: {order_type}"

        send_telegram_message(message)

        if order_type == 'electronic':
            send_telegram_message("📌 **Электронная книга.** Отправьте файл на email клиента.")
        elif order_type == 'printed':
            send_telegram_message("📌 **Печатная книга.** Свяжитесь с клиентом для уточнения адреса ПВЗ.")
        elif order_type == 'both':
            send_telegram_message("📌 **Электронная + печатная.** Отправьте файл на email и свяжитесь для уточнения адреса.")
        else:
            send_telegram_message("⚠️ Не удалось определить тип заказа. Проверьте вручную.")

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logging.error(f"Ошибка в вебхуке: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@flask_app.route('/telegram-webhook', methods=['POST'])
def telegram_webhook():
    try:
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, application.bot)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(application.process_update(update))
        finally:
            loop.close()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logging.error(f"Ошибка в вебхуке Telegram: {e}")
        return jsonify({"status": "error"}), 500

@flask_app.route('/set-webhook', methods=['GET'])
def set_webhook():
    webhook_url = f"https://future-mission-book-bot.onrender.com/telegram-webhook"
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(application.bot.set_webhook(url=webhook_url))
        finally:
            loop.close()
        return jsonify({"status": "webhook set successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@flask_app.route('/')
def index():
    return "✅ Бот Future Mission Book Bot работает!", 200

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=10000)
