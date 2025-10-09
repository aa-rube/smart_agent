#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\utils\logging_config.py
import logging
import os

# Функция для получения домашней директории (может использоваться глобально):
def get_home_directory():
    return os.path.expanduser("~")


# Настройка логов
LOG_PATH = os.path.join(get_home_directory(), "logs", "test_smart_agent.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

# Базовая конфигурация: логи записываются в файл
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    filemode='a'
)

# Получаем корневой логгер
logger = logging.getLogger()

# Создаём обработчик для вывода в консоль
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)  # или DEBUG, если нужно более подробное логирование
console_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
console_handler.setFormatter(console_formatter)

# Добавляем консольный обработчик к логгеру
logger.addHandler(console_handler)
