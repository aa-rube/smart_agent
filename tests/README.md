# Payment Module Tests

Комплексные тесты для модуля оплаты, покрывающие все критические сценарии.

## Структура тестов

- `test_payment_webhook.py` - Тесты обработки webhook'ов YooKassa
- `test_notification_idempotency.py` - Тесты идемпотентности уведомлений
- `test_cooldown_check.py` - Тесты проверки кулдауна и trial eligibility
- `test_billing_loop.py` - Тесты логики списаний и guard'ов
- `test_subscription_mark_charged.py` - Тесты обновления подписок после платежа
- `test_duplicate_checks.py` - Тесты проверки дубликатов платежей
- `test_pre_renew_notification.py` - Тесты уведомлений перед списанием

## Запуск тестов

```bash
# Установить зависимости для тестирования
pip install pytest pytest-asyncio pytest-mock

# Запустить все тесты
pytest tests/

# Запустить конкретный файл тестов
pytest tests/test_payment_webhook.py

# Запустить с подробным выводом
pytest tests/ -v

# Запустить с покрытием кода
pytest tests/ --cov=bot/handlers/payment_handler --cov=bot/utils/billing_db --cov=bot/utils/notification
```

## Покрытие тестами

### Webhook обработка
- ✅ Успешный trial платеж
- ✅ Успешный renewal платеж
- ✅ Отменённый платеж
- ✅ Дубликаты webhook'ов
- ✅ Отсутствующий payment_id
- ✅ Renewal без найденной подписки
- ✅ Статус waiting_for_capture

### Идемпотентность уведомлений
- ✅ Уведомление отправляется только один раз для payment_id
- ✅ Уведомление без payment_id всё равно отправляется
- ✅ Обработка ошибок Redis

### Проверка кулдауна
- ✅ Canceled подписка с недавним last_charge_at блокирует trial
- ✅ Старая canceled подписка позволяет trial
- ✅ Активная подписка всегда предлагает полную оплату

### Логика списаний
- ✅ Успешное создание попытки списания
- ✅ Блокировка из-за 12-часового интервала
- ✅ Блокировка из-за 6 неудач подряд
- ✅ Блокировка из-за 2 попыток за 24 часа
- ✅ Фильтрация due подписок
- ✅ Применение guard правил

### Обновление подписок
- ✅ Обновление по subscription_id
- ✅ Обновление по plan_code
- ✅ Подписка не найдена
- ✅ Fallback при неактивной подписке

### Проверка дубликатов
- ✅ Дубликат с payment_id
- ✅ Недавняя попытка без payment_id
- ✅ Старая попытка не блокирует

### Pre-renewal уведомления
- ✅ Пропуск при недавнем last_charge_at
- ✅ Отправка при старом last_charge_at
- ✅ Обработка None last_charge_at

## Примечания

- Все тесты используют моки для внешних зависимостей (БД, Redis, Telegram Bot, YooKassa)
- Тесты не требуют реальных подключений к БД или внешним сервисам
- Для запуска тестов нужен только pytest и зависимости из requirements.txt

