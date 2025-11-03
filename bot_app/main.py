import asyncio
import json
import logging
import queue
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cloudmersive_barcode_api_client
from cloudmersive_barcode_api_client.rest import ApiException
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

import tkinter as tk
from tkinter import simpledialog, messagebox
from tkinter.scrolledtext import ScrolledText


CONFIG_FILE = Path("config.json")
LOG_QUEUE: "queue.Queue[str]" = queue.Queue()


class QueueLogger(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            LOG_QUEUE.put(msg)
        except Exception:  # pragma: no cover - safety net
            self.handleError(record)


def setup_logging() -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    handler = QueueLogger()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


@dataclass
class BotConfig:
    token: str
    api_key: str

    @classmethod
    def load(cls) -> Optional["BotConfig"]:
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                token = data.get("token", "").strip()
                api_key = data.get("api_key", "").strip()
                if token and api_key:
                    logging.info("Конфигурация успешно загружена")
                return cls(token=token, api_key=api_key)
            except json.JSONDecodeError as exc:
                logging.error("Ошибка чтения config.json: %s", exc)
        return None

    def save(self) -> None:
        CONFIG_FILE.write_text(
            json.dumps({"token": self.token, "api_key": self.api_key}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logging.info("Конфигурация сохранена в config.json")


class TokenDialog(simpledialog.Dialog):
    def body(self, master):
        tk.Label(master, text="Введите токен телеграм-бота:").grid(row=0, column=0, padx=10, pady=10)
        self.entry = tk.Entry(master, width=50, show="*")
        self.entry.grid(row=1, column=0, padx=10)
        return self.entry

    def apply(self):
        self.result = self.entry.get().strip()


class ApiKeyDialog(simpledialog.Dialog):
    def body(self, master):
        tk.Label(master, text="Введите ключ Cloudmersive API:").grid(row=0, column=0, padx=10, pady=10)
        self.entry = tk.Entry(master, width=50, show="*")
        self.entry.grid(row=1, column=0, padx=10)
        return self.entry

    def apply(self):
        self.result = self.entry.get().strip()


class BotApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("DataMatrix Telegram бот")
        self.root.geometry("720x480")

        self.status_var = tk.StringVar(value="Инициализация...")
        status_label = tk.Label(root, textvariable=self.status_var, font=("Arial", 12, "bold"))
        status_label.pack(padx=10, pady=(10, 0), anchor="w")

        self.log_widget = ScrolledText(root, state="disabled", wrap="word", font=("Consolas", 10))
        self.log_widget.pack(fill="both", expand=True, padx=10, pady=10)

        self._application: Optional[Application] = None
        self._bot_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._barcode_api: Optional[cloudmersive_barcode_api_client.BarcodeScanApi] = None

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self.poll_log_queue)

        self.config = self.ensure_config()
        if self.config is None or not self.config.token or not self.config.api_key:
            messagebox.showerror(
                "Ошибка",
                "Не заданы данные конфигурации. Работа приложения завершена.",
            )
            self.root.destroy()
            return

        self.update_status("Запуск бота...")
        self.start_bot_thread()

    def ensure_config(self) -> Optional[BotConfig]:
        loaded_config = BotConfig.load()
        token = loaded_config.token if loaded_config else ""
        api_key = loaded_config.api_key if loaded_config else ""

        if token and api_key:
            return loaded_config

        if not token:
            dialog = TokenDialog(self.root)
            token = dialog.result or ""
            if not token:
                return None

        if not api_key:
            dialog = ApiKeyDialog(self.root)
            api_key = dialog.result or ""
            if not api_key:
                return None

        config = BotConfig(token=token, api_key=api_key)
        config.save()
        return config

    def poll_log_queue(self) -> None:
        while True:
            try:
                msg = LOG_QUEUE.get_nowait()
            except queue.Empty:
                break
            else:
                self.append_log(msg)
        self.root.after(200, self.poll_log_queue)

    def append_log(self, message: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", message + "\n")
        self.log_widget.configure(state="disabled")
        self.log_widget.see("end")

    def update_status(self, text: str) -> None:
        self.status_var.set(text)

    def start_bot_thread(self) -> None:
        if self._bot_thread and self._bot_thread.is_alive():
            return

        self._bot_thread = threading.Thread(target=self.run_bot, daemon=True)
        self._bot_thread.start()

    def run_bot(self) -> None:
        assert self.config is not None
        asyncio.run(self._run_async_bot(self.config))

    async def _run_async_bot(self, config: BotConfig) -> None:
        logging.info("Инициализация телеграм-бота")

        try:
            configuration = cloudmersive_barcode_api_client.Configuration()
            configuration.api_key["Apikey"] = config.api_key
            api_client = cloudmersive_barcode_api_client.ApiClient(configuration)
            self._barcode_api = cloudmersive_barcode_api_client.BarcodeScanApi(api_client)
        except Exception as exc:
            logging.exception("Не удалось инициализировать Cloudmersive API клиент: %s", exc)
            self.update_status("Ошибка инициализации Cloudmersive API")
            messagebox.showerror("Ошибка", f"Не удалось инициализировать Cloudmersive API: {exc}")
            return

        application = ApplicationBuilder().token(config.token).concurrent_updates(True).build()

        application.add_handler(CommandHandler("start", self.cmd_start))
        application.add_handler(CommandHandler("help", self.cmd_help))
        application.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, self.handle_image))
        application.add_handler(MessageHandler(filters.COMMAND, self.unknown_command))

        self._application = application
        try:
            self.update_status("Бот запущен и ожидает сообщения")
            await application.initialize()
            await application.start()
            logging.info("Бот подключен. Ожидание обновлений...")
            await application.updater.start_polling()
            while not self._stop_event.is_set():
                await asyncio.sleep(0.5)
        except Exception as exc:  # pragma: no cover - safety net
            logging.exception("Критическая ошибка работы бота: %s", exc)
            self.update_status("Ошибка: %s" % exc)
            messagebox.showerror("Ошибка", f"Произошла ошибка работы бота: {exc}")
        finally:
            await self.shutdown_bot()

    async def shutdown_bot(self) -> None:
        if self._application is None:
            return
        logging.info("Остановка бота")
        try:
            await self._application.updater.stop()
        except Exception:
            pass
        await self._application.stop()
        await self._application.shutdown()
        self.update_status("Бот остановлен")

    def on_close(self) -> None:
        self._stop_event.set()
        if self._application:
            try:
                self._application.create_task(self.shutdown_bot())
            except RuntimeError:
                pass
        self.root.after(500, self.root.destroy)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Отправьте изображение DataMatrix (фото или файл), и я попробую его распознать."
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Просто пришлите изображение с кодом DataMatrix. Если код читаемый, я верну найденный текст."
        )

    async def unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Неизвестная команда. Используйте /start или отправьте изображение.")

    async def handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if message is None:
            return

        file_id = None
        if message.photo:
            file_id = message.photo[-1].file_id
        elif message.document:
            file_id = message.document.file_id

        if not file_id:
            await message.reply_text("Не удалось получить изображение.")
            return

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
            tmp_path = Path(tmp_file.name)

        telegram_file = await context.bot.get_file(file_id)
        await telegram_file.download_to_drive(custom_path=str(tmp_path))
        logging.info("Изображение скачано: %s", tmp_path)

        if self._barcode_api is None:
            await message.reply_text("Сервис распознавания недоступен.")
            logging.error("Cloudmersive API клиент не инициализирован")
            return

        try:
            decoded_text = await asyncio.to_thread(decode_datamatrix, tmp_path, self._barcode_api)
            if decoded_text:
                await message.reply_text(f"Найденный код: {decoded_text}")
                logging.info("Код успешно распознан: %s", decoded_text)
            else:
                await message.reply_text("Не удалось распознать DataMatrix на изображении.")
                logging.warning("DataMatrix не найден на изображении")
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                logging.warning("Не удалось удалить временный файл %s", tmp_path)


