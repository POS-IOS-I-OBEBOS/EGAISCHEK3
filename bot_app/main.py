import asyncio
import json
import logging
import queue
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image
from pylibdmtx.pylibdmtx import decode
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

    @classmethod
    def load(cls) -> Optional["BotConfig"]:
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                token = data.get("token", "").strip()
                if token:
                    logging.info("Конфигурация успешно загружена")
                    return cls(token=token)
            except json.JSONDecodeError as exc:
                logging.error("Ошибка чтения config.json: %s", exc)
        return None

    def save(self) -> None:
        CONFIG_FILE.write_text(json.dumps({"token": self.token}, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info("Токен сохранен в config.json")


class TokenDialog(simpledialog.Dialog):
    def body(self, master):
        tk.Label(master, text="Введите токен телеграм-бота:").grid(row=0, column=0, padx=10, pady=10)
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

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self.poll_log_queue)

        self.config = self.ensure_config()
        if self.config is None:
            messagebox.showerror("Ошибка", "Токен не был задан. Работа приложения завершена.")
            self.root.destroy()
            return

        self.update_status("Запуск бота...")
        self.start_bot_thread()

    def ensure_config(self) -> Optional[BotConfig]:
        config = BotConfig.load()
        if config:
            return config

        dialog = TokenDialog(self.root)
        token = dialog.result
        if not token:
            return None
        config = BotConfig(token=token)
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
        asyncio.run(self._run_async_bot(self.config.token))

    async def _run_async_bot(self, token: str) -> None:
        logging.info("Инициализация телеграм-бота")
        application = ApplicationBuilder().token(token).concurrent_updates(True).build()

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

        try:
            decoded_text = await asyncio.to_thread(decode_datamatrix, tmp_path)
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


def decode_datamatrix(image_path: Path) -> Optional[str]:
    image = Image.open(image_path)
    image = image.convert("RGB")
    results = decode(image)
    if not results:
        return None
    return results[0].data.decode("utf-8", errors="ignore")


def main() -> None:
    setup_logging()
    root = tk.Tk()
    BotApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
