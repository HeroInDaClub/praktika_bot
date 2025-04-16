import asyncio
import asyncpg
import json
import re
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.bot import DefaultBotProperties


def clean_product_name(name: str) -> str:
    return name.strip()


class DatabaseManager:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool = None

    async def initialize(self):
        self.pool = await asyncpg.create_pool(dsn=self.dsn)
        try:
            async with self.pool.acquire() as connection:
                await connection.execute("SELECT 1 FROM products LIMIT 1;")
        except Exception as e:
            print("Ошибка доступа к таблице 'products'. Проверьте, что таблица создана.", e)

    async def insert_product(self, product_name: str):
        try:
            async with self.pool.acquire() as connection:
                await connection.execute("INSERT INTO products (name) VALUES ($1);", product_name)
        except Exception as e:
            print("Ошибка вставки продукта:", e)

    async def find_products(self, search_query: str):
        try:
            async with self.pool.acquire() as connection:
                # Устанавливаем порог похожести
                await connection.execute("SELECT set_limit(0.1);")
                return await connection.fetch(
                    """
                    SELECT id, name, similarity(name, $1) AS sim
                    FROM products
                    WHERE name % $1
                    ORDER BY sim DESC;
                    """,
                    search_query
                )
        except Exception as e:
            print("Ошибка поиска товаров:", e)
            return []


#async def populate_database_from_json(db_manager: DatabaseManager, json_path: str):
#    try:
#        with open(json_path, "r", encoding="utf-8") as f:
#            products = json.load(f)
#        count = 0
#        for product in products:
#            await db_manager.insert_product(clean_product_name(product))
#            count += 1
#        # После вставки всех записей обновляем поле search_vector для тех записей, где оно пустое
#        async with db_manager.pool.acquire() as connection:
#            await connection.execute(
#                "UPDATE products SET search_vector = to_tsvector('russian', name) WHERE search_vector IS NULL;"
#            )
#        print(f"Inserted {count} products into the database and computed search vectors.")
#    except Exception as e:
#        print("Ошибка заполнения базы данных из JSON:", e)



class AddProductState(StatesGroup):
    waitingForProductName = State()


class SearchProductState(StatesGroup):
    waitingForSearchQuery = State()


async def start_command(message: types.Message):
    await message.answer(
        "Введите /add для добавления товара, /search для поиска или используйте inline-поиск, упомянув бота."
    )


async def add_command(message: types.Message, state: FSMContext):
    await message.answer("Введите название товара:")
    await state.set_state(AddProductState.waitingForProductName)


async def search_command(message: types.Message, state: FSMContext):
    await message.answer("Введите запрос для поиска товара:")
    await state.set_state(SearchProductState.waitingForSearchQuery)


async def process_product_name(message: types.Message, state: FSMContext, db_manager: DatabaseManager):
    product_name = clean_product_name(message.text)
    await db_manager.insert_product(product_name)
    await message.answer(f"Товар '{product_name}' добавлен.")
    await state.clear()


async def process_search_query(message: types.Message, state: FSMContext, db_manager: DatabaseManager):
    query = message.text.strip()
    records = await db_manager.find_products(query)
    if records:
        response = "\n".join([
            f"{r['id']}: {r['name']}\n(Совпадение: {r['sim'] * 100:.2f}%)"
            for r in records
        ])
    else:
        response = "Товары не найдены."
    await message.answer(response)
    await state.clear()


def create_process_product_name_handler(db_manager: DatabaseManager):
    async def handler(message: types.Message, state: FSMContext):
        await process_product_name(message, state, db_manager)

    return handler


def create_process_search_query_handler(db_manager: DatabaseManager):
    async def handler(message: types.Message, state: FSMContext):
        await process_search_query(message, state, db_manager)

    return handler


def create_inline_query_handler(db_manager: DatabaseManager):
    async def handler(inline_query: types.InlineQuery):
        query = inline_query.query.strip()
        if query:
            records = await db_manager.find_products(query)
        else:
            records = []
        if records:
            results = [
                types.InlineQueryResultArticle(
                    id=str(record['id']),
                    title=record['name'],
                    description=f"Совпадение: {record['sim'] * 100:.2f}%",
                    input_message_content=types.InputTextMessageContent(
                        message_text=f"{record['name']}\n(Совпадение: {record['sim'] * 100:.2f}%)"
                    )
                )
                for record in records
            ]
            results = results[:50]
        else:
            results = [
                types.InlineQueryResultArticle(
                    id="add_product",
                    title="Добавить новый товар",
                    input_message_content=types.InputTextMessageContent(message_text="Добавить новый товар"),
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text="Добавить", callback_data="add_product")]
                    ])
                )
            ]
        try:
            await inline_query.answer(results, cache_time=1)
        except Exception as e:
            print("Ошибка при ответе на inline запрос:", e)

    return handler


async def callback_add_product(callback_query: types.CallbackQuery, state: FSMContext, bot: Bot):
    await callback_query.answer()
    if callback_query.message:
        await callback_query.message.answer("Введите название товара для добавления:")
    else:
        await bot.send_message(callback_query.from_user.id, "Введите название товара для добавления:")
    await state.set_state(AddProductState.waitingForProductName)


def create_callback_add_product_handler(bot: Bot):
    async def handler(callback_query: types.CallbackQuery, state: FSMContext):
        await callback_add_product(callback_query, state, bot)

    return handler


MIGRATE_DATA = False  # Измените на True для выполнения миграции данных из JSON


async def main():
    db_manager = DatabaseManager("postgresql://postgres:Nikito4ka777@127.0.0.1:5432/database?sslmode=disable")
    await db_manager.initialize()
    #if MIGRATE_DATA:
        # Если нужно выполнить миграцию, раскомментируйте следующую строку:
        #await populate_database_from_json(db_manager, "cleaned_products.json")

    default_bot_props = DefaultBotProperties(parse_mode="HTML")
    bot = Bot(token="7431345797:AAHla9cdL2pxqQ0suM8B2FAnl_KxtRqRayw", default=default_bot_props)
    await bot.delete_webhook()
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.message.register(start_command, Command("start"))
    dp.message.register(add_command, Command("add"))
    dp.message.register(search_command, Command("search"))
    dp.message.register(create_process_product_name_handler(db_manager), AddProductState.waitingForProductName)
    dp.message.register(create_process_search_query_handler(db_manager), SearchProductState.waitingForSearchQuery)
    dp.inline_query.register(create_inline_query_handler(db_manager))
    dp.callback_query.register(create_callback_add_product_handler(bot), lambda cq: cq.data == "add_product")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