def decode_datamatrix(
    image_path: Path, barcode_api: cloudmersive_barcode_api_client.BarcodeScanApi
) -> Optional[str]:
    try:
        with image_path.open("rb") as image_file:
            response = barcode_api.barcode_scan_image(image_file=image_file)
    except ApiException as exc:
        logging.error("Ошибка Cloudmersive API при распознавании: %s", exc)
        return None
    except Exception as exc:
        logging.exception("Не удалось отправить изображение в Cloudmersive API: %s", exc)
        return None

    if response is None:
        logging.warning("Пустой ответ от Cloudmersive API")
        return None

    barcodes = (
        getattr(response, "barcodes", None)
        or getattr(response, "Barcodes", None)
        or getattr(response, "barcode_results", None)
        or getattr(response, "BarcodeResults", None)
    )

    if not barcodes:
        logging.info("Cloudmersive API не вернул распознанные штрихкоды")
        return None

    for barcode in barcodes:
        value = (
            getattr(barcode, "barcode_value", None)
            or getattr(barcode, "BarcodeValue", None)
            or getattr(barcode, "value", None)
        )
        if value:
            return str(value)

    logging.info("Cloudmersive API не предоставил текст распознанного кода")
    return None


def main() -> None:
    setup_logging()
    root = tk.Tk()
    BotApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
