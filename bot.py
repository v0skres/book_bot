import logging
import os
import asyncio
import threading
from flask import Flask, request, jsonify
from telegram import Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

logging.basicConfig(level=logging.INFO)

# Хранилище обработанных заказов (защита от дублей)
processed_orders = set()

# Создаём отдельный экземпляр Bot для отправки сообщений из вебхука
bot_for_webhook = Bot(token=TOKEN)

# --- Функция для синхронной отправки сообщений ---
def send_telegram_message(text):
    """Отправляет сообщение в Telegram (синхронно)"""
    try:
        asyncio.run(bot.send_message(chat_id=ADMIN_CHAT_ID, text=text))
    except Exception as e:
        logging.error(f"Ошибка отправки сообщения: {e}")

# --- Обработчики команд для бота (polling) ---
async def start(update, context):
    await update.message.reply_text(
        "👋 Привет! Я бот для выдачи книги «О чём зудят твои таланты?».\n\n"
        "Доступные команды:\n"
        "/electronic — получить электронную книгу\n"
        "/printed — заказать печатную книгу (запрос адреса)\n"
        "/both — получить электронную и заказать печатную\n"
        "/cancel — отменить запрос адреса"
    )

async def order_electronic(update, context):
    user_id = update.effective_user.id
    await send_electronic_book(update, context, user_id)

async def order_printed(update, context):
    user_id = update.effective_user.id
    context.user_data['waiting_for_address'] = True
    await context.bot.send_message(
        chat_id=user_id,
        text="📦 Для отправки печатной версии книги, пожалуйста, напишите адрес ближайшего пункта выдачи заказов (ПВЗ).\n\n"
             "Чтобы отменить, отправьте /cancel"
    )

async def order_both(update, context):
    user_id = update.effective_user.id
    await send_electronic_book(update, context, user_id)
    context.user_data['waiting_for_address'] = True
    await context.bot.send_message(
        chat_id=user_id,
        text="📦 Теперь для печатной версии книги, пожалуйста, напишите адрес ближайшего пункта выдачи заказов (ПВЗ).\n\n"
             "Чтобы отменить, отправьте /cancel"
    )

async def cancel(update, context):
    user_id = update.effective_user.id
    context.user_data['waiting_for_address'] = False
    await update.message.reply_text("❌ Операция отменена.")

async def send_electronic_book(update, context, user_id):
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

async def handle_address(update, context):
    user_id = update.effective_user.id
    address = update.message.text
    if context.user_data.get('waiting_for_address', False):
        logging.info(f"Адрес получен от {user_id}: {address}")
        send_telegram_message(
            f"📍 Новый заказ печатной книги!\nПользователь: {user_id}\nАдрес ПВЗ: {address}"
        )
        await update.message.reply_text("✅ Спасибо! Ваш адрес передан. В ближайшее время с вами свяжутся для подтверждения заказа.")
        context.user_data['waiting_for_address'] = False

# --- Создаём приложение для бота (polling) ---
app_bot = Application.builder().token(TOKEN).build()
app_bot.add_handler(CommandHandler("start", start))
app_bot.add_handler(CommandHandler("electronic", order_electronic))
app_bot.add_handler(CommandHandler("printed", order_printed))
app_bot.add_handler(CommandHandler("both", order_both))
app_bot.add_handler(CommandHandler("cancel", cancel))
app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_address))

# --- Flask приложение для вебхуков от Tilda ---
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.form.to_dict()
        logging.info(f"Получены данные из Tilda: {data}")

        # Защита от дублей
        order_id = data.get('payment[orderid]')
        if order_id:
            if order_id in processed_orders:
                logging.info(f"Дубликат заказа {order_id} — игнорируем")
                return jsonify({"status": "duplicate ignored"}), 200
            processed_orders.add(order_id)

        # Извлекаем данные
        client_name = data.get('name', 'Не указано')
        client_phone = data.get('Phone', 'Не указано')
        client_email = data.get('Email', 'Не указано')
        client_city = data.get('city', 'Не указано')

        # Извлекаем товары
        products = []
        i = 0
        while True:
            name_key = f'payment[products][{i}][name]'
            if name_key not in data:
                break
            product = {
                'name': data.get(name_key, ''),
                'quantity': data.get(f'payment[products][{i}][quantity]', '1'),
                'amount': data.get(f'payment[products][{i}][amount]', '0'),
                'price': data.get(f'payment[products][{i}][price]', '0'),
            }
            products.append(product)
            i += 1

        if not products:
            product_name = data.get('product_name') or data.get('product') or data.get('payment[product][name]')
            if product_name:
                products.append({
                    'name': product_name,
                    'quantity': data.get('quantity', '1'),
                    'amount': data.get('amount') or data.get('payment[amount]', '0'),
                    'price': data.get('price') or data.get('payment[amount]', '0'),
                })

        # Определяем тип заказа
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

@flask_app.route('/')
def index():
    return "✅ Бот Future Mission Book Bot работает!", 200

# --- Запуск бота (polling) и Flask-сервера в разных потоках ---
def run_bot():
    """Запускает бота в режиме polling"""
    logging.info("Запуск бота (polling)...")
    app_bot.run_polling()

def run_flask():
    """Запускает Flask-сервер для вебхуков Tilda"""
    logging.info("Запуск Flask-сервера...")
    flask_app.run(host='0.0.0.0', port=10000)

if __name__ == "__main__":
    # Запускаем бота и Flask в разных потоках
    bot_thread = threading.Thread(target=run_bot)
    flask_thread = threading.Thread(target=run_flask)
    bot_thread.start()
    flask_thread.start()
    bot_thread.join()
    flask_thread.join()
